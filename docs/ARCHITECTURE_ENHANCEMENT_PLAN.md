# Architecture Enhancement Plan

**Status: Phase 1 (audit + plan) complete. Phase 2 (evaluation completeness) implemented — see
§11. Phase 3A (parent-child retrieval) and Phase 3B (query rewriting) implemented and tested, both
disabled by default — see §12/§13. Phase 3 Evaluation Gate run — see §14: verdict for both features was
**INSUFFICIENT EVIDENCE**, and a limitation in the evaluation methodology itself was discovered (the
runner bypassed the production retrieval path) and documented in §14 and `docs/RAG_RETRIEVAL.md`.
**Evaluation Architecture Correction implemented and tested — see §15.** **The corrected evaluation was
then made runnable and re-run for real — see §16**: the repository's existing native Qdrant binary
(`qdrant-bin/qdrant.exe`) was started and verified against its own pre-existing real vector data, and
both curated eval queries were run through all four configurations (baseline/parent-child/query-rewrite/
combined) via the corrected `run_evaluation()`. Real Recall/Precision/MRR/NDCG numbers now exist for the
first time. **The evaluation dataset was then expanded to 30 verified questions across 4 real documents
and the full gate re-run again — see §17**: dataset size is no longer a blocker (30 ≥ the 5-query
minimum), 0/30 query-rewrite attempts succeeded but all 30 correctly fell back (60/60 real attempts
across `query_rewrite`+`combined`), parent-context attached on 30/30 applicable runs, and the small
MRR/NDCG deltas that appear were traced to duplicate-document tie noise (an environment condition, not
a feature effect — see §17). Verdict for both features remains **INSUFFICIENT EVIDENCE** — not because
either measured badly, but because generation-quality metrics (the dimension both features actually aim
to improve) remain completely unmeasured, since `ANTHROPIC_API_KEY` still returns
`401 authentication_error` (confirmed live, unmodified). Phase 3C still awaits separate approval (per
the request's explicit phase-gating instruction) and is not recommended to start yet — see §17. A
separate, user-requested fast-guardrails Claude Gateway MVP demo was also built this session — see
`docs/GATEWAY_DEMO.md` (unrelated to Phase 3; not part of this plan's phase sequence). Phases 4+ not
started.**

This plan is the output of a full repository audit (5 parallel subsystem reviews covering every
file under `backend/app/services/`, `backend/app/models/`, `backend/app/routers/`,
`backend/app/gateway/`, and `backend/tests/`, plus `frontend/app/`). Every claim below is
file/line-cited in the underlying audit; this document summarizes and proposes, it doesn't
re-derive.

`WORKFLOW.md` is treated as the baseline architecture per the request that produced this plan — it
is accurate and current for what it documents (ingestion + query pipeline shape). Nothing here
replaces it; several gaps below are things `WORKFLOW.md` doesn't claim exist in the first place.

---

## 1. Current architecture (summary)

Stack: FastAPI + Streamlit + Postgres (SQLAlchemy, no Alembic) + Qdrant (dense + BM25-sparse named
vectors) + BGE-M3 embeddings + `bge-reranker-base` + Claude via a single-entrypoint gateway
(`gateway/claude_gateway.py`) + a 2-node LangGraph ReAct planner (`services/agents/planner.py`) with
four tools (`retrieval_agent`, `sql_agent`, `report_agent`, `project_agent`) + JWT auth + an LLM RBAC
policy engine (`services/llm_rbac/`, YAML-driven) + six deterministic guardrail checks + Redis
(rate limiting, RBAC quotas, gateway cache) + a synchronous ingestion pipeline.

Query flow (confirmed exactly as `WORKFLOW.md` describes it, with line numbers now attached):
`authorize_llm_request` (`chat.py:90`) → input guardrails (`chat.py:140`) → planner loop
(`chat.py:158-180`, fallback to raw retrieval on `GenerationError`) → output guardrails
(`chat.py:193`) → citation check + confidence score (`chat.py:201-206`) → memory update
(`chat.py:208-212`) → one blocking `ChatResponse` (`chat.py:220-227`). No streaming reaches the
client anywhere in this path today (see §3.13).

Ingestion flow (confirmed exactly as `WORKFLOW.md` describes it): fully synchronous inside
`POST /documents/upload` (`routers/documents.py:211-465`) — parse → summarize → NER → chunk →
embed → sparse-index → Postgres commit → Qdrant upsert, all within one HTTP request/response cycle,
using per-stage timeouts (`run_in_threadpool` + `asyncio.wait_for`) but never returning early.

---

## 2. Existing capabilities (what the audit found already built)

Organized by the enhancement phases they're most relevant to, so §3 (gaps) reads as a diff against
this section rather than a restatement.

**RAG evaluation (→ Phase 3 of the request).** Already substantial, not a green field:
`services/evaluation/` computes Recall@K, Precision@K, MRR, NDCG (`retrieval_metrics.py`, rule-based
against curated `expected_chunk_ids`) plus real LLM-judged `groundedness`/`faithfulness`/
`hallucination_rate` (`generation_judge.py`, an actual Claude call, tier=FAST) and retrieval/
generation latency (`runner.py`). `EvalQueryModel`/`EvalRunModel` persist all of it. A full CRUD +
run + summary API exists (`routers/evaluation.py`), and a frontend Evaluation page already renders
it (`frontend/app/views/evaluation.py`). `run_eval_query` executes the *real* production planner
(`run_agent`), not a stub — this is genuine evaluation infrastructure, not a placeholder.

**Tool-level security (→ Phase 4).** `authorize_llm_request()` (`llm_rbac/engine.py:28-82`) is
correctly a single upstream gate called once per request at the router layer (`chat.py:90`,
`documents.py:226/551`, `search.py:63`, `projects.py:86`) — never inside a tool. Of the four tools,
`sql_agent.py` already has its own pre-execution check (`validate_select`, line 30); the other three
(`retrieval_agent`, `report_agent`, `project_agent`) trust whatever role/department/scope values the
planner passes them, with no independent re-check.

**SQL hardening (→ Phase 5).** `sql_guard.py` already does: `sqlparse`-based statement-type check
(SELECT-only), regex table-reference extraction against a hardcoded 8-table allowlist (`users`
deliberately excluded), a forbidden-keyword blocklist (DDL/DML + `pg_sleep`/`pg_read_file`/
`dblink`/`information_schema`), and outer-`LIMIT`-wrapping row capping (default 500,
`sql_agent.py:33`). Missing: query timeout, pre-execution cost/row estimate, column-level
permissions, retry-on-safe-failure, and full AST table extraction (current regex extractor can be
evaded by unusual whitespace/quoting/subqueries).

**Guardrails (→ Phase 6).** All six checks (length, prompt-injection, destructive-intent, scope,
PII, system-prompt-leak) are deterministic regex/keyword matching — no LLM/classifier calls
anywhere in `services/guardrails/`, matching the request's stated preference. Two real gaps:
(a) **no check ever scans retrieved document content** for embedded instructions before it reaches
Claude — guardrails only run on the user's own message; (b) `confidence_score()` is explicitly a
retrieval-relevance proxy (avg rerank score), not a faithfulness/hallucination check against the
generated reply — that already exists, but in the *evaluation* module (`generation_judge.py`), not
in the live guardrail pipeline.

**Observability (→ Phase 7).** Four independent, uncorrelated mechanisms exist today: (1)
`services/monitoring/metrics.py` — five in-process deques (latency/tokens/retrieval/ingestion/
guardrail-events), not persisted, not request-ID-keyed; (2) `services/monitoring/progress.py` — the
ingestion-stage tracker; (3) `GatewayUsageLogModel` — a real persisted Postgres table with
`request_id, tokens, latency_ms, cost_usd, tool_calls, documents_retrieved, decision`, written by
`gateway/usage_tracker.py`; (4) an ephemeral per-response `trace` list assembled in `chat.py` from
`GuardrailStep`s + the planner's own ad-hoc trace dicts, returned to the frontend but never
persisted. Critically, `GatewayUsageLogModel.request_id` is generated fresh inside
`planner.py::call_model` — it is *not* the same ID used anywhere else, so nothing joins
conversation → request → tool calls → retrieval → SQL → latencies into one record.

**Async ingestion (→ Phase 8).** Absent. Fully synchronous, confirmed above. Redis is already used
elsewhere (RBAC quotas, rate limiting, gateway cache) but not touched anywhere in the ingestion
path — there's no existing job/queue primitive to build on, just Redis itself as infrastructure.

**Document quality (→ Phase 9).** Mostly absent. No `ocr_confidence`, no duplicate-chunk detection,
no "requires review" state. What exists: a coarse `status` (`completed`/`degraded`) flipped when a
stage throws, MIME-signature validation, and `upload_logs` outcome tracking.

**Feedback loop (→ Phase 10).** Completely absent — confirmed zero matches for
`feedback|rating|thumbs|helpful` across every model and router.

**Memory (→ Phase 11).** Genuine, not a stub: `services/memory/store.py` does real
summarize-after-N-turns via a live Claude call, and `UserModel.preferences` (a JSONB blob) is
actively read every chat turn and injected into the system prompt (`planner.py:46-52`) — not
orphaned. What's missing is *structure*: preferences is one freeform dict, not typed facts/
task-context distinct from raw conversation.

**Domain agents (→ Phase 12).** None exist. Exactly four tools total
(`retrieval_agent`/`sql_agent`/`report_agent`/`project_agent`); no manufacturing-domain agent
scaffolding anywhere.

**Streaming (→ Phase 13).** Full plumbing exists and is unused — `gateway/streaming.py`,
`ClaudeGateway.stream()`, SSE formatting (`to_sse`) — but `stream()` has zero callers anywhere in
the codebase and `routers/chat.py` returns one blocking `ChatResponse`. This is the cheapest gap to
close: wiring, not building.

**Testing (→ Phase 14).** Extensive on RBAC/auth/guardrails/rate-limiting/state-machines (24 test
files: RBAC matrix, quota/concurrency fail-open, JWT, password hashing, upload MIME validation,
project lifecycle, approvals, SQL-guard allowlist). Zero coverage on RAG quality itself: no
chunking test, no citation-accuracy test, no document-versioning test, no known-question-against-
known-document retrieval test, no full end-to-end integration test (`test_chat_auth.py`'s own
docstring confirms no Qdrant/Redis/Claude fixtures exist in the suite), and only Redis-unavailable
failure-injection tests (no Postgres/Qdrant-down tests).

**RAG quality primitives (→ Phase 2).** Retrieval already does dense+BM25+Qdrant-native RRF fusion,
permission/department filtering, and reranking. Document versioning already flows end-to-end at the
storage layer (`lineage_id`/`version_number`/`previous_version_id`/`is_latest_version`, with
`latest_version_only` as a real retrieval filter). `parent_chunk_id` is stored and returned by
`hybrid_search` but never dereferenced — parent-child is a chunking-time artifact only, not
exploited at query time. Metadata filtering is narrower than the schema supports: `department`,
`project`, `author`, and `approval_status` all exist as populated `DocumentModel` columns but none
are exposed as retrieval filters (only `document_type`/`classification`/`language`/
`latest_version_only`/document IDs are). `services/classification/` is fully-implemented, confirmed
**dead code** — never called from the upload path; `DocumentModel.classification*` is always
written `NULL`. Query rewriting, multi-query retrieval, and context compression are all absent —
query formulation is entirely implicit inside the LLM's own tool-call arguments.

---

## 3. Missing / partial capabilities — consolidated gap list

| # | Gap | State |
|---|---|---|
| 1 | Query rewriting | Absent |
| 2 | Multi-query retrieval | Absent |
| 3 | Parent-child retrieval exploitation | Data exists, never used at query time |
| 4 | Context compression before Claude | Absent |
| 5 | Metadata filters: department/project/author/date/approval_status | Columns exist, not exposed as filters |
| 6 | "Active/approved" version distinction in retrieval | Column exists (`approval_status`), unused by retrieval |
| 7 | Citation accuracy, answer relevance, total latency, token/cost in eval | Absent from `EvalRunModel`/judge |
| 8 | Per-tool independent authorization (retrieval/report/project agents) | Absent (sql_agent has it) |
| 9 | SQL timeout, cost/row pre-check, column-level perms, retry-on-safe-failure | Absent |
| 10 | Retrieved-content guardrail (untrusted-instruction scanning) | Absent |
| 11 | Output faithfulness/hallucination check in the *live* guardrail pipeline | Exists only in offline eval, not live chat |
| 12 | Unified, persisted, request-ID-correlated trace | Absent (4 uncorrelated mechanisms) |
| 13 | Async/background ingestion | Absent (fully synchronous) |
| 14 | Ingestion quality signals (parse-success confidence, dup-chunk, review flag) | Absent |
| 15 | Feedback (helpful/not-helpful) | Absent |
| 16 | Structured memory (typed facts/task-context vs. freeform prefs) | Absent |
| 17 | Domain agents | Absent |
| 18 | Streaming to the client | Built, unwired |
| 19 | RAG-quality test coverage | Absent |
| 20 | classification/ dead code | Should be removed or wired, not left ambiguous |

---

## 4. Proposed architecture

Principle for every item below: **extend the existing chokepoint, don't create a parallel one.**
Every gap here has an obvious existing home — that's what makes this an enhancement plan, not a
rewrite.

### 4.1 RAG quality (gaps 1–6)

- **Query rewriting**: ✅ implemented as Phase 3B — see §13. Landed one layer up from this sketch:
  the rewrite (and its fallback) happens in `planner.py`'s `search_documents` tool, immediately
  before it calls the unmodified `retrieval_agent.search_documents()`/`search_with_reranking()` —
  simpler than threading it through `retrieval_agent.py` itself, and keeps `retrieval_agent.py`'s
  contract (already extended once, for Phase 3A) untouched by this phase.
- **Multi-query retrieval**: same file, `generate_query_variants(query, n) -> list[str]`, gated
  behind a difficulty heuristic or a config flag; fan the variants through the existing
  `hybrid_search` per-variant, then fuse with the same RRF logic `search.py` already implements
  (extract it to a reusable `rrf_fuse(result_sets)` helper rather than duplicating the formula).
- **Parent-child exploitation**: ✅ implemented as Phase 3A — see §12. Matches this sketch closely:
  `fetch_parent_context()` landed in `services/retrieval/search.py` exactly as proposed, gated by
  `settings.parent_child_retrieval_enabled`, attaching parent text as a separate `parent_context`
  field alongside the unchanged, still-precisely-cited child `text` — the "don't silently replace"
  requirement above is enforced by construction (`text` is never overwritten).
- **Context compression**: new `services/retrieval/compression.py::compress_hits(hits, query) ->
  hits`, deterministic-first (truncate to N tokens per hit, dedupe near-identical chunks) with an
  optional LLM-based trim behind a flag — must preserve `chunk_id`/`document_id`/citation numbering
  exactly as-is; this is a text-shortening step, not a re-ranking step.
- **Metadata filters**: extend `SearchFilters` (`routers/search.py`) and
  `resolve_document_ids()`/`metadata_filter.py` with `department`, `project`, `author`,
  `date_from`/`date_to`, `approval_status` — all already-populated columns, this is pure plumbing,
  no new data needed. Mirror the same filters onto `GET /documents` query params so the frontend
  Documents page can filter without a schema change.
- **Version-state retrieval**: add `approval_status` as a first-class retrieval filter next to the
  existing `latest_version_only`, defaulting to `approved` unless the caller (role-gated) requests
  otherwise.

### 4.2 Evaluation completeness (gap 7) — ✅ implemented, see §11

**Implemented differently than originally sketched here.** Rather than two new modules each
issuing their own Claude call, citation accuracy and answer relevance were folded into the
*existing* `generation_judge.py` call (a new `judge_agent_v2.yaml` prompt version) — it already
receives the question, numbered sources, and answer, so scoring two more dimensions is zero
additional latency/cost. Token/cost/model were sourced from the existing `gateway_usage_logs` audit
trail (via a shared `request_id` threaded through `run_agent()` and `judge_answer()`), not
re-derived. Full detail and rationale: `docs/RAG_EVALUATION.md`.

### 4.3 Tool-level authorization (gap 8)

Do **not** duplicate `authorize_llm_request`. Add one small reusable guard,
`services/llm_rbac/tool_guard.py::verify_tool_access(decision: PolicyDecision, *, tool: str,
department: str | None = None, table: str | None = None)`, that re-checks the already-computed
`PolicyDecision` (tool in `allowed_tools`, department in `knowledge_departments`, table in
`sql_allowed_tables`) and raises `AppError(403)` on mismatch. Call it at the top of
`retrieval_agent.search_documents`, `report_agent.generate_report`, and `project_agent.list_my_projects`
— mirroring what `sql_agent.py` already does inline with `validate_select`. This is defense-in-depth
against a future second entrypoint reaching these tools with a stale/forged decision, not a new
policy source.

### 4.4 SQL hardening (gap 9)

In `sql_agent.py`: wrap execution with a statement-level timeout
(`SET LOCAL statement_timeout` on the connection before executing, reverted after — Postgres-native,
no new dependency), add a pre-execution `EXPLAIN` row-estimate check against a configurable ceiling
(`settings.sql_agent_max_estimated_rows`) that rejects before running, and on a `SqlGuardError`
allow exactly one Claude-driven retry with the validation error fed back as tool-result context
(reuses the existing ReAct loop — the planner already re-invokes the model with tool output, so
"retry" is just returning a corrective error message instead of a terminal one). Column-level
permissions: only if a concrete need surfaces — the schema has no per-column ACL data source today
and inventing one is scope creep the request itself warns against ("do not add components simply
because they are popular").

### 4.5 Guardrail expansion (gaps 10–11)

- **Retrieved-content guardrail**: new deterministic check,
  `services/guardrails/retrieved_content.py::scan_retrieved_text(hits) -> list[GuardrailStep]`,
  reusing the *existing* injection-pattern list from `injection.py` (don't fork a second pattern
  set) applied to hit text instead of user input, called from `retrieval_agent.py` right after
  `search_with_reranking` returns and before results go into the tool's return value. A match
  redacts/flags that specific chunk (doesn't block the whole turn) and is recorded as a
  `GuardrailStep` the same way every other check already is — this is the direct fix for "retrieved
  documents are untrusted data" being unenforced today.
- **Live faithfulness check**: do not port the full LLM-judge into the hot path (cost/latency). Add
  a cheap deterministic **claim-support heuristic** (do the sentences near each `[n]` marker share
  n-gram/embedding overlap with that chunk's text) as a real-time signal in `citation_rail.py`,
  and reserve the expensive LLM-judge faithfulness check for evaluation runs and an optional
  admin-triggered "audit this response" action — not every chat turn.

### 4.6 Observability (gap 12)

Add one new table, `RequestTraceModel` (`request_traces`): `id, request_id (idx), user_id, role,
endpoint, rbac_decision (JSONB), guardrail_events (JSONB), tool_calls (JSONB), retrieval_queries
(JSONB), retrieved_document_ids (JSONB), sql_generated (text?), latencies (JSONB: retrieval_ms/
generation_ms/total_ms), error (text?), fallback_used (bool), confidence (str?), citations (JSONB),
created_at`. Generate **one** `request_id` at the top of `chat.py`'s handler (not inside
`planner.py::call_model` as today) and thread it through `run_agent`, `usage_tracker.record_usage`,
and the guardrail pipeline so all four currently-uncorrelated mechanisms finally share a join key.
Write the row once, at the end of the request, from data already being assembled for the response
`trace` — this is consolidation, not a new data-collection surface. Expose `GET /admin/traces/{request_id}`
reusing the existing `require_role(Role.ADMIN)` pattern from `admin.py`.

### 4.7 Async ingestion (gap 13)

Reuse Redis (already core infra) as a simple work queue rather than adding Celery/RQ/a new broker —
consistent with "do not introduce unnecessary dependencies." `POST /documents/upload` becomes:
validate MIME synchronously (cheap, fail fast) → create a `DocumentModel` row with
`status="queued"` → push a job (`document_id`) onto a Redis list → return `202` immediately with the
document ID. A background worker (a second process, `python -m app.workers.ingestion_worker`,
`BLPOP`-ing the same list) runs the existing parse→chunk→embed→index pipeline unchanged, just no
longer inline in the request. `GET /documents/{id}/progress` (already exists) becomes the only
polling surface the frontend needs — no new endpoint required, just a new status vocabulary:
`uploaded → validating → parsing → chunking → embedding → indexing → completed | failed`, stored as
`DocumentModel.status`. Keep the current synchronous path available behind a flag
(`settings.async_ingestion_enabled`, default off initially) so this is opt-in, not a breaking
change to Docker Compose (which doesn't run a worker process today).

### 4.8 Document quality (gap 14)

Add columns to `DocumentModel` (see §6): `parse_confidence` (float, e.g. extracted-text-chars ÷
expected-chars-per-page heuristic), `duplicate_chunk_count` (int, computed via existing chunk-text
hashing during chunk persistence), `requires_review` (bool). Set `requires_review=True` instead of
`status="completed"` when `parse_confidence` is below a configurable threshold
(`settings.parse_confidence_review_threshold`) — surfaced in the Documents frontend page as a
distinct badge state next to the existing status badges.

### 4.9 Feedback loop (gap 15)

New `FeedbackModel` (`message_feedback`): `id, message_id (FK→messages), user_id (FK→users),
rating ("helpful"|"not_helpful"), created_at`. New endpoint `POST /messages/{id}/feedback` on
`conversations.py` (or a new small `feedback.py` router). Frontend: two small buttons under each
assistant `st.chat_message` in `views/chat.py`. An analytics view (new `Admin` tab, reusing the
existing `explorable_table` pattern from `components.py`) showing feedback rate by
guardrail-event/tool/model-tier — joined against `RequestTraceModel` once §4.6 exists, so
"common failure categories" has real dimensions to group by. No auto-fine-tuning, per the request.

### 4.10 Structured memory (gap 16)

Add one JSONB column, `UserModel.structured_facts` (or a small `user_facts` table if per-fact
querying is ever needed — start with JSONB, matching the existing `preferences` pattern, to avoid
over-building), holding `{"facts": [...], "task_context": {...}}` distinct from the freeform
`preferences` blob. Populate it opportunistically from `memory/store.py::maybe_summarize`'s existing
Claude call (ask it to also extract durable facts, not just summarize) rather than a second LLM
call. RBAC/privacy: this is already scoped per-`user_id` exactly like `preferences` is today — no
new access-control surface needed, just don't ever key it by anything other than the owning user's ID.

### 4.11 Domain agents (gap 17 — explicitly last, per the request)

Not proposed for immediate implementation. If/when a concrete manufacturing use case is confirmed,
new agents go in `services/agents/{production,maintenance,quality}_agent.py` following the exact
shape `project_agent.py` already establishes (a `@tool`-decorated function taking `role`/
`knowledge_departments`/`user_id`, registered in `planner.py::_build_tools` behind an
`allowed_tools` check) — no new infrastructure, no separate gateway/RBAC/guardrail path per agent.

### 4.12 Streaming (gap 18)

Cheapest gap in the plan — the plumbing exists. Add `POST /chat/stream` (new route, keep
`POST /chat` for non-streaming callers) that calls `claude_gateway.stream()` for the final-answer
generation step only (not the tool-selection loop, which needs complete responses to parse tool
calls) and emits SSE events for coarse stage markers (`planning`, `searching`, `generating`) plus
token deltas for the final answer — never raw tool arguments, system prompt, or chain-of-thought,
per the request's explicit constraint. Frontend: `frontend/app/api_client.py` gets a
`stream_chat_message()` using `requests`' streaming mode; `views/chat.py` swaps `st.markdown` for
incremental `st.write_stream`-style updates on the final answer only.

### 4.13 Testing (gap 19) — see §9.

### 4.14 classification/ decision (gap 20) — investigated during Phase 2, left untouched

Revised after closer investigation than §4.14 originally had (that version recommended wiring it
in; this supersedes it). Findings:

- **Why it was removed**: confirmed via `WORKFLOW.md` ("removed for latency") and corroborated by
  `ARCHITECTURE.md`, which documents the *original* design — classification was meant to drive
  chunking strategy ("parent/child chunking for Manuals and SOPs, semantic for Research Papers…").
  The current chunking dispatcher no longer reads classification at all; it picks a strategy from
  document *format* + structure (presence of headings) instead. So this wasn't only a latency
  optimization — the architecture moved away from classification-driven chunking to a simpler,
  more reliable format-driven one, and the now-orphaned classification call was left disconnected
  rather than deleted outright.
- **Measurable value today**: none for evaluation (this phase doesn't touch document metadata).
  Real, but *future* value for retrieval metadata filtering (§4.1) — `document_type` is already
  exposed as a search filter, and a populated `classification` label would make that filter
  meaningfully more useful than the mostly-empty `document_type` field currently is.
- **Latency/cost**: real. `classify()`'s zero-shot fallback loads and runs
  `MoritzLaurer/deberta-v3-xsmall-zeroshot-v1.1-all-33`, a distinct transformer model, inside the
  *already-synchronous* upload request (§4.7/Phase 8 async ingestion hasn't landed) — on top of the
  four models ingestion already runs inline (embedding, NER, reranker warm-up, Docling). Wiring it
  in now would make an already-known pain point (synchronous, multi-model upload) measurably worse.
- **Needed by upcoming metadata-filtering work?** Not yet — that work (§4.1) hasn't started. When it
  does, wiring classification back in (behind a feature flag, and ideally after async ingestion
  §4.7 lands so its cost no longer sits in the request path) is worth revisiting.

**Decision: leave untouched.** Not clearly justified *now* — the latency cost is real and
immediate, the value is real but not needed until a not-yet-started phase. Revisit when either §4.1
(metadata filtering) or §4.7 (async ingestion) is scheduled.

---

## 5. Files that will change

| Area | Files |
|---|---|
| Retrieval | `services/retrieval/search.py`, `metadata_filter.py`, `services/agents/retrieval_agent.py`, `routers/search.py`, `routers/documents.py` (list filters) |
| SQL | `services/agents/sql_agent.py` |
| Guardrails | `services/guardrails/pipeline.py`, `citation_rail.py` |
| Gateway/observability | `gateway/usage_tracker.py`, `routers/chat.py`, `services/agents/planner.py` (request_id threading) |
| Ingestion | `routers/documents.py`, `services/chunking/persistence.py` (dup-chunk detection) |
| Memory | `services/memory/store.py` |
| Chat/streaming | `routers/chat.py`, `frontend/app/api_client.py`, `frontend/app/views/chat.py` |
| Evaluation | `services/evaluation/runner.py`, `routers/evaluation.py` |
| Admin frontend | `frontend/app/views/admin.py` (traces, feedback analytics), `frontend/app/views/documents.py` (quality badges, filters) |
| Config | `backend/app/core/config.py` |

## 6. New files required

```
backend/app/services/retrieval/query_rewrite.py
backend/app/services/retrieval/compression.py
backend/app/services/llm_rbac/tool_guard.py
backend/app/services/guardrails/retrieved_content.py
backend/app/services/evaluation/citation_accuracy.py
backend/app/services/evaluation/answer_relevance.py
backend/app/models/request_trace.py
backend/app/models/feedback.py
backend/app/routers/feedback.py            # or fold into conversations.py
backend/app/workers/ingestion_worker.py    # only if Phase 8 (async ingestion) proceeds
backend/tests/rag/test_chunking.py
backend/tests/rag/test_retrieval_quality.py
backend/tests/rag/test_versioning.py
backend/tests/integration/test_chat_e2e.py
backend/tests/security/test_prompt_injection.py
backend/tests/security/test_sql_injection.py
backend/tests/failure/test_dependency_unavailable.py
```

## 7. Database changes

All additive — no renames, no drops, consistent with the existing `create_all()` +
`_run_light_migrations()` pattern (§2 confirmed this supports clean additive changes cleanly).

- `documents`: `+parse_confidence float?`, `+duplicate_chunk_count int?`, `+requires_review bool
  def false` (Phase 9)
- `users`: `+structured_facts jsonb?` (Phase 11)
- New table `request_traces` (Phase 7, see §4.6 for full column list)
- New table `message_feedback` (Phase 10, see §4.9)
- New table `eval_runs` gets `+tokens_used int?`, `+cost_usd float?`, `+total_latency_ms float?`
  (Phase 3)

## 8. Configuration changes

All new behavior gated in `backend/app/core/config.py`, matching the existing flat-`Settings` +
`.env`-override pattern already used for `guardrails_enabled`/`rate_limit_enabled`/etc. — no new
config system:

```
query_rewrite_enabled: bool = False
multi_query_enabled: bool = False
parent_context_enabled: bool = False
context_compression_enabled: bool = False
retrieved_content_guardrail_enabled: bool = True
sql_agent_timeout_seconds: float = 10
sql_agent_max_estimated_rows: int = 50000
parse_confidence_review_threshold: float = 0.3
async_ingestion_enabled: bool = False
streaming_enabled: bool = False
```

## 9. Testing strategy

Per phase, add tests **before or alongside** the code, not after:

- Every new deterministic function (query rewrite fallback, compression, dup-chunk detection,
  parse-confidence scoring, tool_guard) gets a focused unit test — same style as existing
  `tests/llm_rbac/*` and `tests/guardrails/*`.
- `backend/tests/rag/` (new): chunking-strategy correctness, a small fixed known-question →
  known-chunk retrieval fixture (the audit confirmed zero coverage here today — this is the single
  highest-value testing gap), document-versioning filter behavior.
- `backend/tests/integration/test_chat_e2e.py` (new): the first real Qdrant+Postgres+Redis-backed
  round trip through the actual planner — requires test fixtures/containers that don't exist yet;
  scope this to a small, fast subset (one document, one query) rather than a full stack smoke test.
- `backend/tests/security/`: prompt injection (existing `injection.py` patterns tested directly),
  retrieved-content injection (new, once §4.5 lands), unauthorized document/tool/table access
  (extend existing `test_documents_rbac.py`/`test_rbac.py` patterns rather than new files where
  those files already cover the surface).
- `backend/tests/failure/`: Postgres/Qdrant-unavailable tests are the confirmed gap (only Redis is
  tested today) — mock the client layer the way `tests/gateway/test_cache.py` already mocks Redis.
- Every phase's rollout (§10) ends with `pytest backend/tests` green before moving to the next
  phase — no phase merges with red tests.

## 10. Rollout order

Matches the request's specified implementation order exactly, with the audit's findings folded in
(skipping "build" work for anything already substantially present, per "do not duplicate existing
functionality"):

1. ~~Repository audit~~ — this document.
2. Evaluation completeness (§4.2) — smallest, lowest-risk, builds on infrastructure that already
   works end-to-end.
3. Tool-level authorization (§4.3) — small, high-value, no schema changes.
4. SQL hardening (§4.4) — small, no schema changes.
5. Guardrail expansion (§4.5) — retrieved-content scanning is the most important single security
   gap this audit found.
6. Observability (§4.6) — do this *before* Phases 7–10 below so async ingestion, feedback, and
   RAG-quality work all land with request-tracing already in place to debug them with.
7. Async ingestion (§4.7) — largest structural change in this plan; ships behind a default-off flag.
8. Document quality/versioning filters (§4.1 metadata filters + §4.8) — natural pairing, same files.
9. Feedback loop (§4.9) — depends on §4.6's trace table for the analytics view to be useful.
10. Memory improvements (§4.10) — independent, can slot in anytime after step 5.
11. Domain agents (§4.11) — explicitly deferred, no concrete use case confirmed yet.
12. Streaming (§4.12) — cheap, but sequenced late so it doesn't complicate debugging the bigger
    changes above while they land.
13. Frontend improvements — incremental alongside each backend phase above (traces in Admin after
    step 6, quality badges in Documents after step 8, feedback buttons in Chat after step 9), not a
    separate final pass.
14. Full testing pass (§9) — continuous per-phase, plus a final security/failure-injection sweep.

Each phase, per the request's own rule: run tests, inspect errors, fix regressions, update docs,
report which files changed and why — before starting the next phase.

---

## 11. Phase 2 implementation report

Scope: evaluation completeness only (citation accuracy, answer relevance, total latency,
token/cost/model). No retrieval-architecture, tool-authorization, SQL, guardrail, or async-ingestion
changes — none of those were touched, per the request's explicit constraints. Full design rationale:
`docs/RAG_EVALUATION.md`.

**Design deviation from §4.2's original sketch**: citation accuracy and answer relevance were
folded into the existing `generation_judge.py` judge call (new prompt version) instead of two new
modules issuing their own Claude calls — discovered during pre-implementation inspection that the
existing call already receives everything needed to score both. Zero additional Claude calls, zero
additional latency/cost versus the plan as originally written.

### Files changed

- `backend/app/services/evaluation/generation_judge.py` — `v1`→`v2` prompt, parses
  `citation_accuracy`/`answer_relevance`, accepts optional `request_id`.
- `backend/app/services/evaluation/runner.py` — generates one `request_id` shared across the
  run's Claude calls, times the judge stage, sums `total_latency_ms`, reads token/cost/model back
  from `gateway_usage_logs`.
- `backend/app/services/agents/planner.py` — `run_agent()` gained an optional `request_id` param
  (default `None`; production `chat.py` call sites unaffected — same fresh-id-per-turn behavior as
  before when omitted).
- `backend/app/models/eval_run.py` — 7 new nullable columns (see below).
- `backend/app/db/postgres.py` — matching `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` entries in
  `_run_light_migrations()`.
- `backend/app/routers/evaluation.py` — `EvalRunResponse`/`EvalSummaryResponse` extended;
  `_run_to_response()` computes `total_tokens`; `eval_summary()` adds averages + `total_cost_usd`.
- `frontend/app/views/evaluation.py` — Summary tab: 2 new metric rows (8 cards) for the new
  per-run metrics, plus `avg_generation_latency_ms` (existed in the API since before this phase but
  was never rendered — added while touching this block). Run success message includes the two new
  scores.

### Files created

- `backend/prompts/judge_agent_v2.yaml` — new prompt version (v1 untouched, per existing
  don't-edit-in-place convention).
- `backend/tests/evaluation/__init__.py`, `test_generation_judge.py`, `test_runner.py`,
  `test_evaluation_router.py` — 18 new tests.
- `docs/RAG_EVALUATION.md` — evaluation system documentation.

### Database changes

Additive only, applied via the existing `create_all()` + light-migration pattern (no Alembic, no
renames/drops). `eval_runs` gains: `citation_accuracy FLOAT`, `answer_relevance FLOAT`,
`total_latency_ms FLOAT`, `tokens_input INTEGER`, `tokens_output INTEGER`, `cost_usd FLOAT`,
`model VARCHAR(64)` — all nullable, so every pre-existing row remains valid with these `null`.
Verified against the live dev database: migration applies cleanly, insert/read round-trip confirmed
for all 7 new columns.

### APIs changed

- `EvalRunResponse` (`GET /eval/runs`, `POST /eval/queries/{id}/run`): +`citation_accuracy`,
  `answer_relevance`, `total_latency_ms`, `tokens_input`, `tokens_output`, `total_tokens` (computed),
  `cost_usd`, `model`. All existing fields unchanged.
- `EvalSummaryResponse` (`GET /eval/summary`): +`avg_citation_accuracy`, `avg_answer_relevance`,
  `avg_total_latency_ms`, `avg_tokens_input`, `avg_tokens_output`, `avg_cost_usd`, `total_cost_usd`.
  All existing fields unchanged.
- No breaking changes — every new field is additive and nullable; existing clients ignoring unknown
  fields are unaffected.

### Tests

18 new tests across 3 files (`generation_judge` prompt parsing + request_id threading; `runner`
token/cost aggregation, total-latency computation, judge-failure resilience, unchanged
recall/precision/MRR regression guard; router response-shape/summary-aggregation compatibility).
**276/276 passed** (258 pre-existing + 18 new), zero regressions. Also manually verified against a
live Postgres (migration + insert/read round-trip) and a live Streamlit render of the updated
Summary tab (16 metric cards, correct formatting, no layout overflow).

Not covered (see §9's original testing-strategy gaps, still open): no test exercises the real
`run_agent()` → real multiple-tool-loop-turn → shared-`request_id` path end-to-end against a live
Claude/Postgres stack — the `request_id`-threading change in `planner.py` is a 2-line, backward-
compatible addition verified indirectly (via `runner.py`'s tests, which mock `run_agent` itself) and
by code review, not a dedicated unit test, since the existing suite has no infrastructure for a real
planner-loop test (confirmed absent in the Phase 1 audit).

### Classification decision

Investigated, **left untouched** — see §4.14 for full reasoning. Summary: removal was a deliberate
architecture simplification (chunking moved from classification-driven to format-driven), not only
a latency shortcut; re-enabling it now would add a real, immediate latency cost to an already-
synchronous upload path for a benefit (better metadata filtering) that isn't needed until a
not-yet-started future phase.

### Remaining gaps

- No per-citation breakdown (one `citation_accuracy` score per answer, not per `[n]` marker).
- `total_latency_ms` measures full evaluation time (including the judge), not production chat-turn
  latency.
- Evaluation still requires hand-curated `expected_chunk_ids` for retrieval-ranking metrics.
- `request_id` threading exists only for the evaluation path; production `chat.py` doesn't yet
  generate one shared id per request (that's Phase 7/observability's `RequestTraceModel` work, §4.6
  — explicitly out of scope here and not started).

**Stopped after Phase 2 per the request — did not proceed to Phase 3 automatically; resumed only on
explicit instruction.**

---

## 12. Phase 3A implementation report

Scope: parent-child retrieval only. Query rewriting (3B) and multi-query retrieval (3C) not started
— both explicitly require separate approval per the request. No retrieval architecture, ranking,
tool-authorization, SQL, or guardrail changes. Full design/rationale: `docs/RAG_RETRIEVAL.md`.

### Files changed

- `backend/app/services/retrieval/search.py` — new `fetch_parent_context()`.
- `backend/app/services/agents/retrieval_agent.py` — calls it behind the feature flag, attaches
  `parent_context` to hits that have one.
- `backend/app/core/config.py` — 3 new settings (below).
- `backend/app/services/agents/planner.py` — `planner_agent` prompt bumped `v1`→`v2` (see below).

### Files created

- `backend/prompts/planner_agent_v2.yaml` — new prompt version (v1 untouched) explaining
  `parent_context` to Claude: background only, never a separate citable source.
- `backend/tests/retrieval/{__init__,test_parent_context}.py`, `backend/tests/test_retrieval_agent.py`
  — 22 new tests.
- `docs/RAG_RETRIEVAL.md`.

### Database changes

None. Phase 3A reads an existing column (`ChunkModel.parent_chunk_id`) that was already populated —
no new tables, no new columns.

### API changes

`retrieval_agent.search_documents()`'s returned dicts (consumed by the chat planner's
`search_documents` tool — internal, not a REST response model) gain an optional `parent_context`
key when the flag is on and a hit has a parent. No REST endpoint schema changed — `POST /search` was
deliberately not touched (see `docs/RAG_RETRIEVAL.md`'s "why not `/search` too").

### Configuration changes

```
parent_child_retrieval_enabled: bool = False
parent_context_max_expansions: int = 5
parent_context_max_chars: int = 2000
```

### Tests

22 new tests: child→parent resolution, missing parent, duplicate-parent dedup, max-expansions
limiting (with a zero-expansions case asserting no DB query happens at all), max-chars truncation,
flag-on/off attachment behavior, citation-text-unchanged-when-parent-context-present, and
disabled-mode output-shape parity with the pre-Phase-3A contract. **290/290 passing** (268
pre-existing + 22 new), zero regressions.

### Evaluation results

Real evaluation run against this environment's actual Qdrant/Postgres data (not simulated) — see
`docs/RAG_RETRIEVAL.md` for full detail and the exact query used.

- **Retrieval-ranking metrics (Recall@5, Precision@5, MRR, NDCG@5) — measured, identical baseline vs.
  Phase 3A**: 1.0 / 0.4 / 0.333 / 0.544 in both runs, same retrieved-chunk-ID set and order. This
  confirms, with real data, that parent-context enrichment does not alter which chunks are
  retrieved or how they rank — exactly the required property.
- **Generation-quality and cost metrics (groundedness, faithfulness, hallucination rate, citation
  accuracy, answer relevance, tokens, cost) — NOT measured.** This environment's
  `ANTHROPIC_API_KEY` is rejected by Anthropic's API (`401 authentication_error`), confirmed with a
  direct minimal SDK call outside any of this phase's code, ruling out a bug in the implementation.
  Both real evaluation runs' generation/judge stages failed for this reason; their result fields are
  `null`, not fabricated placeholder numbers. **No latency/token/cost comparison number in this
  report is invented** — the "expected" direction of effect (more input tokens, hypothesized
  groundedness/faithfulness improvement on terse-child-chunk cases) is stated in
  `docs/RAG_RETRIEVAL.md` explicitly as an unverified hypothesis, not a result.

### Recommendation

The retrieval-layer change is implemented, tested, and confirmed (with real data) to leave ranking
metrics unaffected — safe to ship behind its default-off flag on that basis alone. The half of the
evaluation this phase was specifically designed to answer objectively — does parent context actually
improve answer quality, and by how much does it cost in tokens/latency — remains unverified pending
a working Anthropic API key in this environment. Recommend keeping
`parent_child_retrieval_enabled=False` in any shared/deployed environment until that comparison can
actually be run for real.

### Remaining Phase 3 work

- Phase 3B (query rewriting) and Phase 3C (multi-query retrieval) — not started, per the request's
  explicit requirement that each sub-phase be separately approved after the prior one's evaluation.
- Re-running Phase 3A's generation-quality evaluation once a working API key is available — the
  single highest-priority follow-up, since it's the one piece of this phase's own success criteria
  that couldn't be completed here.

**Stopped after Phase 3A per the request — did not proceed to Phase 3B automatically; resumed only
on explicit instruction and approval.**

---

## 13. Phase 3B implementation report

Scope: query rewriting only, experimental and disabled by default. Multi-query retrieval (3C),
domain agents, async ingestion, and tool-level authorization explicitly not touched, per the
request's constraints. Parent-child retrieval (3A) not modified further. Full design/rationale:
`docs/RAG_RETRIEVAL.md`.

### Files changed

- `backend/app/core/config.py` — 4 new settings (below).
- `backend/app/services/agents/planner.py` — new `_maybe_rewrite_query()` helper; `search_documents`
  tool calls it before retrieval; `_build_tools()`/`run_agent()` thread `conversation_summary` and
  `request_id` through (the latter already existed for Phase 2's evaluation aggregation — reused,
  not duplicated). The planner's own prompt (`planner_agent`) stays at `v2` (set in Phase 3A) — Phase
  3B needed no change there, since the rewrite happens entirely between the tool call and retrieval;
  Claude never sees that a rewrite occurred.

### Files created

- `backend/app/services/retrieval/query_rewrite.py` — `rewrite_query()` + `RewriteOutcome`.
- `backend/prompts/query_rewrite_agent_v1.yaml` — new, dedicated prompt.
- `backend/tests/retrieval/test_query_rewrite.py`, `backend/tests/test_planner_query_rewrite.py` —
  44 new tests.
- `docs/RAG_RETRIEVAL.md` §"Phase 3B" (updated in place, was a stub).

### Database changes

None.

### API changes

None. `retrieval_agent.search_documents()`'s contract is unchanged by this phase (only Phase 3A
touched it, adding `parent_context`). The rewrite is entirely internal to the planner's tool
execution — nothing in `POST /chat`'s request/response shape changed.

### Configuration changes

```
query_rewriting_enabled: bool = False
query_rewrite_max_chars: int = 300
query_rewrite_timeout_seconds: float = 5.0
query_rewrite_tier: str = "fast"
```

### Tests

44 new tests covering every case the request listed: successful rewrite, original-query
preservation, rewrite failure/timeout/malformed-response fallback, maximum rewrite length,
conversation-context usage, RBAC preservation, metadata-filter preservation, disabled-mode
behavior, and gateway usage tracking (via `claude_gateway.generate()`'s existing auto-recording,
verified by asserting the correct `agent_name`/`request_id` reach the gateway call). **312/312
passing** (290 pre-existing + 22 rewrite-module tests), zero regressions.

### Evaluation results

- **Retrieval-only comparison**: run for real, but not meaningfully comparable to a "rewriting
  worked" condition — see `docs/RAG_RETRIEVAL.md` for why (every rewrite attempt fails against this
  environment's invalid key, so "on" and "off" both retrieve with the original query). What *is*
  real: `query_rewriting_enabled=True` against the live Qdrant/Postgres data, same eval query as
  Phase 3A, produced Recall@5=1.0/Precision@5=0.4/MRR=0.333/NDCG@5=0.544 with the identical retrieved
  chunk set/order as the Phase 3A baseline — confirming the fallback path works under a **real**
  failure, not a mock.
- **Generation evaluation**: not run — `ANTHROPIC_API_KEY` is still invalid (unchanged from Phase
  3A; not modified, per instruction). No generation-quality or cost/token number is reported,
  estimated, or invented for this phase.
- **Latency/token/cost differences**: not measurable for the same reason. The comparison
  infrastructure is in place (toggle `query_rewriting_enabled`, run `run_evaluation()`, read
  `tokens_input`/`tokens_output`/`cost_usd`/`total_latency_ms` off the resulting `EvalRunModel` row —
  identical mechanism Phase 2 built) and ready to produce real numbers the moment a working key
  exists.

### Recommendation

Ship disabled (`query_rewriting_enabled=False`, the default) in any shared/deployed environment.
Implementation is tested and its safety net (fallback under real failure) is confirmed; its actual
retrieval/generation benefit is completely unverified and cannot be until a working Anthropic API key
is available.

### Remaining Phase 3 work

- Phase 3C (multi-query retrieval) — not started, awaiting separate approval per the request.
- Re-running both Phase 3A's and Phase 3B's generation-quality evaluations once a working API key is
  available remains the single highest-priority follow-up across all of Phase 3.

**Stopping here per the request — not proceeding to Phase 3C automatically.**

---

## 14. Phase 3 Evaluation Gate report

Scope: build a controlled experiment runner comparing Baseline / Parent-Child / Query-Rewrite /
Combined configurations, using config overrides only (never a permanent settings change), and produce
an evidence-based recommendation for whether Phase 3A/3B should be enabled and whether Phase 3C should
start. Explicitly not permitted, and not done: modifying parent-child or query-rewriting logic,
enabling either feature globally, touching RBAC/guardrails/SQL agent, implementing Phase 3C or any
other new capability, or inventing/estimating any unavailable metric. Full design and results:
`docs/RAG_RETRIEVAL.md` §"Phase 3 Evaluation Results".

### Files changed

- `backend/app/models/eval_run.py` — one new nullable column, `experiment_label`.
- `backend/app/db/postgres.py` — one additive migration adding that column plus an index, appended to
  the existing `_run_light_migrations()` list (applied to the live DB).
- `backend/app/routers/evaluation.py` — `experiment_label` added to `EvalRunResponse`; new request/
  response schemas and a new `POST /eval/experiments/run` endpoint.
- `frontend/app/api_client.py` — new `run_experiment_gate()` client function.
- `frontend/app/views/evaluation.py` — new "Experiments" tab (4th tab; existing Eval queries/Runs/
  Summary tabs unchanged) rendering the Metric | Baseline | Parent-Child | Query-Rewrite comparison
  table, per-question paired deltas, and the recommendation with its reasons.
- `docs/RAG_RETRIEVAL.md` — new "Phase 3 Evaluation Results" section; correction notes added to the
  existing Phase 3A and Phase 3B sections (see "A discovered limitation" below).

### Files created

- `backend/app/services/evaluation/experiments.py` — `ExperimentConfig`/`BASELINE`/`PARENT_CHILD`/
  `QUERY_REWRITE`/`COMBINED`, `_temporary_flags()` (scoped settings override, restores on exception),
  `run_experiment()`, `compare()`, `paired_comparison()`, `generation_availability()`, `_recommend()`,
  `build_feature_report()`, `run_gate()`. Uses `run_evaluation()` (Phase 2) completely unmodified.
- `backend/tests/evaluation/test_experiments.py` — 29 new tests.

### Database changes

```sql
ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS experiment_label VARCHAR(32);
CREATE INDEX IF NOT EXISTS ix_eval_runs_experiment_label ON eval_runs (experiment_label);
```

Applied to the live database. No other schema change.

### Configuration changes

None permanent. `settings.parent_child_retrieval_enabled`/`settings.query_rewriting_enabled` remain
`False` by default; `experiments.py` only ever changes them inside `_temporary_flags()`'s `with` block,
restoring the exact prior value in a `finally`, verified by dedicated tests including an
exception-safety case and a case that starts from `True` (not just the default `False`) to prove it
restores the actual prior value rather than hardcoding `False`.

### Tests

29 new tests in `test_experiments.py`, covering every item the request listed: experiment configuration
values, flag isolation (set/restore, restore-on-exception, restores-true-not-just-False), no permanent
mutation across repeated experiment runs, per-run `experiment_label` tagging, `compare()`'s averaging/
delta/delta-percent math and its unavailable-never-becomes-zero handling, `paired_comparison()`'s
improved/degraded/unchanged/skipped-unavailable counting, `generation_availability()`'s 401/
`authentication_error` detection (all-failed / partially-failed / non-auth-failure / all-available
cases), `_recommend()`'s every branch (insufficient evidence for small dataset, insufficient evidence
for no measurable quality metric, recommend enable, keep disabled for a degraded metric, keep disabled
for quality-improved-but-cost-increased), and `run_gate()`'s sequencing/flag-per-call correctness,
combined-has-no-recommendation, and default experiment selection. **370/370 passing** (341 pre-existing
+ 29 new), zero regressions. Full command and result in "Final test run" below.

### Real evaluation run

Executed for real three times against this environment's live Qdrant/Postgres data while building this
gate (final run: `k=10`, all three experiments plus combined, called directly through
`experiments.run_gate()`) — not simulated, not mocked. Full numbers, tables, and methodology are in
`docs/RAG_RETRIEVAL.md`; summary:

- **Retrieval metrics** (Recall@10/Precision@10/MRR/NDCG@10): bit-for-bit identical across all four
  conditions (Baseline/Parent-Child/Query-Rewrite/Combined), every run performed.
- **Generation-quality/cost/token metrics**: unavailable on every run —
  `"Generation evaluation unavailable: Anthropic authentication failed."` (HTTP 401, pre-existing,
  confirmed independently of this phase's code, not modified/bypassed here).
- **Latency**: baseline read ~40% slower than the other conditions in the final run — traced to a
  one-time model-load cost baseline paid running first in a fresh process, not a real effect of either
  feature (see `docs/RAG_RETRIEVAL.md` for the full explanation).
- **Recommendation**: **INSUFFICIENT EVIDENCE** for both `parent_child_retrieval_enabled` and
  `query_rewriting_enabled` — the 2-query dataset is below this gate's 5-query minimum for a verdict,
  and no generation-quality metric was measurable. Combined intentionally has no independent
  recommendation.

### A discovered limitation — must be read alongside Phase 3A/3B's own "Evaluation results" sections

While building this gate, two structural facts came to light that materially change how §12's and
§13's "Evaluation results" subsections (and this gate's own retrieval-metric comparison) should be
interpreted — correction notes have been added directly to those subsections and to
`docs/RAG_RETRIEVAL.md`, rather than only mentioned here:

1. `services/evaluation/runner.py::run_evaluation()` computes Recall/Precision/MRR/NDCG from a direct
   call to `search_with_reranking()`, which never passes through `retrieval_agent.search_documents()`
   (where `fetch_parent_context()`, Phase 3A's code, lives) or the planner's `search_documents` tool
   (where `_maybe_rewrite_query()`, Phase 3B's code, lives). This is unconditional — those four metrics
   cannot reflect either feature's effect, regardless of flag value, by construction. "Identical
   retrieval metrics across conditions" was never capable of being anything else.
2. In this environment specifically, `run_agent()`'s first LLM call (the planner's own routing
   decision, before any tool-call decision) fails immediately on the invalid API key — so the
   `search_documents` tool, and therefore both Phase 3A's and Phase 3B's actual code, never executed
   during **any** real evaluation run performed in this session, under any flag combination. Both
   features' live-environment correctness rests entirely on their unit/integration test coverage (22 +
   44 tests, all passing, using mocked dependencies) — not on any run of the real system this session.

Neither point required or received a code change to `runner.py`, `experiments.py`, or the frozen Phase
3A/3B logic — fixing point 1 would mean modifying Phase 2's evaluation runner, which is out of this
gate's scope ("an evaluation gate, not another architecture expansion"); point 2 requires a valid
Anthropic API key, which this task was explicitly barred from working around in any way.

### Recommendation — is either feature production-approved?

**No.** Per the explicit instruction not to mark Phase 3A/3B as production-approved unless evidence
supports it: it does not. `parent_child_retrieval_enabled` and `query_rewriting_enabled` remain
`False` by default, and should stay that way in any shared/deployed environment. Both features are
implemented and unit/integration-tested; neither has any live-environment evidence, positive or
negative, of their actual retrieval/generation effect.

### Should Phase 3C proceed?

**No — not recommended yet.** Phase 3C (multi-query retrieval) would add a third feature on top of two
that remain evidentially unverified in this environment, and would inherit the exact same evaluation
blockers (invalid API key; retrieval-metric computation that structurally can't see tool-mediated
retrieval changes) that prevented 3A/3B from reaching a verdict. Recommended before starting 3C, in
priority order: (1) obtain a valid `ANTHROPIC_API_KEY` so `run_agent()`'s tool loop can actually run and
generation-quality metrics become measurable; (2) grow the eval dataset past this gate's 5-query
minimum; (3) as a separate, explicitly-scoped follow-up, change `run_evaluation()`'s retrieval-metrics
computation to route through `retrieval_agent.search_documents()` instead of a direct
`search_with_reranking()` call, so Recall/Precision/MRR/NDCG can actually reflect Phase 3A's and future
Phase 3C's effect. None of these three are performed in this task.

### Final test run

Full backend suite after all changes in this phase, from `backend/`, via `python -m pytest -q`:

```
341 passed, 2 warnings in 14.15s
```

Zero failures, zero regressions.

## 15. Evaluation Architecture Correction report

Scope: correct §14's discovered limitation — `run_evaluation()`'s Recall/Precision/MRR/NDCG came from
a direct `search_with_reranking()` call that bypassed `retrieval_agent.search_documents()` (Phase 3A's
`fetch_parent_context()`) and `planner.py`'s `search_documents` tool (Phase 3B's
`_maybe_rewrite_query()`) unconditionally, regardless of flag state. Fix what evaluation measures, not
what Phase 3A/3B do. Explicitly not permitted, and not done: implementing Phase 3C, implementing
multi-query retrieval, modifying Phase 3A/3B's own logic, implementing domain agents, async ingestion,
new guardrails, RBAC changes, SQL changes, frontend redesign, or fabricating/estimating any metric that
couldn't actually be measured in this environment. Full design, proof, and current results:
`docs/RAG_RETRIEVAL.md` §"Evaluation Architecture Correction".

### Old vs. corrected evaluation path

```
OLD:  eval_query.query → search_with_reranking() → hybrid_search() → Recall/Precision/MRR/NDCG
      (never reaches fetch_parent_context() or _maybe_rewrite_query() — unconditionally)

NEW:  eval_query.query → _maybe_rewrite_query()        [real planner.py function]
                       → search_documents()             [real retrieval_agent.py function]
                            → search_with_reranking() → hybrid_search() → rerank
                            → fetch_parent_context()    [real search.py function, when flag is on]
                       → Recall/Precision/MRR/NDCG computed from these ids
```

### Files changed

- `backend/app/services/evaluation/runner.py` — retrieval stage now calls
  `planner._maybe_rewrite_query()` then `retrieval_agent.search_documents()` instead of
  `search_with_reranking()` directly; new `EvaluationRetrievalError`; builds and persists a
  `retrieval_trace` dict per run.
- `backend/app/models/eval_run.py` — one new nullable column, `retrieval_trace` (JSONB).
- `backend/app/db/postgres.py` — one additive migration adding that column, appended to
  `_run_light_migrations()` (applied to the live DB).
- `backend/app/routers/evaluation.py` — `retrieval_trace` added to `EvalRunResponse`;
  `EvaluationRetrievalError` mapped to a `503 retrieval_unavailable` response in both
  `POST /eval/queries/{id}/run` and `POST /eval/experiments/run`.
- `backend/tests/evaluation/test_runner.py` — rewritten: stubs `search_documents`/
  `_maybe_rewrite_query` (the new call sites) instead of `search_with_reranking`; adds coverage for
  the corrected call sites, the trace contents, and the new retrieval-failure error type.
- `backend/tests/evaluation/test_evaluation_router.py` — two new tests for `retrieval_trace` in
  `_run_to_response`.
- `docs/RAG_RETRIEVAL.md` — new "Evaluation Architecture Correction" section; the prior "Phase 3
  Evaluation Results" section is labeled **Historical / non-representative retrieval comparison**,
  kept verbatim, not deleted.

### Files created

- `backend/tests/evaluation/test_evaluation_integration.py` — 13 new tests. Unlike the rest of the
  suite's established convention of stubbing at the function-call boundary, these leave
  `_maybe_rewrite_query()`/`search_documents()`/`fetch_parent_context()`/`rewrite_query()` real and
  mock only the true external I/O underneath (the Qdrant-facing `search_with_reranking()` call and the
  Anthropic-facing `claude_gateway.generate()` call) — proof Phase 3A/3B's actual code executes during
  evaluation, not a second layer of stubs standing in for it. One test makes a real, unmocked
  Anthropic call using this environment's actual credential.

### Database changes

```sql
ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS retrieval_trace JSONB;
```

Applied to the live database (additive, nullable — existing rows read back as `retrieval_trace: null`).

### Configuration changes

None. No new settings; `parent_child_retrieval_enabled`/`query_rewriting_enabled` remain `False` by
default, unchanged.

### Tests

26 new tests (13 in `test_runner.py` replacing/extending the 6 that tested the old call site, 2 in
`test_evaluation_router.py`, 13 in the new `test_evaluation_integration.py`, net +26 over the prior
341). **367/367 passing**, zero regressions. Full command and result in "Final test run" below.

Coverage includes every item the request listed: the corrected call sites (unit level, `test_runner.py`)
and the real call chain through them (integration level) for all four configurations (baseline,
parent-child, query-rewrite, combined) individually and via `experiments.run_experiment()`; proof
`_temporary_flags()` drives the real chain, not just `settings`; `retrieval_trace` contents for every
combination; a direct identity assertion that `runner.py` calls the exact production functions, not
copies; and every failure path the request named — query-rewrite gateway failure (real fallback logic,
mocked only at the Anthropic HTTP boundary), query-rewrite timeout (real `ThreadPoolExecutor` timeout,
shortened `query_rewrite_timeout_seconds`), missing parent row (retrieval still succeeds), empty
retrieval (handled, not an error), simulated Qdrant-unavailable and PostgreSQL-unavailable (both raise
the new `EvaluationRetrievalError` rather than crashing opaquely or reporting a misleading zero-scored
run), and Claude-unavailable (one real, unmocked Anthropic call against this environment's actual
invalid credential, asserting `generation_available: False` and the existing 401-detection message).

### Live-environment verification

Performed for real, not simulated, against this session's actual environment (details and full
numbers in `docs/RAG_RETRIEVAL.md`):

- `run_evaluation()` called directly against the real Postgres database holding the two curated eval
  queries from §14's gate, with Qdrant genuinely unreachable (`127.0.0.1:6333`, connection refused) —
  raised `EvaluationRetrievalError` with a clear message, exactly as designed. Not a mock.
- A direct `claude_gateway.generate()` call confirmed the Anthropic credential is still invalid
  (`401 authentication_error`) — matching the condition stated in the task, not modified or bypassed.
- Real Postgres was reachable and queried directly (both curated eval queries confirmed present);
  Qdrant was not reachable by any means available in this environment (no Docker daemon running, no
  native Qdrant process, and the project's `docker-compose.yml` does not run Qdrant as a service — its
  own comment states it "runs natively on the host").

**Because Qdrant could not be reached this session, the corrected gate could not be re-run against
live vector data — no Recall/Precision/MRR/NDCG number is reported for baseline/parent-child/
query-rewrite/combined this session. Every cell is `unavailable`, not zero, not estimated, not carried
over from the historical (non-representative) comparison.** See `docs/RAG_RETRIEVAL.md` for the full
results table and the "what is proven vs. what still isn't" breakdown.

### Recommendation — is either feature production-approved now?

**No — unchanged from §14's INSUFFICIENT EVIDENCE**, and deliberately so: this task corrected *how*
retrieval is measured, not whether Phase 3A/3B help. `parent_child_retrieval_enabled` and
`query_rewriting_enabled` remain `False` by default. What changed is that the evaluation gate is now
capable of producing a real verdict — proven to execute the real code end-to-end (integration tests)
and to fail clearly rather than silently when its infrastructure is unavailable (proven live, this
session) — the moment a reachable Qdrant and a valid `ANTHROPIC_API_KEY` are both available together.

### Should Phase 3C proceed?

**No — not recommended**, same reasoning as §14, now with one blocker narrowed: the evaluation
methodology itself is fixed, but the live-data blocker (reachable Qdrant + valid Anthropic key,
together, in one environment) is unchanged. Do not start Phase 3C.

### Final test run

Full backend suite after this correction, from `backend/`, via `python -m pytest -q`:

```
367 passed, 2 warnings in 10.77s
```

Zero failures, zero regressions.

## 16. Make the Corrected Evaluation Runnable — report

Scope: this task made no source-code changes. §15 corrected *what* evaluation measures; this task made
the corrected evaluation actually runnable and executed it for real. Explicitly not permitted, and not
done: implementing Phase 3C, implementing multi-query retrieval, enabling either feature globally,
modifying RBAC/guardrails/SQL security, implementing domain agents or async ingestion, changing vector
databases, or fabricating any Qdrant response, Claude response, or evaluation metric. Full detail, real
numbers, and methodology are in `docs/RAG_RETRIEVAL.md`.

### Runtime dependencies discovered

The repository already had a supported way to run every dependency (`docker-compose.yml` for
Postgres/Redis; `app/core/config.py`'s `Settings` for the Anthropic credential). The one gap was
Qdrant, which `docker-compose.yml` deliberately does not run as a container — its own comment states
it "runs natively on the host." The repository already contained that native install:
`qdrant-bin/qdrant.exe` (v1.18.3) with pre-existing real storage at `qdrant-bin/storage/` (a
`document_chunks` collection, `points_count: 16`, dense vector `dense` size 1024 cosine, sparse vector
`bm25_sparse` — exactly matching `app/core/config.py`'s settings). No second Qdrant configuration, no
alternate vector database, and no fake data were introduced — the existing binary and its existing data
were used as-is.

### Qdrant status

Started via `cd qdrant-bin && ./qdrant.exe` (this session only — not made to auto-start, not added to
`docker-compose.yml`, since doing so wasn't requested and the existing native-host pattern is what the
repo's own comment documents as the supported approach). Verified reachable (`GET /collections`,
`GET /collections/document_chunks`) and confirmed to already contain all three `expected_chunk_ids`
referenced by the two curated eval queries. Left running at the end of this task.

### Claude Gateway status

Unchanged: `ANTHROPIC_API_KEY` is present in configuration (checked by presence/length only, never
printed) and reaches `claude_gateway`, but a direct minimal `generate()` call returns
`HTTP 401 authentication_error`. Not modified, not hardcoded, no provider switch.

### Controlled evaluation result

One eval query run through `run_evaluation()` under all four flag configurations, reading each real,
persisted `retrieval_trace` back: baseline showed no rewrite attempt and no parent expansion;
parent-child showed 2 of 10 hits real-enriched with parent context; query-rewrite showed a real
Anthropic call, a real 401, and a real fallback to the original query recorded in the trace; combined
showed both simultaneously. Full JSON traces in `docs/RAG_RETRIEVAL.md`.

### Whether Phase 3A actually executed

**Yes, confirmed live.** `retrieval_trace.parent_context_chunk_ids` was non-empty (2 chunk ids) on
every parent-child and combined run, for both eval queries — `fetch_parent_context()` ran for real
against real Postgres chunk rows resolved from real Qdrant hits.

### Whether Phase 3B actually executed

**Yes, confirmed live — the fallback path.** `retrieval_trace.rewrite_trace` was a real, non-null trace
entry on every query-rewrite and combined run, containing a real Anthropic 401 error message and
"kept original query" — `rewrite_query()`'s real gateway call and real fallback logic executed. No run
this session observed a *successful* rewrite, since Claude is unavailable in this environment; that
remains unverified live (though covered by 22 unit tests with mocked success cases, per §13).

### Baseline / Parent-child / Query-rewrite / Combined results

Real Recall@10/Precision@10/MRR/NDCG@10, averaged across both curated eval queries:

| Metric | Baseline | Parent-Child | Query Rewrite | Combined |
|---|---:|---:|---:|---:|
| Recall | 0.500 | 0.500 | 0.500 | 0.500 |
| Precision | 0.100 | 0.100 | 0.100 | 0.100 |
| MRR | 0.125 | 0.125 | 0.125 | 0.125 |
| NDCG | 0.2412 | 0.2412 | 0.2412 | 0.2412 |

Identical across all four — explained in full in `docs/RAG_RETRIEVAL.md` (parent-child is designed to
never change ranking; query rewriting never got a successful rewrite to test, only its fallback, given
the real 401). This is a real result with a real explanation, not a repeat of §15's structural bug —
see that section's "Why every retrieval number is identical" for the full, verifiable reasoning.

### Latency comparison

First retrieval call in a fresh process: 39.2–78.7s (model-weight loading, once per process). Warm
calls: 8.1–29.0s, varying by how warmed-up Qdrant's own process was. Total latency (avg, includes the
fast-failing generation attempt): baseline 24,570ms, parent-child 10,845ms, query-rewrite 11,064ms,
combined 12,004ms — baseline's average is inflated by the one cold call in its position in the run
order; full breakdown and a warm-to-warm-only comparison are in `docs/RAG_RETRIEVAL.md`.

### Token/cost comparison

Unavailable on every run — `run_agent()` and `judge_answer()` both fail on the same real 401 before any
usage is recorded to `gateway_usage_logs`. `tokens_input`/`tokens_output`/`cost_usd`/`model` are `None`
(not zero) on all 8 runs.

### Per-question changes

2 questions, both unchanged across all four configurations (0 improved, 0 degraded): question 1
(both expected chunks found, Recall 1.0) and question 2 (expected chunk not retrieved in top 10 of 16,
Recall 0.0 — a real, pre-existing retrieval/data characteristic of this small corpus, not something
either feature could address by design; noted as an observation, not remediated — out of scope).

### Production recommendation

**Phase 3A (parent-child retrieval): INSUFFICIENT EVIDENCE.** Real execution proof exists; dataset size
and unmeasurable generation quality (the dimension this feature targets) both remain blocking.
**Phase 3B (query rewriting): INSUFFICIENT EVIDENCE.** Real fallback-safety proof exists (stronger than
before); a *successful* rewrite's effect remains completely unmeasured. Neither feature is
production-approved; neither is enabled by default; do not proceed to Phase 3C.

### Test result

Full backend suite unaffected (no source changes in this task): **367 passed**, same as §15's final
run. Re-confirmed after this task's live-infrastructure work.

### Remaining blockers

1. **Anthropic credential** — `ANTHROPIC_API_KEY` still returns `401 authentication_error`. Requires a
   valid key; not something this task is authorized to fix.
2. **Eval dataset size** — 2 curated queries, below the gate's 5-query minimum for any verdict.
3. **Qdrant is session-local** — started manually this session via the repository's existing native
   binary; not wired into any auto-start mechanism (not requested, and doing so wasn't part of this
   task's scope).

**Stopped here per the request — did not implement Phase 3C.**

## 17. Evaluation Dataset Expansion report

Scope: §16 made the corrected evaluation runnable but the dataset (2 queries) stayed below this gate's
own 5-query minimum for a verdict. This task expanded it to 30 verified questions across the real
documents already in this environment and re-ran the full gate. Explicitly not permitted, and not done:
implementing Phase 3C, multi-query retrieval, enabling either feature globally, modifying the retrieval
architecture, RBAC, guardrails, or SQL security, fabricating evaluation data/Claude responses/metrics,
or exposing API keys. Full detail: `docs/RAG_RETRIEVAL.md` §"Evaluation Dataset Expansion".

### Files changed

- `backend/app/models/eval_query.py` — new `categories` JSONB column; `description` widened from
  `String(512)` to `Text`.
- `backend/app/db/postgres.py` — one additive migration for both.
- `backend/app/routers/evaluation.py` — `categories` added to `EvalQueryCreateRequest`/
  `EvalQueryResponse`.
- `docs/RAG_RETRIEVAL.md` — new "Evaluation Dataset Expansion" section; top-of-file pointer updated.

### Files created

None (dataset rows were inserted directly via a one-off script using the existing `EvalQueryModel` —
no new application code needed beyond the schema/router changes above).

### Database changes

```sql
ALTER TABLE eval_queries ADD COLUMN IF NOT EXISTS categories JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE eval_queries ALTER COLUMN description TYPE TEXT;
```

Applied to the live database. 28 new `eval_queries` rows inserted; the pre-existing query 2's
`expected_chunk_ids` corrected in place (see "A data-quality finding" in `RAG_RETRIEVAL.md`) rather
than deleted — dataset went from 2 rows to 30.

### Evaluation dataset size / categories / verified count

**30 questions, all 30 verified** — every `expected_chunk_id` resolved by live document-id +
chunk-index lookup against Postgres and asserted present in Qdrant at build time (a failing assertion
would have halted the build; none did). Categories: `direct_fact` (20), `date_based` (7), `identifier`
(7), `context_required` (4), `parent_context` (2), `ambiguous_query` (2), `conversational` (2),
`terminology` (2), `rewrite_candidate` (1), `rewrite_not_needed` (1) — not mutually exclusive.

### Anthropic authentication status

Re-verified live immediately before this run: still `401 authentication_error`. Unmodified.

### Rewrite success / fallback counts

**0/30 successful, 30/30 fallback**, for both `query_rewrite` and `combined` — 60 real Anthropic call
attempts total this task, all hitting the same 401, all correctly falling back to the original query.

### Baseline / Parent-child / Query-rewrite / Combined metrics (30 questions)

| Metric | Baseline | Parent-Child | Query Rewrite | Combined |
|---|---:|---:|---:|---:|
| Recall | 0.9833 | 0.9833 | 0.9833 | 0.9833 |
| Precision | 0.1067 | 0.1067 | 0.1067 | 0.1067 |
| MRR | 0.8892 | 0.8559 | 0.8725 | 0.9059 |
| NDCG | 0.9040 | 0.8794 | 0.8917 | 0.9163 |
| Retrieval latency (avg) | 18,090 ms | 11,164 ms | 8,702 ms | 11,003 ms |
| Tokens/cost/citation-accuracy | unavailable (0/30) | unavailable | unavailable | unavailable |

### Category-level results

No category shows a real, reproducible improvement or regression — every non-zero MRR delta (2/30
`direct_fact` questions under parent-child, 1/30 under query-rewrite, 1/30 improved under combined; 1/2
`parent_context` questions under parent-child) traces directly to duplicate-document tie noise (see
below), confirmed per-question via `retrieval_trace`. `date_based`, `identifier`, `context_required`,
`terminology`, and both rewrite-tagged categories were perfectly stable across all four configurations.

### Latency/cost differences

Not attributable to either feature — each configuration ran in its own process with its own one-time
model-load cost baked into that run's numbers (baseline's average is highest because it ran first
within its process in this particular execution). No token/cost figure was computable for any
configuration (401).

### Duplicate-document tie noise — the actual explanation for the MRR/NDCG deltas

`WM_1.pdf` is indexed twice in this environment (a pre-existing accidental double-upload). Several
questions' correct-answer chunk has a byte-identical twin in the duplicate document at a near-tied
score; with each configuration run as a separate process, which duplicate sorts first can differ
run-to-run (floating-point/thread non-determinism), moving MRR/NDCG without moving Recall/Precision (set
membership is unaffected). Directly confirmed: all 9 questions whose retrieved-chunk-id list differed
across configurations involved exactly this pattern, and recall was identical for all 9. Flagged
mid-task; the user explicitly chose to leave the dataset as-is and document this rather than broaden
`expected_chunk_ids` to absorb it — so it's reported here transparently rather than smoothed over.
`combined`'s nominally-highest MRR/NDCG in the table above is this same noise, not a synergy signal.

### Production recommendation

**Phase 3A (parent-child retrieval): INSUFFICIENT EVIDENCE.** Dataset size is resolved; no reproducible
retrieval-ranking effect exists (by design — enrichment doesn't move ranking); the feature's actual
target (generated-answer quality) remains unmeasured.
**Phase 3B (query rewriting): INSUFFICIENT EVIDENCE.** Fallback reliability has strong live evidence
now (60/60); a successful rewrite's value remains completely unverified live.
Neither feature is production-approved; neither is enabled by default; do not proceed to Phase 3C.

### Test result

**425 passed** (367 from §16 + 41 new from the separately-requested Gateway Demo MVP, §18 below) — the
dataset expansion itself added no new backend tests (it's data, not code; verified by build-time
assertions instead). Zero regressions.

### Remaining limitations

1. **Anthropic credential** — still `401 authentication_error`. The one blocker that would unlock a
   real verdict for both features (generation-quality metrics, and a chance to observe an actual
   successful rewrite).
2. **Duplicate `WM_1.pdf` upload** — a pre-existing environment condition that adds tie noise to
   rank-sensitive retrieval metrics for questions whose answer exists in both copies. Not fixed, per
   the user's explicit choice to document rather than alter the dataset.
3. **Corpus is still small** (4 real documents, 23 chunks) — 30 questions is well past the 5-query
   statistical-validity floor but still a narrow domain (one warranty document dominates at 20/30
   direct-fact questions).

**Stopped here per the request — did not implement Phase 3C.**

## 18. Fast-Guardrails Claude Gateway Demo (separate, user-requested) — REMOVED, historical record only

> **2026-08-10 update:** This feature no longer exists in the codebase. It was live and working as
> recently as 2026-08-10 11:25 (confirmed via `.playwright-mcp/` browser-session snapshots capturing
> the rendered `/gateway_demo` page, its role picker, and its preset attack cases), then was deleted
> directly from the working tree at some point afterward. Critically, **it was never committed to
> git** — the repo has exactly 3 commits total (`Initial commit` + two unrelated Streamlit-Cloud
> pins), none of which touch any gateway/demo file, and there is no second branch or stash containing
> it. So this isn't a revert you can find in `git log`: the code was written and later deleted purely
> in the working tree, with no commit ever marking either event. It is **not recoverable from git**
> — re-adding it would mean rebuilding it from this description, not restoring it. `docs/GATEWAY_DEMO.md`
> referenced below also no longer exists. Left as-is below for the historical record of what was built
> and verified; treat every claim past this point as describing something that used to exist, not
> something currently in the tree.

Not part of this plan's Phase 3 sequence — a standalone MVP demo requested mid-session. Full design,
verified live-test results, and the demo script are in `docs/GATEWAY_DEMO.md`. Summary: a new
`backend/app/gateway/demo/` package (classifier/rbac/policy/gateway/models) runs deterministic regex +
a local BGE-M3 nearest-centroid semantic classifier — **no LLM call** — to block unsafe/unauthorized
requests before Claude is ever reached, reusing the existing `claude_gateway`, `injection.py`'s
patterns, `pii.py`'s redactor, and `roles.py`'s `Role` values rather than duplicating any of them. New
`POST /gateway-demo/generate` endpoint and a new Streamlit "Gateway Demo" page. 41 new tests (also
counted in §17's 425 total). Verified live end-to-end, including in a real browser session, against the
running backend (real Qdrant, real Postgres, the same real-but-invalid Anthropic key) — RBAC correctly
allows/denies the identical message for different roles, HIGH-risk categories block unconditionally
with zero Claude calls, and the UI's counters/trace/preset cases all render and update correctly.
