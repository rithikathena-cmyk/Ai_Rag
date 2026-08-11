from dataclasses import dataclass

from app.gateway.schemas import ModelTier


@dataclass(frozen=True)
class PolicyDecision:
    """Returned by services/llm_rbac/engine.py::authorize_llm_request() on
    success — that function raises AppError (403 permission, 429 quota/rate-
    limit) on any denial rather than returning allowed=False, so every field
    here describes an *allowed* request. Callers use this to pick the model
    tier / tool set / knowledge-department filter for the rest of the
    request; nothing downstream should re-derive these from the user's role
    directly, so there is exactly one place (engine.py) that interprets
    llm_rbac.yaml.
    """

    allowed: bool
    role: str
    department: str | None
    model_tier: ModelTier
    allowed_tools: frozenset[str]
    sql_allowed_tables: frozenset[str] | None
    knowledge_departments: tuple[str, ...] | None  # None = no category restriction (RBAC disabled)
    max_concurrent_requests: int | None = None  # None = unlimited
    requires_approval: bool = False
