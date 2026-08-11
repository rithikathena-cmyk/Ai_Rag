# Audit Logging (LLM RBAC)

Extends `docs/CLAUDE_GATEWAY_ARCHITECTURE.md` §10 ("Usage tracking"). `GatewayUsageLogModel` was
already the durable, queryable usage record; this pass extends it into the LLM-RBAC audit log the
spec asks for, rather than standing up a second, parallel table.

## 1. Schema

| Column | Since | Meaning |
|---|---|---|
| `id`, `request_id`, `agent_name`, `model`, `tier`, `tokens_input`, `tokens_output`, `latency_ms`, `cost_usd`, `created_at` | pre-existing | Unchanged. |
| `user_id` | new | FK → `users.id`. `NULL` for internal, non-role-driven callers (`generation_judge.py`, `memory/store.py` — see `docs/CLAUDE_GATEWAY_MODEL_ROUTING.md` §4). |
| `role`, `department` | new | Denormalized *at write time* — a later role/department change never rewrites history, so an old audit row always reflects what the caller actually was when the request happened. |
| `prompt_version` | new | The planner system prompt's version (`gateway/prompt_manager.py`'s `PromptTemplate.version`) — which exact prompt produced this response. |
| `tool_calls` | new | `JSONB` list of tool names invoked during this turn (e.g. `["search_documents", "query_analytics"]`), accumulated via closure state in `planner.py::_build_tools()`. |
| `documents_retrieved` | new | `JSONB` list of document IDs surfaced to the LLM this turn — the literal answer to "what did this request see." |
| `decision` | new | `"allowed"` or `"denied"`, default `"allowed"` (existing rows stay valid). |
| `denial_reason` | new | Set only on `decision="denied"` rows — the `AppError` message that caused the rejection. |
| `requested_capability` | new (this pass) | The `action` capability name (e.g. `"workforce_planning"`, `"upload_documents"`) when the caller supplied one — structured counterpart to `denial_reason`'s free text, matching the spec's explicit `requested_capability` audit field. `NULL` when no `action` was given (the common case — a normal chat turn without a quick-action button). |

## 2. What's written on allow vs. deny

**Allowed** (`gateway/usage_tracker.py::record_usage()`, unchanged call sites — `claude_gateway.generate()`/
`.stream()`, plus the direct call in `planner.py::call_model()` since LangGraph's `.invoke()` bypasses
`generate()`): one row per LLM turn, real token counts, real cost, `decision="allowed"`. When
`user_id` is supplied, this also advances that user's daily/monthly quota counters
(`services/llm_rbac/quotas.py::increment_usage()`) in the same step — the next request's budget check
sees this one's usage.

**Denied** (`gateway/usage_tracker.py::record_denied()`, new — called from `routers/chat.py` and
`routers/search.py`'s `except AppError` handlers around `authorize_llm_request()`): one row per
rejected request, `model="n/a"`, zero tokens/cost, `decision="denied"`, `denial_reason` set. A request
that never reached Claude still produces an auditable record — the spec's "Decision
(Allowed/Denied)" requirement applies to every request, not just successful ones.

Note: `/search` doesn't call the Claude Gateway at all (it's pure retrieval, no LLM) — so a
*successful* search produces no `GatewayUsageLogModel` row (there's no gateway call to log), only a
denied one does. This is an intentional scope boundary, not an oversight: this table's purpose is
gateway/LLM usage, and search isn't an LLM request.

## 3. Approval (v1 scope)

The spec's "Approval Policies" section maps, in this repo, to `llm_rbac.yaml`'s
`approval_required_actions` per role and `PolicyDecision.requires_approval` — currently only
Project Manager's `delete_documents` sets this. `routers/documents.py::delete_document()` is now the
one consumer: when `decision.requires_approval` is true, it responds `403 approval_required` (and
still writes a `decision="denied"` audit row) rather than silently allowing the deletion. There is
still no `ApprovalRequest` model or grant mechanism anywhere in this repo — building one wasn't in
scope for this pass — so "requires approval" currently means "blocked until an approval workflow
exists," not "blocked pending a real approval." See `docs/LLM_RBAC_POLICY.md` §4 for the reasoning.

## 4. Query examples

Existing route, unchanged behavior, now returns the new columns: `GET /admin/gateway-usage`
(`routers/admin.py`, admin-only). Example queries against the table directly:

```sql
-- Every denied request in the last 24h, by role
SELECT role, denial_reason, count(*)
FROM gateway_usage_logs
WHERE decision = 'denied' AND created_at > now() - interval '1 day'
GROUP BY role, denial_reason
ORDER BY count(*) DESC;

-- A user's total cost this month
SELECT sum(cost_usd)
FROM gateway_usage_logs
WHERE user_id = :user_id AND decision = 'allowed'
  AND created_at >= date_trunc('month', now());

-- Which documents a specific request actually saw
SELECT documents_retrieved FROM gateway_usage_logs WHERE request_id = :request_id;
```

## 5. `role_usage_counters` — the fast-lookup rollup, not a second audit trail

`RoleUsageCounterModel` (`models/role_usage_counter.py`) is a small pre-aggregated table
(`user_id`, `period_type` [`day`/`month`], `period_start`, `tokens_used`, `cost_usd_used`,
`request_count`) that exists purely so `services/llm_rbac/quotas.py::check_budget()` is one indexed
row lookup instead of re-aggregating `gateway_usage_logs` on every request. It is not a second audit
trail — `gateway_usage_logs` stays the detailed, per-request record; this table only ever answers
"has this user's budget run out," never "what did this user do."
