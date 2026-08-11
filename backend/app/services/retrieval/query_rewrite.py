"""Phase 3B — query rewriting (docs/RAG_RETRIEVAL.md). An optional,
feature-flagged retrieval aid: rewrites an ambiguous/conversational query
into a more retrieval-effective form using the existing Claude Gateway
(never a second Anthropic client) and recent conversation context.

Security: the rewritten query is used ONLY for the retrieval call in
services/agents/planner.py's search_documents tool — it never replaces the
user's actual question in the conversation Claude answers from. This module
has no access to (and therefore cannot alter) any RBAC/permission/department
parameter; those are threaded into search_with_reranking()/hybrid_search()
completely independently, both before and after rewriting. rewrite_query()'s
signature is deliberately (query, context) -> outcome — it structurally
cannot see or touch a role, department, or permission value.

Reliability: every failure mode (gateway error, timeout, refusal, malformed
JSON, empty output, over-length output) falls back to the original query,
unchanged. Callers should always use RewriteOutcome.query and never need to
special-case failure themselves — a rewrite failure must never fail the
user's actual request.
"""

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass

from app.core.config import settings
from app.gateway import usage_tracker
from app.gateway.claude_gateway import GenerationError, claude_gateway
from app.gateway.prompt_manager import load_prompt
from app.gateway.schemas import GenerateRequest, ModelTier

REWRITE_PROMPT = load_prompt("query_rewrite_agent", "v1")
REWRITE_SYSTEM_PROMPT = REWRITE_PROMPT.text

# A small, reused worker pool — not one ThreadPoolExecutor per call — so a
# timed-out rewrite's abandoned call finishes in the background without the
# caller waiting for it (an executor's own shutdown() would otherwise block
# until the submitted work completes, defeating the timeout entirely). The
# underlying Anthropic HTTP call itself isn't preemptible; this bounds how
# long the *caller* waits, not the network call's actual lifetime.
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="query-rewrite")


@dataclass
class RewriteOutcome:
    query: str  # what to actually use for retrieval — rewritten on success, original on any fallback
    rewritten: bool
    original_query: str
    rewritten_query: str | None  # what the model returned, even if not used — for observability
    latency_ms: float
    tokens_input: int
    tokens_output: int
    cost_usd: float
    fallback_reason: str | None  # None iff rewritten is True


def _resolve_tier() -> ModelTier:
    try:
        return ModelTier(settings.query_rewrite_tier)
    except ValueError:
        return ModelTier.FAST


def _fallback(query: str, start: float, *, reason: str, tokens_input=0, tokens_output=0, cost_usd=0.0) -> RewriteOutcome:
    return RewriteOutcome(
        query=query, rewritten=False, original_query=query, rewritten_query=None,
        latency_ms=(time.perf_counter() - start) * 1000, tokens_input=tokens_input,
        tokens_output=tokens_output, cost_usd=cost_usd, fallback_reason=reason,
    )


def rewrite_query(
    query: str, *, context: str | None = None, request_id: str | None = None,
    user_id: uuid.UUID | None = None, role: str | None = None, department: str | None = None,
) -> RewriteOutcome:
    start = time.perf_counter()
    user_message = (
        f"Conversation context:\n{context}\n\nUser's latest message: {query}" if context
        else f"User's latest message: {query}"
    )
    request = GenerateRequest(
        agent_name="query_rewrite",
        system=REWRITE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
        tier=_resolve_tier(),
        max_tokens=200,
        request_id=request_id,
        cache_system=True,
        user_id=user_id,
        role=role,
        department=department,
    )

    try:
        future = _executor.submit(claude_gateway.generate, request)
        result = future.result(timeout=settings.query_rewrite_timeout_seconds)
    except FutureTimeoutError:
        return _fallback(query, start, reason=f"timed out after {settings.query_rewrite_timeout_seconds}s")
    except GenerationError as exc:
        return _fallback(query, start, reason=f"gateway error: {exc}")
    except Exception as exc:  # belt-and-suspenders — a rewrite failure must never fail the actual request
        return _fallback(query, start, reason=f"unexpected error: {exc}")

    latency_ms = (time.perf_counter() - start) * 1000
    tokens_input, tokens_output = result.usage.input_tokens, result.usage.output_tokens
    cost_usd = usage_tracker.estimate_cost_usd(result.model, result.usage)
    common = dict(tokens_input=tokens_input, tokens_output=tokens_output, cost_usd=cost_usd)

    if result.stop_reason == "refusal":
        return _fallback(query, start, reason="model refused", **common)

    text = result.text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
        rewritten = str(data.get("rewritten_query", "")).strip()
    except (json.JSONDecodeError, ValueError):
        return _fallback(query, start, reason="malformed JSON response", **common)

    if not rewritten:
        return _fallback(query, start, reason="empty rewrite", **common)

    if len(rewritten) > settings.query_rewrite_max_chars:
        return RewriteOutcome(
            query=query, rewritten=False, original_query=query, rewritten_query=rewritten,
            latency_ms=latency_ms, fallback_reason=f"rewrite exceeded {settings.query_rewrite_max_chars} chars",
            **common,
        )

    return RewriteOutcome(
        query=rewritten, rewritten=True, original_query=query, rewritten_query=rewritten,
        latency_ms=latency_ms, fallback_reason=None, **common,
    )
