# Claude Gateway — Architecture

Scope note: this implements Task 2 of the LLM Gateway/Guardrails request, scoped per
[`docs/LLM_GATEWAY_ANALYSIS.md`](LLM_GATEWAY_ANALYSIS.md)'s Path A — a gateway wrapping the 4 real
Claude call sites in this repo, not a rebuild for an 11-agent manufacturing system that doesn't
exist yet.

## 1. Why

Before this change, every caller that needed Claude built its own `anthropic.Anthropic()` client
(or `ChatAnthropic()` for the LangGraph loop), duplicated the same `thinking`/`output_config`
kwargs, checked `stop_reason == "refusal"` itself, and called `record_token_usage()` by hand. None
of the 4 call sites had retry logic. None used prompt caching. Prompts were hardcoded Python string
constants with no version history. Adding a second model, a retry policy, or persisted usage
tracking meant editing 3-4 files identically.

The gateway (`backend/app/gateway/`) is now the *only* thing that talks to `anthropic`/
`langchain_anthropic` in this codebase. Every other module gets auth, model routing, retries,
caching, and usage tracking for free.

```
                                planner_agent (LangGraph)  ─┐
                                eval_judge                  ├──►  Claude Gateway  ──►  Anthropic SDK  ──►  Claude
                                conversation_summary        ─┘
```

## 2. Package layout

```
backend/app/gateway/
  __init__.py         re-exports claude_gateway (singleton) + GenerationError
  schemas.py           ModelTier, GenerateRequest/Result, StreamChunk, TokenUsage, RetryPolicy
  model_router.py      tier -> {model, max_tokens, effort}, sourced from backend/config/models.yaml
  retry_handler.py     call_with_retry() — exponential backoff + jitter, policy from config/llm.yaml
  prompt_manager.py    loads backend/prompts/{name}_{version}.yaml, lru_cached
  cache_manager.py     opt-in Redis response cache (get_cached/set_cached)
  usage_tracker.py     persists GatewayUsageLogModel rows + feeds the existing in-memory dashboard
  streaming.py         Anthropic stream -> StreamChunk generator -> SSE adapter
  claude_gateway.py    ClaudeGateway facade: generate(), stream(), get_langchain_model()

backend/prompts/
  planner_agent_v1.yaml
  judge_agent_v1.yaml
  memory_summarizer_v1.yaml

backend/config/
  models.yaml           tier routing + approximate per-model pricing
  llm.yaml               retry policy, timeout, cache policy
  guardrails.yaml         guardrail toggles (see GUARDRAILS_ARCHITECTURE.md)
```

`backend/app/core/yaml_config.py` is the (intentionally tiny) loader both the gateway and the
guardrails extensions use — see §7 for why it only owns *new* config, never existing `Settings`
fields.

## 3. The two call shapes

**`generate()`** — the direct, single-call path used by `generation_judge.py` (LLM-as-judge
scoring) and `memory/store.py` (conversation summarization):

```python
result = claude_gateway.generate(GenerateRequest(
    agent_name="eval_judge",
    system=JUDGE_SYSTEM_PROMPT,
    messages=[{"role": "user", "content": user_message}],
    tier=ModelTier.FAST,
    max_tokens=500,
))
# result.text, result.stop_reason, result.usage, result.model, result.latency_ms
```

Internally: check the opt-in response cache → resolve the tier to a model → call Anthropic through
`retry_handler.call_with_retry` → record usage → populate the cache (if requested) → return.

**`get_langchain_model()`** — for `planner.py`'s LangGraph loop, which needs `.bind_tools()` and
`ToolNode` support that a plain `generate()` call can't provide:

```python
model = claude_gateway.get_langchain_model(tier=ModelTier.FAST, max_tokens=settings.agent_max_tokens)
model = model.bind_tools(tools)
...
response = retry_handler.call_with_retry(lambda: model.invoke([...]), agent_name="planner_agent")
```

This is the one deliberate seam in "no agent calls Claude except through the gateway": LangGraph's
`ToolNode`/`.bind_tools()` need a `langchain_core.BaseChatModel` instance to call `.invoke()`
directly, not a single `generate()` call — see `docs/LLM_GATEWAY_ANALYSIS.md` §5 question 4 for
the alternative that was considered and rejected (refactoring the planner loop to hand-roll tool
call parsing was judged far more invasive for no real benefit). The gateway still owns *auth* and
*model routing* for this path; retry is applied explicitly at the one call site
(`planner.py::call_model`) since `ChatAnthropic.invoke()` isn't itself routed through
`retry_handler`.

## 4. Model routing

Two tiers, defined in `backend/config/models.yaml`:

| Tier | Intended for | Model today |
|---|---|---|
| `fast` (default) | daily ops, RAG answers, reports, eval judging, summarization | `claude-opus-5` |
| `reasoning` | complex multi-step planning (no current caller needs this — exists so the mechanism is real, not a stub) | `claude-opus-5` |

Both tiers point at the same model today — this repo genuinely only uses one model
(`docs/LLM_GATEWAY_ANALYSIS.md` §0 confirmed there's no existing sonnet/opus split to preserve).
Routing is now a config change (`models.yaml`), not a code change, for whenever a second model
is introduced.

## 5. Retry

`retry_handler.call_with_retry(fn, agent_name=...)` wraps a zero-arg callable, retrying on
`anthropic.APIError` subclasses named in `llm.yaml`'s `retry.retryable_errors` (rate limits,
connection errors, timeouts, 5xx) with exponential backoff + jitter, capped at `max_delay_seconds`.
Anything else (bad request, auth failure, permission denied) propagates on the first attempt —
retrying those can't help.

Streaming is **not** retried (see `claude_gateway.stream()`'s docstring): once tokens have reached
the caller, restarting the request means re-emitting a partially-sent response, which is worse than
surfacing the failure. Only the LangGraph path's `.invoke()` call and the plain `generate()` path
get retry coverage.

## 6. Streaming

`claude_gateway.stream(request)` yields `StreamChunk(text, done, usage)` objects;
`streaming.to_sse()` adapts that into `data: {...}\n\n` lines for
`fastapi.responses.StreamingResponse(..., media_type="text/event-stream")`. This is a real, tested
capability — but no route in this repo calls it yet, since `POST /chat` returning a single
synchronous JSON response is existing, working behavior this migration didn't touch (per "don't
rewrite existing functionality"). Wiring an SSE chat endpoint is a natural next step, not done here.

## 7. Prompt versioning

Prompts moved from hardcoded Python constants to `backend/prompts/{name}_{version}.yaml`:

```yaml
name: planner_agent
version: v1
changelog:
  - "v1: extracted verbatim from the inline PLANNER_SYSTEM_PROMPT constant..."
text: |
  You are the planning and synthesis layer for a multi-agent document intelligence system.
  ...
```

`prompt_manager.load_prompt(name, version)` is `lru_cache`d — a running process reads a prompt file
once. **Prompt files are treated as immutable once shipped**: to change wording, add a new
`_v2.yaml` file and point the caller at it, rather than editing `_v1.yaml` in place, so a live
deployment's behavior never shifts mid-flight and old logged requests stay reproducible against the
prompt version that actually produced them.

The three existing system prompts (planner, judge) and the memory-summarizer instruction template
were extracted **verbatim** — no wording changed as part of this migration.

## 8. Prompt caching

`GenerateRequest.cache_system=True` wraps the `system` string in Anthropic's `cache_control:
{"type": "ephemeral"}` content-block shape (`claude_gateway._system_block()`). Since all three
system prompts here are static per version, this is cheap to enable broadly — governed by
`llm.yaml`'s `cache.prompt_cache_enabled` (on by default). Not yet enabled at any of the 3 call
sites in this pass (they pass `cache_system=False`, the schema default) — flipping it on is a
one-line change per call site once the cost/latency tradeoff has been measured against real traffic.

## 9. Response caching (Redis)

Separate from prompt caching: `cache_manager.py` is a Redis-backed cache for a full `generate()`
response, keyed on `sha256(agent_name + system + messages + tier)`. **Off unless a caller explicitly
sets `cache_ttl_seconds`** — caching a conversational chat reply by default would be wrong (the same
user message can mean something different depending on conversation history), so none of the 3
wired call sites enable it. It's there for future callers where the same input genuinely always
warrants the same output (e.g. a fixed eval-suite question). Fails open on any Redis error — a cache
outage degrades to "no caching," never a hard failure.

## 10. Usage tracking

`usage_tracker.record_usage()` runs after every `generate()`/`stream()` call:

1. Writes to `GatewayUsageLogModel` (new Postgres table: `request_id`, `agent_name`, `model`,
   `tier`, `tokens_input/output`, `latency_ms`, `cost_usd`) — durable, queryable by request.
2. Also calls the pre-existing `record_token_usage()` (`services/monitoring/metrics.py`), so the
   admin dashboard (`GET /admin/metrics`) keeps working unchanged — nothing regresses for the
   existing in-memory view.

`cost_usd` is an estimate from `models.yaml`'s `pricing` block (`input_per_million_usd` /
`output_per_million_usd` per model) — not billing-accurate, just enough for relative cost tracking.
Update it to match your actual Anthropic pricing agreement.

Both writes are best-effort: a tracking failure (e.g. Postgres briefly unavailable) is logged and
swallowed, never surfaced to the caller — the same pattern `_log_upload()` already uses in
`routers/documents.py`.

**Explicitly deferred**: Langfuse and OpenTelemetry integration (request Task 10) were scoped out
of this pass — see `docs/LLM_GATEWAY_ANALYSIS.md` §2/§5. `usage_tracker.py` is deliberately the
single place a Langfuse/OTel exporter would hook in later without touching the 3 call sites again.

## 11. Configuration split

New gateway/guardrail settings live in `backend/config/*.yaml`, loaded via
`core/yaml_config.load_yaml_config()` — **not** through the existing `core/config.py::Settings`
class. This was a deliberate choice to avoid two competing sources of truth: every setting that
already existed in `Settings` (e.g. `anthropic_api_key`, `claude_model_name`) keeps `Settings` as
its one authoritative source; only settings that are genuinely new with this work (model tiers,
retry policy, cache policy, the two new guardrail toggles) are sourced from YAML. See
`guardrails.yaml`'s header comment for the same rule applied on the guardrails side.

`backend/config/` and `backend/prompts/` are `COPY`'d into the Docker image explicitly
(`backend/Dockerfile`) alongside `app/` — they were not previously part of the image.

## 12. Deployment considerations

- **New dependencies**: `pyjwt`, `redis`, `pyyaml` (added across this and the prior auth increment)
  — no NeMo/Langfuse/OTel/MCP packages, per the approved Path A scope.
- **New Postgres table**: `gateway_usage_logs`, created automatically by the existing
  `ensure_schema()` / `Base.metadata.create_all()` mechanism — no separate migration step.
- **Redis is now load-bearing** for response caching (opt-in) and rate limiting (prior increment);
  both fail open if Redis is unreachable, so a Redis outage degrades gracefully rather than taking
  the API down.
- **No breaking API changes**: `/chat`'s request/response shape is unchanged except one additive
  field (`confidence` — see `AGENT_SECURITY_MODEL.md`). Existing Streamlit frontend calls keep
  working without modification.

## 13. Extension points

- **A second real model**: add an entry to `models.yaml`'s `tiers`, point `reasoning` (or a new
  tier) at it — no Python changes needed for routing itself; a caller opts in via
  `GenerateRequest(tier=ModelTier.REASONING)`.
- **Streaming chat endpoint**: `claude_gateway.stream()` + `streaming.to_sse()` are ready; add a
  `POST /chat/stream` route that calls them and update the frontend to consume SSE.
- **Langfuse/OpenTelemetry**: hook into `usage_tracker.record_usage()` (single call site) and/or
  wrap `ClaudeGateway.generate()`/`.stream()` with a tracer span.
- **A 4th/5th LLM call site**: build a `GenerateRequest`, call `claude_gateway.generate()`. That's
  the entire integration surface — no other gateway internals need touching.
