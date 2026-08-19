import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from qdrant_client.models import Distance, VectorParams
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.permissions import Permission
from app.db.postgres import get_db
from app.db.qdrant import get_qdrant_client
from app.gateway import availability
from app.models.gateway_usage_log import GatewayUsageLogModel
from app.models.user import UserModel
from app.services.auth.dependencies import get_current_user
from app.services.auth.rbac import require_permission
from app.services.ingestion.consistency import check_all_documents
from app.services.llm_rbac import policy_loader
from app.services.monitoring.metrics import (
    get_guardrail_events, get_ingestion_metrics, get_latencies, get_retrieval_errors, get_retrieval_metrics,
    get_token_usage,
)

# Was a blanket require_role(ADMIN) for every route in this file. Split per
# the enterprise permission matrix: the Qdrant-collections/model-availability
# routes are real system configuration (SYSTEM_SETTINGS, Admin-only); the
# operational-metrics routes (latency/tokens/gateway-cost/guardrails) are
# platform-ops analytics (VIEW_ANALYTICS, CEO+Admin — see individual route
# dependencies below, since the two groups now differ).
router = APIRouter(prefix="/admin", tags=["admin"])
_settings_only = [Depends(require_permission(Permission.SYSTEM_SETTINGS))]
_analytics = [Depends(require_permission(Permission.VIEW_ANALYTICS))]

_DISTANCE_MAP = {"Cosine": Distance.COSINE, "Euclid": Distance.EUCLID, "Dot": Distance.DOT}


class CollectionResponse(BaseModel):
    name: str
    points_count: int | None
    status: str
    is_primary: bool


class CollectionCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    vector_size: int = settings.embedding_dimension
    distance: Literal["Cosine", "Euclid", "Dot"] = "Cosine"


def _to_response(client, name: str) -> CollectionResponse:
    info = client.get_collection(name)
    return CollectionResponse(
        name=name,
        points_count=info.points_count,
        status=str(info.status),
        is_primary=(name == settings.qdrant_collection_name),
    )


@router.get("/collections", response_model=list[CollectionResponse], dependencies=_settings_only)
def list_collections():
    client = get_qdrant_client()
    names = [c.name for c in client.get_collections().collections]
    return [_to_response(client, name) for name in names]


@router.post("/collections", response_model=CollectionResponse, status_code=201, dependencies=_settings_only)
def create_collection(body: CollectionCreateRequest):
    client = get_qdrant_client()
    if client.collection_exists(body.name):
        raise AppError(409, "collection_already_exists", f"Collection '{body.name}' already exists")
    client.create_collection(
        collection_name=body.name,
        vectors_config=VectorParams(size=body.vector_size, distance=_DISTANCE_MAP[body.distance]),
    )
    return _to_response(client, body.name)


@router.delete("/collections/{name}", status_code=204, dependencies=_settings_only)
def delete_collection(name: str):
    if name == settings.qdrant_collection_name:
        raise AppError(
            400, "cannot_delete_primary_collection",
            f"'{name}' is the active document collection and can't be deleted from here",
        )
    client = get_qdrant_client()
    if not client.collection_exists(name):
        raise AppError(404, "collection_not_found", f"Collection '{name}' not found")
    client.delete_collection(collection_name=name)


class IndexConsistencyItem(BaseModel):
    document_id: str
    filename: str
    postgres_chunk_count: int
    qdrant_point_count: int


class IndexConsistencyResponse(BaseModel):
    checked: int
    inconsistent: list[IndexConsistencyItem]


@router.get("/index-consistency", response_model=IndexConsistencyResponse, dependencies=_settings_only)
def get_index_consistency(db: Session = Depends(get_db)):
    """Flags any document whose Postgres chunk count doesn't match its
    actual Qdrant point count — e.g. status="completed" but zero points,
    which the ingestion path itself has no way to detect after the fact
    (see services/ingestion/consistency.py's module docstring for why: it's
    not a code bug in the upload/reindex flow, it's drift between two
    independently-lifecycled stores). Fix a flagged document via the
    existing POST /documents/{id}/reindex — never by writing to Qdrant
    directly from here."""
    reports = check_all_documents(db)
    inconsistent = [r for r in reports if not r.consistent]
    return IndexConsistencyResponse(
        checked=len(reports),
        inconsistent=[
            IndexConsistencyItem(
                document_id=str(r.document_id), filename=r.filename,
                postgres_chunk_count=r.postgres_chunk_count, qdrant_point_count=r.qdrant_point_count,
            )
            for r in inconsistent
        ],
    )


class ModelAvailabilityResponse(BaseModel):
    disabled: bool


@router.get("/model-availability", response_model=ModelAvailabilityResponse, dependencies=_settings_only)
def get_model_availability():
    return ModelAvailabilityResponse(disabled=availability.is_disabled())


@router.put("/model-availability", response_model=ModelAvailabilityResponse, dependencies=_settings_only)
def set_model_availability(body: ModelAvailabilityResponse):
    """Admin-only testing toggle (gateway/availability.py) — forces every
    Claude call to fail with GenerationError so routers/chat.py's degraded
    retrieval-fallback path, and the chat UI's "try a different model" retry
    button, can be demonstrated on demand without touching the real
    ANTHROPIC_API_KEY. Process-local: resets on backend restart, not
    persisted, not shared across multiple workers/instances."""
    availability.set_disabled(body.disabled)
    return ModelAvailabilityResponse(disabled=availability.is_disabled())


class LatencySummary(BaseModel):
    endpoint: str
    count: int
    avg_ms: float
    p95_ms: float


class TokenUsageSummary(BaseModel):
    source: str
    model: str
    total_input_tokens: int
    total_output_tokens: int
    call_count: int


class MetricsResponse(BaseModel):
    latency_samples: list[dict]
    latency_summary: list[LatencySummary]
    token_usage_samples: list[dict]
    token_usage_summary: list[TokenUsageSummary]


@router.get("/metrics", response_model=MetricsResponse, dependencies=_analytics)
def get_metrics():
    latencies = get_latencies()
    tokens = get_token_usage()

    by_endpoint: dict[str, list[float]] = {}
    for entry in latencies:
        by_endpoint.setdefault(entry["endpoint"], []).append(entry["duration_ms"])
    latency_summary = []
    for endpoint, values in by_endpoint.items():
        ordered = sorted(values)
        p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)]
        latency_summary.append(
            LatencySummary(endpoint=endpoint, count=len(values), avg_ms=sum(values) / len(values), p95_ms=p95)
        )
    latency_summary.sort(key=lambda s: s.count, reverse=True)

    by_source_model: dict[tuple[str, str], dict] = {}
    for entry in tokens:
        key = (entry["source"], entry["model"])
        agg = by_source_model.setdefault(key, {"input": 0, "output": 0, "count": 0})
        agg["input"] += entry["input_tokens"]
        agg["output"] += entry["output_tokens"]
        agg["count"] += 1
    token_usage_summary = [
        TokenUsageSummary(
            source=source, model=model,
            total_input_tokens=agg["input"], total_output_tokens=agg["output"], call_count=agg["count"],
        )
        for (source, model), agg in by_source_model.items()
    ]

    return MetricsResponse(
        latency_samples=latencies[-200:],
        latency_summary=latency_summary,
        token_usage_samples=tokens[-200:],
        token_usage_summary=token_usage_summary,
    )


class QueryMetricsResponse(BaseModel):
    retrieval_samples: list[dict]
    ingestion_samples: list[dict]
    retrieval_error_samples: list[dict]


@router.get("/query-metrics", response_model=QueryMetricsResponse, dependencies=_analytics)
def get_query_metrics():
    """Per-query stage-timing breakdowns for the metrics dashboard — raw
    samples only (retrieval: filter/embed/sparse/qdrant/rerank/total per
    search call; ingestion: parse/summarize/entity/chunk/tokenize/
    embed/sparse/sparse_index/total per upload; retrieval_error_samples:
    infra failures — Qdrant/Postgres unavailable, reranker crash — caught
    along the retrieval path). Aggregation, trend charts, and suggestions are
    computed frontend-side from these."""
    return QueryMetricsResponse(
        retrieval_samples=get_retrieval_metrics()[-200:],
        ingestion_samples=get_ingestion_metrics()[-200:],
        retrieval_error_samples=get_retrieval_errors()[-200:],
    )


class GatewayUsageSample(BaseModel):
    id: uuid.UUID
    # NOT unique per row — one outer user request can make several Claude
    # Gateway calls (planner turn, tool calls, judge, ...) that all share
    # the same request_id for correlation. `id` above is this row's own
    # primary key and the only field here safe to use as a unique React key.
    request_id: str
    agent_name: str
    model: str
    tier: str
    tokens_input: int
    tokens_output: int
    latency_ms: float
    cost_usd: float
    # LLM-RBAC audit fields (docs/AUDIT_LOGGING.md) — this table is the one
    # durable "who did what, was it allowed" record the spec asks for (see
    # GatewayUsageLogModel's own docstring), previously written but never
    # read back through any endpoint. user_email is resolved server-side
    # (batched, same convention as routers/approvals.py::_resolve_emails)
    # so the frontend never needs a second lookup for a bare user_id.
    user_id: uuid.UUID | None
    user_email: str | None
    role: str | None
    department: str | None
    decision: str
    denial_reason: str | None
    requested_capability: str | None
    tool_calls: list[str] | None
    documents_retrieved: list[str] | None
    created_at: datetime


class GatewayUsageSummaryRow(BaseModel):
    agent_name: str
    model: str
    tier: str
    call_count: int
    total_tokens_input: int
    total_tokens_output: int
    total_cost_usd: float
    avg_latency_ms: float


class GatewayUsageResponse(BaseModel):
    samples: list[GatewayUsageSample]
    summary: list[GatewayUsageSummaryRow]
    total_cost_usd: float
    # Counted over the FULL table (not just the returned sample window),
    # same convention as total_cost_usd below — lets the UI show "how many
    # requests were denied" without requiring every denial to fit in `limit`.
    denied_count: int


@router.get("/gateway-usage", response_model=GatewayUsageResponse, dependencies=_analytics)
def get_gateway_usage(limit: int = 200, decision: str | None = None, db: Session = Depends(get_db)):
    """Claude Gateway call history: which agent called which model/tier, at
    what cost — and, per GatewayUsageLogModel's own docstring, the durable
    LLM-RBAC audit trail of who made each request, under what role/
    department, whether it was allowed, and why not when it wasn't.
    `decision` optionally filters to "allowed" or "denied" only. Summary is
    aggregated over the FULL table (not just the returned sample window)
    since gateway_usage_logs is a real, unbounded Postgres table, unlike the
    in-memory metrics below."""
    query = db.query(GatewayUsageLogModel)
    if decision:
        query = query.filter(GatewayUsageLogModel.decision == decision)
    rows = query.order_by(GatewayUsageLogModel.created_at.desc()).limit(limit).all()

    user_ids = {r.user_id for r in rows if r.user_id is not None}
    emails = {}
    if user_ids:
        emails = {
            row.id: row.email
            for row in db.query(UserModel.id, UserModel.email).filter(UserModel.id.in_(user_ids)).all()
        }

    samples = [
        GatewayUsageSample(
            id=r.id, request_id=r.request_id, agent_name=r.agent_name, model=r.model, tier=r.tier,
            tokens_input=r.tokens_input, tokens_output=r.tokens_output,
            latency_ms=r.latency_ms, cost_usd=r.cost_usd,
            user_id=r.user_id, user_email=emails.get(r.user_id), role=r.role, department=r.department,
            decision=r.decision, denial_reason=r.denial_reason, requested_capability=r.requested_capability,
            tool_calls=r.tool_calls, documents_retrieved=r.documents_retrieved,
            created_at=r.created_at,
        )
        for r in rows
    ]

    summary_rows = (
        db.query(
            GatewayUsageLogModel.agent_name,
            GatewayUsageLogModel.model,
            GatewayUsageLogModel.tier,
            func.count(GatewayUsageLogModel.id),
            func.sum(GatewayUsageLogModel.tokens_input),
            func.sum(GatewayUsageLogModel.tokens_output),
            func.sum(GatewayUsageLogModel.cost_usd),
            func.avg(GatewayUsageLogModel.latency_ms),
        )
        .group_by(GatewayUsageLogModel.agent_name, GatewayUsageLogModel.model, GatewayUsageLogModel.tier)
        .all()
    )
    summary = [
        GatewayUsageSummaryRow(
            agent_name=agent_name, model=model, tier=tier, call_count=call_count,
            total_tokens_input=total_in or 0, total_tokens_output=total_out or 0,
            total_cost_usd=total_cost or 0.0, avg_latency_ms=avg_latency or 0.0,
        )
        for agent_name, model, tier, call_count, total_in, total_out, total_cost, avg_latency in summary_rows
    ]
    summary.sort(key=lambda s: s.total_cost_usd, reverse=True)

    total_cost = db.query(func.sum(GatewayUsageLogModel.cost_usd)).scalar() or 0.0
    denied_count = db.query(func.count(GatewayUsageLogModel.id)).filter(GatewayUsageLogModel.decision == "denied").scalar() or 0
    return GatewayUsageResponse(samples=samples, summary=summary, total_cost_usd=total_cost, denied_count=denied_count)


class GuardrailEventSample(BaseModel):
    direction: str
    check: str
    action: str
    detail: str
    created_at: float


class GuardrailCheckSummary(BaseModel):
    direction: str
    check: str
    pass_count: int
    redact_count: int
    block_count: int


class GuardrailAnalyticsResponse(BaseModel):
    events: list[GuardrailEventSample]
    summary: list[GuardrailCheckSummary]


# Placeholder swapped in for a guardrail event's raw detail when the caller
# isn't cleared to see it (below). Deliberately says WHY the field is empty
# rather than sending "" — a blank cell reads as "no detail was recorded,"
# which would be false.
_REDACTED_GUARDRAIL_DETAIL = "Details restricted"


@router.get("/guardrail-analytics", response_model=GuardrailAnalyticsResponse, dependencies=_analytics)
def get_guardrail_analytics(current_user: UserModel = Depends(get_current_user)):
    """Pass/redact/block counts per check (input: length/injection/destructive/
    scope/pii, output: system_prompt_leak/pii/output_citation_check), plus the
    raw recent event log — same in-memory store every guardrail step already
    writes to (services/monitoring/metrics.py::record_guardrail_event), just
    not previously exposed through any endpoint.

    VIEW_ANALYTICS is now granted to every role (config/llm_rbac.yaml), so
    this endpoint is reachable by ordinary Employees — but a step's raw
    `detail` embeds classifier internals that the chat UI deliberately hides
    from non-privileged users (semantic_check.py's "best score=0.52" and its
    matched unsafe-example phrase, deberta_injection_check.py's "score=1.00",
    scope.py's literal configured deny-keyword). Those are recorded here
    verbatim by pipeline.py::_record(), so the same VIEW_AUDIT_LOGS line that
    gates org-wide trace visibility (routers/traces.py) gates the raw detail
    here — everyone still sees every event's direction/check/action and all
    the pass/redact/block counts, which is what the dashboard is actually for.
    """
    granted = policy_loader.role_config(current_user.role).granted_permissions
    may_see_raw_detail = Permission.VIEW_AUDIT_LOGS.value in granted or "*" in granted

    events = get_guardrail_events()
    if not may_see_raw_detail:
        events = [{**e, "detail": _REDACTED_GUARDRAIL_DETAIL} for e in events]

    by_check: dict[tuple[str, str], dict[str, int]] = {}
    for e in events:
        agg = by_check.setdefault((e["direction"], e["check"]), {"pass": 0, "redact": 0, "block": 0})
        if e["action"] in agg:
            agg[e["action"]] += 1
    summary = [
        GuardrailCheckSummary(
            direction=direction, check=check,
            pass_count=counts["pass"], redact_count=counts["redact"], block_count=counts["block"],
        )
        for (direction, check), counts in sorted(by_check.items())
    ]

    return GuardrailAnalyticsResponse(events=events[-200:], summary=summary)


class RoleSummary(BaseModel):
    role: str
    display_name: str
    department_default: str | None
    tiers_allowed: list[str]
    knowledge_departments: list[str]
    tools: list[str]
    granted_permissions: list[str]
    all_permissions: bool
    quotas: dict


class RolesResponse(BaseModel):
    roles: list[RoleSummary]


@router.get("/roles", response_model=RolesResponse, dependencies=[Depends(require_permission(Permission.VIEW_ROLES))])
def list_roles():
    """Read-only summary of every role's permission/tool/quota configuration
    (backend/config/llm_rbac.yaml, via policy_loader) — the Roles &
    Permissions admin view. Live editing of role definitions is a materially
    bigger feature (a dynamic config store instead of a static YAML file, an
    admin UI for authoring RBAC changes safely) and is out of scope here;
    MANAGE_ROLES exists in the permission catalog for when that's built, but
    nothing calls it yet."""
    summaries = []
    for role in policy_loader.all_roles():
        cfg = policy_loader.role_config(role)
        summaries.append(RoleSummary(
            role=cfg.role,
            display_name=cfg.display_name,
            department_default=cfg.department_default,
            tiers_allowed=sorted(cfg.tiers_allowed),
            knowledge_departments=list(cfg.knowledge_departments),
            tools=sorted(cfg.tools),
            granted_permissions=(
                sorted(p.value for p in Permission) if "*" in cfg.granted_permissions else sorted(cfg.granted_permissions)
            ),
            all_permissions="*" in cfg.granted_permissions,
            quotas=cfg.quotas,
        ))
    return RolesResponse(roles=summaries)
