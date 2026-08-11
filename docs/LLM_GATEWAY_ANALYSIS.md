# LLM Gateway & Guardrails — Repository Analysis

**Status: analysis only. No implementation code has been written. This document is the Task 1 deliverable and requires sign-off before Task 2+ begins, per the request that spawned it.**

## 0. Direct answer: does the requested architecture match this repository?

**No — not yet.** The request describes an architecture roughly 3-4 phases ahead of what's actually in the repo. Concretely:

| Assumption in the request | Reality in this repo |
|---|---|
| 11 domain agents (Production, Maintenance, Inventory, Quality, Warehouse, Planning, Procurement, Reporting, Root Cause, Supervisor, Planner) | **1** planner agent + **3** tool-backed helpers (Retrieval, SQL, Report). The `Role` enum has manufacturing role *names* (`maintenance_engineer`, `production_manager`, etc.) added in the auth increment, but no agent, tool, or data model exists behind any of them yet — the enum's own docstring says they're "for RBAC-gated domain tools in a later increment." |
| Manufacturing data (machines, sensors, work orders — e.g. "why did M102 stop", `shutdown_machine(M102)`) | **Does not exist.** The Postgres schema is entirely document-RAG domain: `documents`, `chunks`, `entities`, `terms`, `permissions`, `reports`, `conversations`, `messages`, `eval_queries/runs`, `users`, `upload_logs`. No machine/sensor/production tables, no MES integration. |
| MCP tool integrations | Not present anywhere in the codebase. |
| Claude Agent SDK | Not used. The repo uses the plain `anthropic` Python SDK plus `langchain-anthropic` + `langgraph`. |
| Langfuse, OpenTelemetry, Prometheus, Grafana | None installed, none configured. Observability today is a single hand-rolled in-memory module (`services/monitoring/metrics.py`) — deques of recent samples, no persistence, no external time-series store, reset on restart. |
| NeMo Guardrails (Colang rails) | Not installed. A **functionally similar but hand-written** guardrails system already exists (`services/guardrails/`) — regex-based prompt-injection/destructive-intent detection, PII redaction, scope keywords, system-prompt-leak check — wired explicitly into `POST /chat`, not as global middleware. |
| `claude-sonnet` for daily ops, `claude-opus` for complex reasoning (model routing) | **One** model for everything: `settings.claude_model_name = "claude-opus-5"`. No routing logic exists. |
| Prompt versioning / YAML prompt files | Prompts are hardcoded Python string constants inline in each module (`PLANNER_SYSTEM_PROMPT` in `planner.py`, `JUDGE_SYSTEM_PROMPT` in `generation_judge.py`, etc.). No `prompts/` directory, no version tracking. |
| Prompt caching | Not used in any of the 4 SDK call sites. |
| Document-permission-aware retrieval ("Maintenance Engineer can't see financial documents") | `PermissionModel` exists as a table + CRUD API (grant/revoke), but retrieval (`search_documents`) **does not consult it**. Any authenticated caller can retrieve any chunk from any document today. |
| Streaming responses | `POST /chat` is fully synchronous — no SSE, no `stream=True` anywhere. |
| `config/llm.yaml`, `config/guardrails.yaml`, `config/models.yaml` | Config is one flat `Settings(BaseSettings)` class in `core/config.py`, env-var driven (~90 fields covering every subsystem). No YAML config loader exists in the codebase. |

**What does match:** the request's Task 2 (a gateway that centralizes Claude calls) and parts of Task 9/10 map cleanly onto real, working code — see §4. Tasks 3-8 as literally scoped (NeMo Guardrails, MCP tool execution rails, manufacturing-department retrieval filtering) describe capabilities for a manufacturing-domain agent system that hasn't been built yet, not a hardening pass over an existing one.

**Recommendation:** treat this as two separate efforts. (1) A gateway migration — genuinely valuable now, low risk, wraps 4 existing call sites. (2) A guardrails upgrade — also valuable, but should extend the existing hand-written pipeline rather than bolting on `nemoguardrails` (a Colang-based DSL) as a parallel, disconnected system, *unless* NeMo Guardrails is a hard requirement independent of this repo's current state. §5 lays out both paths.

---

## 1. Current architecture

### 1.1 Backend layout

The request's assumed layout (`backend/agents/`, `backend/rag/`, `backend/api/`, `backend/database/`, `backend/models/`, `backend/config/`) doesn't match. Actual layout:

```
backend/app/
  core/            config.py (Settings), errors.py (AppError), roles.py (Role enum)
  db/              postgres.py, qdrant.py, redis.py
  models/          one SQLAlchemy model per table (document, chunk, user, permission, report,
                    conversation, message, entity, term, eval_query, eval_run, upload_log, ...)
  routers/         one FastAPI router per resource (chat, documents, search, conversations,
                    reports, admin, evaluation, users, auth, upload_logs, terms, health)
  services/
    agents/        planner.py (LangGraph loop), retrieval_agent.py, sql_agent.py, report_agent.py
    auth/          password.py, jwt.py, dependencies.py, rbac.py
    chunking/       classification/     embedding/        entities/
    evaluation/     generation/         guardrails/        ingestion/
    memory/         monitoring/          rate_limit/        reranking/
    retrieval/      sparse/             summarization/
  main.py          FastAPI app, router registration, one latency-tracking middleware,
                    3 global exception handlers
```

### 1.2 Every LLM call site (exhaustive)

Four places call the Anthropic API. No fifth exists.

| # | File | Call pattern | Model param | Used by |
|---|---|---|---|---|
| 1 | `services/agents/planner.py::run_agent()` | `ChatAnthropic(...).bind_tools([...])` invoked inside a LangGraph `StateGraph` loop | `settings.claude_model_name` | `POST /chat` (the live, agentic path) |
| 2 | `services/generation/client.py::generate_answer()` | Raw `anthropic.Anthropic().messages.create()` | `settings.claude_model_name` | `services/generation/pipeline.py::answer_query()` — **dead code, not called from any router**. Grepped repo-wide; zero callers of `answer_query`. |
| 3 | `services/evaluation/generation_judge.py::judge_answer()` | Raw `anthropic.Anthropic().messages.create()` | `settings.claude_model_name` | `POST /eval/queries/{id}/run` (LLM-as-judge scoring) |
| 4 | `services/memory/store.py::maybe_summarize()` | Raw `anthropic.Anthropic().messages.create()` | `settings.claude_model_name` | Called at the end of every `POST /chat` turn once the conversation passes `conversation_summary_trigger_turns` |

All four read the same singleton client from `services/generation/client.py::get_client()` (already a de-facto minimal client factory) except site #1, which builds its own `ChatAnthropic` instance because LangGraph's `ToolNode` needs a LangChain-wrapped model, not a raw SDK object.

Common traits across all four: use `settings.claude_model_name` (no routing), pass `thinking={"type": "adaptive"}` and `output_config={"effort": ...}`, call `record_token_usage(source, model, in, out)` after the response, check `stop_reason == "refusal"`, raise a module-local `*Error` on `anthropic.APIError`. **No retry logic exists anywhere** — an `APIError` propagates straight to the caller (in `/chat`, this is caught and triggers `run_retrieval_fallback`, a non-LLM degraded path).

### 1.3 LangGraph flow

One graph, defined fresh on every `run_agent()` call (not compiled once at module load):

```
START -> agent (call_model: ChatAnthropic.invoke) -> conditional_edge
           ^                                              |
           |                                     tool_calls present?
           +--------------- tools (ToolNode) <----+-- yes
                                                    \-- no --> END
```

Three tools, each a thin LangChain `@tool`-decorated closure wrapping a plain Python function:
- `search_documents_tool` → `services/agents/retrieval_agent.py::search_documents()` → `reranking/pipeline.py::search_with_reranking()` → `retrieval/search.py::hybrid_search()` (Qdrant dense+sparse, RRF fusion) → reranked with `bge-reranker-base`.
- `query_analytics_tool` → `services/agents/sql_agent.py::run_analytics_query()` — regex/sqlparse-validated single-SELECT against an allow-listed table set, wrapped in `LIMIT 500`, always rolled back.
- `generate_report_tool` → `services/agents/report_agent.py::generate_report()` — writes CSV/XLSX/DOCX/PDF via openpyxl/python-docx/reportlab.

Max 4 tool-call iterations (`agent_max_tool_iterations`), recursion-limited; `GraphRecursionError` is caught and returns a canned "didn't finish" reply rather than crashing.

### 1.4 RAG pipeline

`retrieval/search.py::hybrid_search()`: resolves a document-ID allowlist from Postgres metadata filters (`document_type`, `classification`, `language`, `latest_version_only`) **before** hitting Qdrant, then runs dense (BGE-M3) + sparse (BM25) prefetch merged via Qdrant-native RRF, or falls back to dense-only if no sparse terms match. `reranking/pipeline.py` re-scores the candidate pool with a cross-encoder.

**Gap relevant to Task 5 (Retrieval Rails):** `PermissionModel` (user_id, document_id, permission_level) exists and has a full CRUD surface (`POST/GET/DELETE /documents/{id}/permissions`), but `hybrid_search()`'s metadata filter never joins against it. Permission enforcement at retrieval time is genuinely new work, not a wrap-and-reuse.

### 1.5 Existing guardrails (not NeMo, but same intent as Tasks 4/7)

`services/guardrails/pipeline.py` — plain Python, no DSL:

```
run_input_guardrails(text):
  length -> prompt_injection (regex) -> destructive_intent (regex) -> scope (keyword allow/deny) -> pii_redact
  (first "block" short-circuits the rest)

run_output_guardrails(text):
  system_prompt_leak_check -> pii_redact
```

Called explicitly from `routers/chat.py` (not global middleware — only `/chat` runs these; `/search`, `/documents`, etc. do not). Every step records a `GuardrailStep(name, action, detail)` shown in the chat trace and logged via `record_guardrail_event()` into the same in-memory metrics deque. Each check is individually toggleable via `settings.guardrail_*` flags.

This is the closest existing analog to NeMo's input/output rails. It has no retrieval-rail or execution-rail equivalent (§1.4 gap; and no MCP tools exist to gate — §0).

### 1.6 Existing "deterministic guard" equivalent to the requested `sql_guard.py`

`services/agents/sql_agent.py::_validate_select()` already does exactly what Task 3's `deterministic/sql_guard.py` asks for: single-SELECT enforcement via `sqlparse`, a forbidden-keyword regex (DDL/DML/superuser functions), and a table allowlist. It's currently embedded in the SQL tool rather than factored out as a reusable guard, but the logic is complete and tested-by-use.

### 1.7 Existing configuration system

One `pydantic_settings.BaseSettings` subclass (`core/config.py`), ~90 flat fields, loaded from a single repo-root `.env` (path resolved absolutely so it works regardless of process cwd). Every subsystem's knobs live in this one class — embedding, chunking, guardrails, auth/JWT, Redis, rate limiting, LLM (`claude_model_name`, `claude_max_tokens`, `claude_effort`), memory/summarization, upload validation. No YAML loading exists anywhere in the repo.

### 1.8 Existing observability

`services/monitoring/metrics.py`: five `deque(maxlen=1000)` buffers (latency, token usage, retrieval-stage timings, ingestion-stage timings, guardrail events), guarded by a single `threading.Lock`, read by `GET /admin/metrics` and `/admin/query-metrics`. Explicitly documented as best-effort/dev-scale: resets on restart, doesn't aggregate across multiple uvicorn workers. `record_token_usage(source, model, input_tokens, output_tokens)` is the only per-LLM-call telemetry captured today — no request_id, no per-call latency, no cost calculation, no persistence to Postgres.

### 1.9 Existing middleware

Exactly one: `track_latency` in `main.py`, wrapping every request in a `time.perf_counter()` measurement fed to `record_latency()`. Three global exception handlers normalize errors to `{"error": {"code", "message"}}`. No auth middleware (auth is per-route `Depends`, added in the prior increment), no guardrail middleware (guardrails are called explicitly inside `/chat` only).

---

## 2. Gap summary (what's real vs. what needs to be built)

| Request component | Status |
|---|---|
| Central gateway wrapping Claude calls | **Buildable now** — 4 known call sites, one already-shared client factory to build on |
| Model routing (sonnet/opus by task) | **New** — currently one model everywhere; need to decide routing policy |
| Retry/backoff | **New** — currently zero retry logic anywhere |
| Streaming | **New** — `/chat` is fully synchronous today |
| Prompt versioning (YAML) | **New** — prompts are inline Python strings today |
| Prompt caching | **New** — not used |
| Usage tracking (request_id, cost) | **Partial** — token counts are tracked in-memory; no request_id/cost/persistence |
| Redis for caching | **Partial** — Redis exists (added for rate limiting), unused for query/embedding/memory caching |
| Input rails (injection/jailbreak) | **Exists**, as hand-written regex checks, not NeMo/Colang |
| Retrieval rails (permission filtering) | **New** — `PermissionModel` exists but isn't consulted at retrieval time |
| Execution rails (MCP tool gating, approval workflow) | **New** — no MCP tools, no approval-request model exist to gate |
| Output rails (citation/confidence requirements) | **Partial** — citations already exist (`[1]`-style, from `search_documents`); confidence scoring and enforced hallucination checks don't |
| YAML config split (`llm.yaml`/`guardrails.yaml`/`models.yaml`) | **New** — one flat env-driven `Settings` class today |
| Langfuse / OpenTelemetry / Prometheus / Grafana | **New** — none installed; only an in-memory metrics module exists |
| NeMo Guardrails (`nemoguardrails`, Colang) | **New** — not installed |
| 11-agent manufacturing architecture, MCP machine-control tools | **New, and the largest gap** — requires a manufacturing data model that doesn't exist in this repo at all |

---

## 3. Files that would be touched by a scoped-to-reality gateway migration (Task 2 only)

If we proceed with just the Claude Gateway (not the full 11-agent/MES/NeMo scope):

**New:**
- `backend/app/gateway/{__init__,claude_gateway,model_router,prompt_manager,cache_manager,usage_tracker,retry_handler,streaming,schemas}.py`
- `backend/prompts/*.yaml` (extracted from the 4 inline prompt constants)

**Modified (swap direct SDK/ChatAnthropic calls for gateway calls — logic inside each file is otherwise untouched):**
- `services/agents/planner.py` — `ChatAnthropic(...).bind_tools(...)` → gateway-provided LangChain-compatible model, or refactor `call_model` to call `claude_gateway.generate()` directly and hand-roll tool-call parsing (design decision, see open questions)
- `services/evaluation/generation_judge.py` — `get_client().messages.create()` → `claude_gateway.generate()`
- `services/memory/store.py::maybe_summarize()` — same swap
- `services/generation/client.py` / `pipeline.py` — either migrated to the gateway or deleted as dead code (confirm with product owner before deleting; currently unreferenced)
- `core/config.py` — add gateway-specific settings (or introduce `config/llm.yaml` alongside it, per Task 9)
- `requirements.txt` — no new packages required for the gateway itself (it's built on the already-present `anthropic` SDK); would need additions only for streaming-to-FastAPI (already supported by `StreamingResponse`, stdlib) and any cache backend beyond the existing Redis client

**Not touched:** `retrieval_agent.py`, `sql_agent.py`, `report_agent.py`, all of `chunking/`, `embedding/`, `ingestion/`, `reranking/`, `classification/` — these contain no LLM calls and are out of scope for a gateway migration.

---

## 4. Reusable components (don't rebuild these)

- **`services/generation/client.py::get_client()`** — the singleton-client pattern the gateway's auth/client management should extend, not replace.
- **`core/config.py`** — the settings-loading mechanism (absolute-path `.env` resolution, `pydantic-settings`) works and is used consistently repo-wide; new config should plug into this pattern (or sit alongside it as YAML, per Task 9 — this is a genuine design choice, see below).
- **`services/monitoring/metrics.py`** — the in-memory recording pattern (`record_token_usage`, `record_latency`, `record_guardrail_event`) is the existing usage-tracking substrate; a gateway `usage_tracker.py` should either extend this module or clearly supersede it (not run a disconnected parallel system the admin dashboard doesn't see).
- **`services/guardrails/`** — the entire pipeline (types, individual checks, `pipeline.py`) is a working input/output rail system. Extending it (new checks, wiring it into more routes, adding retrieval/execution stages) is lower-risk than introducing a second, disconnected NeMo-based system.
- **`services/agents/sql_agent.py::_validate_select()`** — directly reusable as the deterministic SQL guard the request asks for; just needs factoring into a standalone module if it should also gate ad-hoc SQL from other future callers.
- **`db/redis.py::get_redis_client()`** — ready to use as the cache backend for `cache_manager.py`.
- **`core/roles.py::Role` / `services/auth/rbac.py::require_role()`** — the permission-checking substrate for execution rails, already built in the prior increment.
- **LangGraph `StateGraph`/`ToolNode` pattern in `planner.py`** — the multi-tool-call loop this repo already runs; any new agent should follow this same shape rather than introducing a different orchestration pattern.

---

## 5. Two paths forward — needs a decision before Task 2 starts

**Path A — Gateway + guardrails-hardening, scoped to what exists.** Build the Claude Gateway (Task 2) wrapping the 4 real call sites; extend the existing hand-written guardrails pipeline with retrieval-permission filtering (real gap, real value) and a factored-out SQL guard; add real retry/streaming/prompt-versioning/basic usage persistence. Skip NeMo Guardrails, MCP execution rails, Langfuse/OTel/Prometheus, and the 11-agent MES buildout — flag them as follow-on work once the manufacturing data model and MCP tools they depend on actually exist. Lowest risk, ships value against real gaps, doesn't introduce two parallel guardrail systems.

**Path B — Build the full spec as written.** Install `nemoguardrails`, Langfuse, OpenTelemetry SDKs; stand up the 11-agent architecture and MCP tool layer from scratch, including a manufacturing data model (machines, sensors, work orders, approval requests) that doesn't exist yet. This is a much larger effort than "wrap the existing gateway" — it's closer to a second product surface next to the current document-RAG chatbot. Worth doing if the MES agent buildout is genuinely the next roadmap item and this gateway/guardrails work is meant to be its foundation, not a hardening pass on the current app.

**Questions this analysis can't answer on its own:**
1. Is the 11-agent manufacturing system in active development elsewhere (a data model / MCP tools coming soon), or should this task scope down to what exists today?
2. Is `nemoguardrails` a hard requirement (e.g., a platform standard), or is extending the existing hand-written guardrails pipeline acceptable?
3. Should `generate_answer()`/`answer_query()` (confirmed dead code) be deleted or migrated as part of this work?
4. For `planner.py`'s LangGraph integration specifically: should the gateway expose a LangChain-`BaseChatModel`-compatible interface (keeps `ToolNode`/`bind_tools` working unchanged) or should the LangGraph loop be refactored to call `claude_gateway.generate()` directly with hand-rolled tool-call handling? The former is far less invasive.
5. Is YAML config (`config/llm.yaml` etc.) meant to *replace* `core/config.py`'s relevant fields, or *supplement* it as a separate loader for gateway/guardrail-specific settings only?
