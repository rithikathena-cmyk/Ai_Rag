# Tool Authorization

How `allowed_tools` (one of `PolicyDecision`'s fields, resolved by
`services/llm_rbac/engine.py::authorize_llm_request()`) actually reaches the LangGraph planner, and
why a disallowed tool is structurally unreachable rather than checked-and-rejected per call.

## 1. The three tools, and the one place they're gated

`services/agents/planner.py::_build_tools()` is the single place all three planner tools
(`search_documents`, `query_analytics`, `generate_report`) are constructed. There is no per-tool
authorization check inside `retrieval_agent.py`, `sql_agent.py`, or `report_agent.py` themselves —
each of those modules just does its job assuming it was legitimately called. The gate is entirely at
construction time:

```python
names = all_tools.keys() if allowed_tools is None else [n for n in all_tools if n in allowed_tools]
return [all_tools[n] for n in names]
```

`allowed_tools` comes from `PolicyDecision.allowed_tools` (`decision.allowed_tools` in
`routers/chat.py`), which in turn is `role_config(role).tools` from `llm_rbac.yaml` — see
`docs/LLM_RBAC_POLICY.md` §1 row 2. `None` means "no filtering," the pre-LLM-RBAC default that keeps
any direct caller of `run_agent()` (tests, internal scripts) working unchanged.

## 2. Why "never bound" beats "checked and rejected"

The filtered tool list is what gets bound to the model:

```python
model = claude_gateway.get_langchain_model(tier=tier, ...).bind_tools(tools)
```

Claude only ever sees the tool *schemas* that were bound. A tool an Employee's role doesn't include
(`query_analytics`, `generate_report`) is never in that schema list — the model has no way to know
it exists, let alone call it. This is a stronger guarantee than "call the tool, then check the role
and reject": there's no code path where a disallowed tool call reaches `retrieval_agent.py`/
`sql_agent.py`/`report_agent.py` at all, so there's nothing to audit-log as a "tool not allowed"
denial (unlike a permission-catalog denial, which does produce a `record_denied()` row — see
`docs/AUDIT_LOGGING.md`). The absence of that log line is itself the expected behavior: it means the
model literally never attempted the call, not that a check silently swallowed one.

## 3. Two independent gates per tool, not one

Being in `allowed_tools` only controls whether the tool is *offered*. Two of the three tools have a
second, independent narrowing once they *are* offered and called:

| Tool | Gate 1 (offered at all) | Gate 2 (narrowed once called) |
|---|---|---|
| `search_documents` | `allowed_tools` | `knowledge_departments` → `apply_category_policy()` (department/access_roles filter, applied before the Qdrant query is built — see `docs/KNOWLEDGE_ACCESS_CONTROL.md`) |
| `query_analytics` | `allowed_tools` | `sql_allowed_tables` → `sql_guard.validate_select()` (table allowlist substitution, plus sql_guard's own unconditional SELECT-only/no-DDL check — see `docs/LLM_RBAC_POLICY.md` §1 row 4) |
| `generate_report` | `allowed_tools` | none at the tool-call level — but the report's own `owner_id`/`department` are now tagged at creation (this pass; see `docs/AUDIT_LOGGING.md` §2 and `docs/LLM_RBAC_POLICY.md` §1's Report row), so a later *read* of that report is gated by `routers/reports.py`'s visibility filter |

Both gates run every time — narrowing never widens what `allowed_tools`/`sql_guard`'s own default
already restrict (see `sql_agent.py`'s docstring: the `allowed_tables` a caller supplies is always a
subset of `sql_guard.ALLOWED_TABLES` by construction, since it only ever comes from
`llm_rbac.yaml`, never client input).

## 4. Non-planner authorization: the same `allowed_tools`-style pattern, applied to REST routes

Before this pass, tool authorization only existed for the three planner tools — `routers/documents.py`
had no equivalent gate on `POST /documents/upload`/`DELETE /documents/{id}`, meaning "can this role
upload/delete a document" was unenforced even though "can this role's planner turn upload/delete a
document" was moot (no such tool exists). This pass closes that gap using the *same* centralized
policy engine, not a parallel mechanism:

```python
decision = authorize_llm_request(db, current_user, endpoint="documents", action="upload_documents")
```

`action` here plays the same role `ChatRequest.action` plays for chat — a named entry in
`llm_rbac.yaml`'s permission catalog, checked by the same `_check_permission()` function that gates
`workforce_planning`/`engineering_planning`/etc. There is no second, bespoke authorization function
for document routes — everything still routes through `services/llm_rbac/engine.py`.

Document/report **reads** (`GET /documents`, `GET /documents/{id}`, `GET /reports`, ...) don't call
`authorize_llm_request()` at all — that function's rate-limit and daily/monthly budget checks are
scoped to Claude Gateway requests (see its own docstring), and applying LLM-usage quotas to a plain
metadata read would conflate two different kinds of governance. Instead they call the new
`services/llm_rbac/policy_loader.py::knowledge_departments_for(role)` — the same
`knowledge_departments` resolution `authorize_llm_request()` does internally, without the quota/
rate-limit side effects — and feed that into the existing `filter_by_category()`. One policy
resolution function, two different callers depending on whether quota enforcement is appropriate.
