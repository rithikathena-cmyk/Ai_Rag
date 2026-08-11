# Report Authorization

`services/llm_rbac/report_policy.py::authorize_report()` end to end — a sibling to
`services/llm_rbac/engine.py::authorize_llm_request()`, not a replacement for it. Both run for a
report-generation chat turn; they answer different questions.

## 1. Two questions, two functions

| Question | Function | Governs |
|---|---|---|
| "Can this role talk to Claude at all, with which tools/tier/quota?" | `authorize_llm_request()` | Every chat turn, report or not |
| "Can this role generate *this specific report type*, and what data is it scoped to?" | `authorize_report()` | Only turns that name a `report_type` |

`routers/chat.py::chat()` runs `authorize_llm_request()` first (unconditionally, exactly as before
this pass), then — only if `ChatRequest.report_type` is set — runs `authorize_report()` as a second,
narrower gate before the planner ever starts. A denial from either one is a 403 with a `record_denied()`
audit row; nothing new about the shape of that failure, just a second check with its own reason.

## 2. `ReportDecision`

```python
class ReportDecision(NamedTuple):
    status: Literal["allowed", "denied", "approval_required"]
    reason: str | None
    data_available: bool
    row_filter: dict | None
```

`status="approval_required"` is a real, typed code path — nothing currently sets it, since none of
the configured report types in `llm_rbac.yaml`'s `reports:` blocks need a human sign-off before
generation (project *governance* actions do, via `ApprovalRequestModel` — see
`docs/PROJECT_GOVERNANCE.md` — but that's a different kind of approval, gating a state mutation, not
a report request). It's kept in the type rather than removed because the spec explicitly asks for the
three-way status, and a future report type genuinely might need it (e.g. an enterprise-wide report
that's expensive enough to warrant a sign-off) without changing this function's signature.

## 3. `data_available` — the honest half of the contract

Several report types the spec names have zero backing data anywhere in this schema — no machine,
shift, production, attendance, performance, leave, training, or certification table exists (confirmed
by grep before writing a line of this feature — see `docs/LLM_GATEWAY_ANALYSIS.md` §0). Rather than
either silently omitting these report types from the catalog (which would make the ALLOW/DENY policy
untestable against the spec's own §19 matrix) or having Claude fabricate plausible-looking numbers
for a "machine status" report backed by nothing, `report_policy.NO_DATA_REPORT_TYPES` names them
explicitly:

```python
NO_DATA_REPORT_TYPES = frozenset({
    "machine_status", "shift_report", "production_summary", "machine_performance",
    "attendance", "employee_performance", "leave", "training", "certification",
    "workforce", "hr_analytics", "employee_summary",
    "production", "maintenance", "quality", "inventory", "warehouse", "procurement", "hr",
})
```

A report type in this set still gets a correct **`status="allowed"`** decision if the role's catalog
includes it — the *permission* is real and tested (`tests/llm_rbac/test_report_policy.py` asserts
this explicitly for several). What's not real is `data_available`, which is `False`. `routers/chat.py`
checks this immediately after the allow/deny check and, if `False`, returns `501 no_data_source` with
a clear message — **no LLM call happens, no report is fabricated**, and the denial is still logged
(`record_denied()`, `requested_capability=report_type`) so it shows up in the audit trail exactly like
a real denial would.

## 4. `row_filter` — real scoping for real tables

| Report-type category | Example types | Scoping mechanism |
|---|---|---|
| Project-scoped | `project_status`, `project_risk`, `engineering_report`, `project_portfolio`, `executive_report` | `services/agents/project_agent.py::list_my_projects()` — a Python `.filter(manager_id == user_id)` query, PM gets `{"scope": "own", "user_id": ...}`, CEO/Admin gets `{"scope": "all"}` |
| Document-scoped | `manual_summary`, `sop_summary` | Reuses the existing `knowledge_departments` mechanism (`apply_category_policy()`) — no new code, `row_filter = {"knowledge_departments": role_cfg.knowledge_departments}` |
| Ownership-scoped | `assigned_work` | `{"owner_id": user.id}` — realized through `search_documents`'s existing per-user permission filter (`filter_by_permission`), not a new mechanism |
| No-data | everything in `NO_DATA_REPORT_TYPES` | `row_filter=None` — moot, generation never starts |

`_resolve_row_filter()` (`report_policy.py`) is the single place this table is implemented — it's not
duplicated per report type, just a dispatch on which of three category sets a `report_type` falls
into.

## 5. Why project data doesn't flow through `query_analytics`

See `docs/PROJECT_GOVERNANCE.md` §6 for the full reasoning — short version: `sql_guard.py` only does
table-level allowlisting, and extending it to inject a row-level `WHERE` clause into Claude-authored
SQL is a genuinely risky change to a security-critical component. `projects`/`project_members` are
simply never in any role's `sql_allowed_tables`; a Project Manager's Claude-authored analytics SQL
can't reach them at all, by omission rather than by a filter that could have a bug in it. Project data
only reaches the model via the read-only `list_my_projects` planner tool, whose row-level filter is
plain Python — a much smaller, much easier to verify surface (exhaustively covered by
`tests/projects/test_service.py` and the lifecycle tests).

## 6. The full request shape

```json
POST /chat
{
  "message": "Give me a status report on my active projects",
  "report_type": "project_status"
}
```

`report_type` is a new, separate field from the pre-existing `action` (both optional). `action` still
drives `authorize_llm_request()`'s permission-catalog check and Opus-tier escalation, unrelated to
report-type authorization — a client can set either, both, or neither. Keeping them distinct avoids
overloading one field with two different governance concepts that happen to both be "a string naming
a capability."
