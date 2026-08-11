import uuid
from dataclasses import dataclass, field
from enum import StrEnum


class ModelTier(StrEnum):
    """Which class of work a gateway call is doing — resolved to an actual
    model name by model_router.py, per backend/config/models.yaml.

    FAST/REASONING are the original, still-used-as-is tiers for callers not
    driven by an end-user role (generation_judge.py, memory/store.py).
    HAIKU/SONNET/OPUS are the LLM-RBAC tiers services/llm_rbac/engine.py
    resolves a role to (backend/config/llm_rbac.yaml's per-role
    tiers_allowed) — the role-based model-access policy. SONNET/OPUS share
    underlying models with FAST/REASONING today, kept as distinct config
    keys so the two vocabularies (capability tier vs. role-driven model
    choice) can diverge later without the two accidentally sharing a
    codepath that assumes they never will.
    """

    FAST = "fast"
    REASONING = "reasoning"
    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"


class GenerationErrorReason(StrEnum):
    """Why a GenerationError was raised — carried on the exception (see
    claude_gateway.GenerationError) so callers up the stack
    (services/agents/planner.py's run_retrieval_fallback, routers/chat.py's
    ChatResponse.degraded_reason, the chat UI) can show an accurate,
    user-safe message instead of collapsing every failure into one generic
    "no AI model configured" sentence regardless of what actually happened.
    Values are stable strings — they cross the HTTP boundary into
    ChatResponse.degraded_reason, so never rename one without updating the
    frontend's message map (frontend/app/views/chat.py)."""

    NO_API_KEY = "no_api_key"  # ANTHROPIC_API_KEY unset
    MODEL_DISABLED = "model_disabled"  # admin kill switch (gateway/availability.py)
    AUTH_FAILED = "auth_failed"  # Anthropic rejected the key (401)
    PROVIDER_UNAVAILABLE = "provider_unavailable"  # rate limited / overloaded / connection / timeout — transient
    PROVIDER_ERROR = "provider_error"  # any other Anthropic API error (bad request, not found, ...)
    CAPACITY = "capacity"  # this process's own concurrency cap (claude_gateway.capacity_guard)
    INTERNAL = "internal"  # anything else — a bug elsewhere in the agent loop, not actually about the model


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class GenerateRequest:
    agent_name: str
    system: str
    messages: list[dict]
    tier: ModelTier = ModelTier.FAST
    max_tokens: int | None = None
    effort: str | None = None
    cache_system: bool = False
    request_id: str | None = None
    # Attribution for calls made on behalf of a specific end-user request
    # (e.g. query rewriting, conversation summarization) so their usage
    # counts toward that user's LLM-RBAC budget and shows up attributed in
    # gateway_usage_logs, instead of as an anonymous system call. Left None
    # by callers that genuinely aren't user-driven (eval judge).
    user_id: uuid.UUID | None = None
    role: str | None = None
    department: str | None = None


@dataclass
class GenerateResult:
    text: str
    stop_reason: str | None
    usage: TokenUsage
    request_id: str
    model: str
    latency_ms: float


@dataclass
class StreamChunk:
    text: str
    done: bool = False
    usage: TokenUsage | None = None  # populated on the final (done=True) chunk


@dataclass
class ModelTierConfig:
    model: str
    max_tokens: int
    effort: str
    # Not every Claude model accepts thinking={"type": "adaptive"} or
    # output_config={"effort": ...} — verified live: claude-haiku-4-5-20251001
    # rejects BOTH, each with its own 400 invalid_request_error ("adaptive
    # thinking is not supported on this model" / "This model does not support
    # the effort parameter"), while claude-sonnet-5/claude-opus-5 accept both
    # fine — they're bundled extended-reasoning controls this model tier
    # simply doesn't have. Defaults True (today's existing behavior for every
    # tier that isn't haiku) so this is opt-out, not opt-in, per
    # backend/config/models.yaml's per-tier supports_extended_reasoning key —
    # see gateway/claude_gateway.py's generate()/get_langchain_model()/
    # stream() for where this is actually consulted.
    supports_extended_reasoning: bool = True


@dataclass
class RetryPolicy:
    max_retries: int
    base_delay_seconds: float
    max_delay_seconds: float
    retryable_errors: list[str] = field(default_factory=list)
