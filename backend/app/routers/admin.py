from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from qdrant_client.models import Distance, VectorParams

from app.core.config import settings
from app.core.errors import AppError
from app.db.qdrant import get_qdrant_client
from app.services.monitoring.metrics import get_latencies, get_token_usage

router = APIRouter(prefix="/admin", tags=["admin"])

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


@router.get("/collections", response_model=list[CollectionResponse])
def list_collections():
    client = get_qdrant_client()
    names = [c.name for c in client.get_collections().collections]
    return [_to_response(client, name) for name in names]


@router.post("/collections", response_model=CollectionResponse, status_code=201)
def create_collection(body: CollectionCreateRequest):
    client = get_qdrant_client()
    if client.collection_exists(body.name):
        raise AppError(409, "collection_already_exists", f"Collection '{body.name}' already exists")
    client.create_collection(
        collection_name=body.name,
        vectors_config=VectorParams(size=body.vector_size, distance=_DISTANCE_MAP[body.distance]),
    )
    return _to_response(client, body.name)


@router.delete("/collections/{name}", status_code=204)
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


@router.get("/metrics", response_model=MetricsResponse)
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
