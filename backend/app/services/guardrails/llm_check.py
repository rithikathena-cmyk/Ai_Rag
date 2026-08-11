"""LLM-based advanced input check (docs/GUARDRAILS_ARCHITECTURE.md §10) — a
second-pass rail for phrasing the deterministic regex/keyword checks
(injection.py, destructive.py, scope.py) can miss. Two providers are
supported (`_PROVIDERS`, selected via guardrails.yaml's `provider` key):
`gemini` (default historically — a $0 free-tier quota, kept entirely
separate from the Claude Gateway) and `anthropic` (routed through the
existing Claude Gateway, reusing its retries/prompt-caching/usage-tracking/
concurrency cap for free — but every call now costs real Anthropic tokens on
top of the planner/judge/rewrite calls that already use it). The anthropic
path defaults to the cheapest tier (haiku) specifically because this check
runs on every message that reaches it, unlike the once-per-turn planner
call. No per-user budget attribution here (unlike query_rewrite/memory
summarization): run_input_guardrails() doesn't thread user context through
its uniform `check(text)` signature, and adding that is a larger pipeline
change than this provider swap warrants — this call's cost is tracked in
gateway_usage_logs under agent_name="guardrail_llm_check" same as any other
un-attributed system call.
"""

import json
import threading
import time
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.yaml_config import load_yaml_config
from app.gateway.claude_gateway import GenerationError, claude_gateway
from app.gateway.prompt_manager import load_prompt
from app.gateway.schemas import GenerateRequest, ModelTier
from app.services.guardrails.types import GuardrailStep
from app.services.monitoring.metrics import record_latency

NAME = "llm_advanced_check"

PROMPT = load_prompt("guardrail_llm_check", "v1")
SYSTEM_PROMPT = PROMPT.text

_GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# A single shared token bucket — not per-user, since this guards one shared
# resource per provider (Gemini's free-tier RPM quota, or — on the anthropic
# provider — overall cost exposure from this check running on every message)
# rather than a per-user limit (contrast with services/llm_rbac/rate_limiter.py's
# per-user buckets). Exceeding it fails open immediately, before spending a
# network round trip (or, for anthropic, real token cost), so a burst
# degrades to "no advanced check" instead of a wave of provider errors that
# would have failed open anyway.
_bucket_lock = threading.Lock()
_bucket_tokens = 0.0
_bucket_capacity = 0.0
_bucket_last_refill = 0.0


def _check_rate_limit(limit_per_minute) -> bool:
    global _bucket_tokens, _bucket_capacity, _bucket_last_refill
    if not limit_per_minute or limit_per_minute <= 0:
        return True
    with _bucket_lock:
        now = time.monotonic()
        if _bucket_capacity != limit_per_minute:
            # First use, or the configured limit changed — reset to a
            # fresh, fully-topped-up bucket rather than carry over a stale
            # token count at the old capacity.
            _bucket_tokens = float(limit_per_minute)
            _bucket_capacity = float(limit_per_minute)
            _bucket_last_refill = now
        else:
            elapsed = now - _bucket_last_refill
            _bucket_tokens = min(_bucket_capacity, _bucket_tokens + elapsed * (limit_per_minute / 60.0))
            _bucket_last_refill = now
        if _bucket_tokens >= 1:
            _bucket_tokens -= 1
            return True
        return False


class LlmCheckError(Exception):
    pass


@dataclass
class _LlmVerdict:
    verdict: str  # "block" | "pass"
    reason: str


def _config() -> dict:
    return load_yaml_config("guardrails.yaml").get("llm_advanced_check", {})


def _call_gemini(text: str, *, model: str, timeout_seconds: float) -> _LlmVerdict:
    if not settings.gemini_api_key:
        raise LlmCheckError("GEMINI_API_KEY is not configured")

    url = _GEMINI_ENDPOINT.format(model=model)
    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        # gemini-flash-latest (currently gemini-3.6-flash) is a reasoning
        # model that spends tokens on internal "thinking" before writing the
        # answer (verified live: ~55-180 thoughtsTokenCount for a trivial
        # prompt) — thinkingConfig.thinkingBudget=0 to disable it 400s on
        # this model, so budget generously instead; 100 truncated the JSON
        # mid-token.
        "generationConfig": {"temperature": 0, "maxOutputTokens": 400},
    }
    try:
        response = httpx.post(url, params={"key": settings.gemini_api_key}, json=payload, timeout=timeout_seconds)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # Deliberately not str(exc) — httpx embeds the full request URL
        # (including the ?key=... query param) in its exception message, and
        # this text flows straight into a GuardrailStep the /chat response
        # surfaces to any authenticated user with "Show reasoning summary"
        # on (check_with_llm() below), not just admins. A leaked API key in
        # a user-facing trace is a real credential exposure, not cosmetic.
        raise LlmCheckError(f"Gemini request failed with HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        # Timeouts/connection errors don't carry a response, but httpx's
        # exception message can still include the request URL — same reason,
        # just the exception class name, never str(exc).
        raise LlmCheckError(f"Gemini request failed: {type(exc).__name__}") from exc

    data = response.json()
    try:
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise LlmCheckError(f"Unexpected Gemini response shape: {data!r}") from exc

    return _parse_verdict(raw_text)


def _call_anthropic(text: str, *, model: str, timeout_seconds: float) -> _LlmVerdict:
    # timeout_seconds is unused here (unlike _call_gemini's per-call httpx
    # timeout) — the Claude Gateway owns its own request timeout
    # (llm.yaml's request_timeout_seconds), applied at the Anthropic client
    # level rather than per-call. Kept as a parameter only so both providers
    # share the same call(text, model=..., timeout_seconds=...) shape that
    # check_with_llm() below calls uniformly.
    try:
        tier = ModelTier(model)
    except ValueError:
        tier = ModelTier.HAIKU

    try:
        result = claude_gateway.generate(
            GenerateRequest(
                agent_name="guardrail_llm_check",
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": text}],
                tier=tier,
                max_tokens=400,
                cache_system=True,
            )
        )
    except GenerationError as exc:
        raise LlmCheckError(f"Claude Gateway request failed: {exc}") from exc

    if result.stop_reason == "refusal":
        raise LlmCheckError("Model refused to evaluate the input")

    return _parse_verdict(result.text)


def _parse_verdict(raw_text: str) -> _LlmVerdict:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise LlmCheckError(f"Non-JSON model output: {text[:200]!r}") from exc

    verdict = str(parsed.get("verdict", "pass")).strip().lower()
    reason = str(parsed.get("reason", ""))[:300]
    if verdict not in ("block", "pass"):
        raise LlmCheckError(f"Unrecognized verdict {verdict!r}")
    return _LlmVerdict(verdict=verdict, reason=reason)


_PROVIDERS = {"gemini": _call_gemini, "anthropic": _call_anthropic}
# Per-provider default for guardrails.yaml's `model` key — a literal Gemini
# model string for gemini, a ModelTier name (cheapest/haiku) for anthropic.
_DEFAULT_MODEL = {"gemini": "gemini-flash-latest", "anthropic": "haiku"}


def check_with_llm(text: str) -> GuardrailStep:
    """Reached only when every deterministic input check ahead of it in
    pipeline.py's check order already passed — a message the regex checks
    already blocked never reaches (and never pays for) this call.

    Reliability: any failure (missing key, network error, timeout, malformed
    output, unknown provider) fails OPEN — returns "pass" and never blocks a
    real user's message because this optional second-pass layer had an
    infrastructure problem. The deterministic checks ahead of it remain the
    actual security floor; this is additive coverage, not the only line of
    defense, so failing open here does not leave the pipeline unprotected."""
    cfg = _config()
    if not cfg.get("enabled", False):
        return GuardrailStep(NAME, "pass", "Check disabled")

    provider = cfg.get("provider", "gemini")
    call = _PROVIDERS.get(provider)
    if call is None:
        return GuardrailStep(NAME, "pass", f"Unknown provider {provider!r} — check disabled")

    truncated = text[: cfg.get("max_input_chars", 2000)]
    if not truncated.strip():
        return GuardrailStep(NAME, "pass", "Empty input")

    if not _check_rate_limit(cfg.get("rate_limit_per_minute")):
        return GuardrailStep(NAME, "pass", "rate limit reached, failed open")

    start = time.perf_counter()
    try:
        result = call(
            truncated, model=cfg.get("model", _DEFAULT_MODEL.get(provider, "")),
            timeout_seconds=float(cfg.get("timeout_seconds", 4.0)),
        )
    except LlmCheckError as exc:
        return GuardrailStep(NAME, "pass", f"check unavailable, failed open: {exc}")
    except Exception as exc:  # belt-and-suspenders — an infra hiccup must never block a real request
        return GuardrailStep(NAME, "pass", f"unexpected error, failed open: {exc}")
    finally:
        record_latency(f"guardrail.llm_check.{provider}", (time.perf_counter() - start) * 1000)

    if result.verdict == "block":
        return GuardrailStep(NAME, "block", result.reason or "Flagged by LLM-based check")
    return GuardrailStep(NAME, "pass", result.reason or "No concern flagged")
