import contextlib
import logging
import threading
import time
import uuid
from typing import Generator

import anthropic
from langchain_anthropic import ChatAnthropic

from app.core.config import settings
from app.core.yaml_config import load_yaml_config
from app.gateway import model_router, retry_handler, usage_tracker
from app.gateway.schemas import (
    GenerateRequest,
    GenerateResult,
    GenerationErrorReason,
    ModelTier,
    StreamChunk,
    TokenUsage,
)
from app.gateway.streaming import stream_anthropic_response

logger = logging.getLogger(__name__)


class GenerationError(Exception):
    """Raised for any unrecoverable failure to get a Claude response —
    missing API key, or an Anthropic API error that retry_handler gave up
    on. Callers that already special-cased this (planner.py's fallback to
    non-LLM retrieval, chat.py's degraded-response path) keep working
    unchanged since this is the same exception they imported from
    services/generation/client.py before the gateway migration.

    Carries a coarse, safe-to-expose `reason` (GenerationErrorReason) so
    callers can distinguish "no API key configured" from "provider auth
    failed" from "provider is temporarily overloaded" from "some unrelated
    bug in the agent loop" — instead of every one of those collapsing into
    the same "no AI model configured" message regardless of what actually
    happened. Defaults to INTERNAL so any call site that doesn't pass a
    reason still gets a safe (if generic) classification rather than a
    misleading "not configured" claim."""

    def __init__(self, message: str, reason: GenerationErrorReason = GenerationErrorReason.INTERNAL):
        super().__init__(message)
        self.reason = reason


def classify_anthropic_error(exc: anthropic.APIError) -> GenerationErrorReason:
    """Maps a raw Anthropic SDK exception to the coarse, safe category
    surfaced to end users (never the raw exception text/status — that stays
    in the server logs via logger.warning in retry_handler.py and wherever
    this GenerationError is ultimately logged). AuthenticationError means
    the configured key itself is bad/revoked; RateLimitError/
    APIConnectionError/APITimeoutError/InternalServerError (which also
    covers the 529 "overloaded" response — the Anthropic SDK maps every
    >=500 status to InternalServerError) are transient provider-side
    conditions the caller should just retry later; everything else (bad
    request, not found, permission denied, ...) is a provider-side request
    problem that a retry won't fix on its own."""
    if isinstance(exc, anthropic.AuthenticationError):
        return GenerationErrorReason.AUTH_FAILED
    if isinstance(
        exc,
        (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APITimeoutError, anthropic.InternalServerError),
    ):
        return GenerationErrorReason.PROVIDER_UNAVAILABLE
    return GenerationErrorReason.PROVIDER_ERROR


def _request_timeout_seconds() -> float:
    return float(load_yaml_config("llm.yaml").get("request_timeout_seconds", 60.0))


def _prompt_cache_enabled() -> bool:
    return bool(load_yaml_config("llm.yaml").get("cache", {}).get("prompt_cache_enabled", True))


def _max_in_flight_requests() -> int:
    return int(load_yaml_config("llm.yaml").get("concurrency", {}).get("max_in_flight_requests", 20))


class ClaudeGateway:
    """The one place agents/services get a Claude response from. Owns auth
    (the singleton anthropic client), model routing (model_router.py),
    retries (retry_handler.py), prompt caching, and usage tracking
    (usage_tracker.py) — every caller gets all of that for free instead of
    re-implementing it per call site, which is what
    services/generation/client.py, services/evaluation/generation_judge.py,
    and services/memory/store.py each did independently before this
    migration.
    """

    def __init__(self):
        self._client: anthropic.Anthropic | None = None
        self._semaphore: threading.BoundedSemaphore | None = None
        self._semaphore_lock = threading.Lock()

    def _get_client(self) -> anthropic.Anthropic:
        if self._client is None:
            if not settings.anthropic_api_key:
                raise GenerationError("ANTHROPIC_API_KEY is not configured", reason=GenerationErrorReason.NO_API_KEY)
            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=_request_timeout_seconds())
        return self._client

    def _get_semaphore(self) -> threading.BoundedSemaphore:
        if self._semaphore is None:
            with self._semaphore_lock:
                if self._semaphore is None:
                    self._semaphore = threading.BoundedSemaphore(_max_in_flight_requests())
        return self._semaphore

    @contextlib.contextmanager
    def capacity_guard(self):
        """Bounds total concurrent Claude calls process-wide
        (llm.yaml's concurrency.max_in_flight_requests) — on top of
        llm_rbac's per-user ConcurrencyGuard, which doesn't protect against
        a burst across many users exhausting the request thread pool or
        tripping Anthropic's account-level rate limits. Non-blocking: at
        capacity, fails fast (GenerationError) rather than queuing, so
        callers hit the same degraded-response path as any other gateway
        failure (routers/chat.py's retrieval fallback) instead of piling up
        waiting threads. Exposed publicly (not just used internally by
        generate()/stream()) so services/agents/planner.py's LangChain
        `.invoke()` path — which bypasses generate() entirely — can share
        the same cap."""
        semaphore = self._get_semaphore()
        if not semaphore.acquire(blocking=False):
            raise GenerationError(
                "Claude Gateway is at capacity — too many concurrent requests in flight",
                reason=GenerationErrorReason.CAPACITY,
            )
        try:
            yield
        finally:
            semaphore.release()

    def _system_block(self, system: str, cache_system: bool):
        if not system or not cache_system or not _prompt_cache_enabled():
            return system
        # Anthropic prompt caching requires `system` as a content-block list
        # (not a plain string) to attach cache_control — this is the only
        # shape difference caching introduces.
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

    def get_langchain_model(self, tier: ModelTier = ModelTier.FAST, max_tokens: int | None = None) -> ChatAnthropic:
        """Returns a LangChain-compatible model for callers that need
        .bind_tools()/ToolNode (i.e. the LangGraph planner loop) instead of a
        single generate() call. Routed through the same model_router as
        generate(), so tier selection stays consistent across both paths."""
        if not settings.anthropic_api_key:
            raise GenerationError("ANTHROPIC_API_KEY is not configured", reason=GenerationErrorReason.NO_API_KEY)
        tier_config = model_router.resolve(tier)
        kwargs: dict = dict(
            model=tier_config.model,
            max_tokens=max_tokens or tier_config.max_tokens,
            api_key=settings.anthropic_api_key,
            timeout=_request_timeout_seconds(),
        )
        if tier_config.supports_extended_reasoning:
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"] = {"effort": tier_config.effort}
        return ChatAnthropic(**kwargs)

    def generate(self, request: GenerateRequest) -> GenerateResult:
        request_id = request.request_id or str(uuid.uuid4())

        tier_config = model_router.resolve(request.tier)
        system_block = self._system_block(request.system, request.cache_system)

        def _call():
            kwargs: dict = dict(
                model=tier_config.model,
                max_tokens=request.max_tokens or tier_config.max_tokens,
                system=system_block,
                messages=request.messages,
            )
            if tier_config.supports_extended_reasoning:
                kwargs["thinking"] = {"type": "adaptive"}
                kwargs["output_config"] = {"effort": request.effort or tier_config.effort}
            return self._get_client().messages.create(**kwargs)

        start = time.perf_counter()
        try:
            with self.capacity_guard():
                response = retry_handler.call_with_retry(_call, agent_name=request.agent_name)
        except anthropic.APIError as exc:
            raise GenerationError(str(exc), reason=classify_anthropic_error(exc)) from exc
        latency_ms = (time.perf_counter() - start) * 1000

        text = (
            "" if response.stop_reason == "refusal"
            else "".join(block.text for block in response.content if block.type == "text")
        )
        usage = TokenUsage(
            input_tokens=getattr(response.usage, "input_tokens", 0),
            output_tokens=getattr(response.usage, "output_tokens", 0),
        )

        usage_tracker.record_usage(
            request_id=request_id,
            agent_name=request.agent_name,
            model=tier_config.model,
            tier=request.tier.value,
            usage=usage,
            latency_ms=latency_ms,
            user_id=request.user_id,
            role=request.role,
            department=request.department,
        )

        result = GenerateResult(
            text=text,
            stop_reason=response.stop_reason,
            usage=usage,
            request_id=request_id,
            model=tier_config.model,
            latency_ms=latency_ms,
        )
        return result

    def stream(self, request: GenerateRequest) -> Generator[StreamChunk, None, None]:
        """Yields StreamChunk objects; the final chunk has done=True and
        carries the completed TokenUsage. Not retried: once tokens start
        reaching the caller, restarting the request would mean re-emitting
        an already-partially-sent response, which is worse than surfacing
        the failure — so only connection setup gets Anthropic's own
        transport-level retries, not retry_handler's."""
        request_id = request.request_id or str(uuid.uuid4())
        tier_config = model_router.resolve(request.tier)
        system_block = self._system_block(request.system, request.cache_system)
        client = self._get_client()

        start = time.perf_counter()
        usage = TokenUsage()
        try:
            with self.capacity_guard():
                for chunk in stream_anthropic_response(
                    client,
                    model=tier_config.model,
                    max_tokens=request.max_tokens or tier_config.max_tokens,
                    system=system_block,
                    messages=request.messages,
                    effort=request.effort or tier_config.effort,
                    supports_extended_reasoning=tier_config.supports_extended_reasoning,
                ):
                    if chunk.done and chunk.usage:
                        usage = chunk.usage
                    yield chunk
        except anthropic.APIError as exc:
            raise GenerationError(str(exc), reason=classify_anthropic_error(exc)) from exc

        usage_tracker.record_usage(
            request_id=request_id,
            agent_name=request.agent_name,
            model=tier_config.model,
            tier=request.tier.value,
            usage=usage,
            latency_ms=(time.perf_counter() - start) * 1000,
            user_id=request.user_id,
            role=request.role,
            department=request.department,
        )


claude_gateway = ClaudeGateway()
