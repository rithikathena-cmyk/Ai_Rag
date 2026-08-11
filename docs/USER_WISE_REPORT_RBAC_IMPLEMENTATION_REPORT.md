# User-Wise Report RBAC & Project Governance — Implementation Report

Final deliverable for the "ATHENA MES AI — User-Wise LLM RBAC Reports & Project Governance" request.
Per its own instructions ("analyze first," "do not rebuild the existing architecture"), this pass
began with a full repository inspection before writing code — see the plan's Context section for what
that inspection found (no `Project`/`ApprovalRequest` domain model existed anywhere; the prior LLM
RBAC pass's `ReportModel.owner_id`/`department` + `routers/reports.py`'s visibility filter was a
directly reusable pattern; `sql_guard.py` only does table-level allowlisting).

## 1. Existing components reused

- **FastAPI + JWT auth** — every new router (`projects.py`, `approvals.py`) uses the exact same
  `Depends(get_current_user)` pattern as every existing router; no new auth mechanism.
- **`Role` enum** — untouched. CEO and Admin remain the single `admin` value, per the prior pass's
  established naming decision (`docs/LLM_RBAC_ARCHITECTURE.md` §4) — not re-litigated here.
- **LLM RBAC engine** (`authorize_llm_request()`) — reused as-is for every new project write action
  (`project_creation`, `project_update`, `project_allocation`, `project_submit`), following the exact
  pattern the prior pass established for `upload_documents`/`delete_documents`.
- **`require_role()`** — reused unchanged for every CEO/Admin-only direct project action
  (`reallocate`/`priority`/`manager`/`pause`/`resume`/`complete`/`close`/`cancel`), same as `/admin/*`
  and `/users` already do.
- **Claude Gateway** — no new call site. `list_my_projects`, the one new planner tool, doesn't call
  Claude itself; it's a tool the existing gateway-routed model can invoke, same as `search_documents`.
- **SQL guard** — untouched. Deliberately kept out of scope for project data (see §4) rather than
  extended with row-level filtering.
- **Report generation pipeline** — `report_agent.py::generate_report()` extended (not replaced) with
  `owner_id`/`department` kwargs already added in the prior pass; this pass adds authorization
  (`authorize_report()`) and data-availability checking in front of it, same downstream writer.
- **Audit logging** — `GatewayUsageLogModel`/`record_usage()`/`record_denied()` extended with two new
  columns, not restructured.
- **`filter_by_category`/`knowledge_departments_for`** — reused unchanged for `manual_summary`/
  `sop_summary`'s row-scoping; no new document-filtering code.

## 2. Files

**Created:**
`backend/app/models/{project,project_member,approval_request}.py`,
`backend/app/services/projects/{__init__,lifecycle,service}.py`,
`backend/app/services/agents/project_agent.py`,
`backend/app/services/llm_rbac/report_policy.py`,
`backend/app/routers/{projects,approvals}.py`,
`backend/tests/projects/{__init__,test_lifecycle,test_service}.py`,
`backend/tests/test_projects_rbac.py`, `backend/tests/test_approvals_rbac.py`,
`backend/tests/llm_rbac/test_report_policy.py`,
`docs/USER_WISE_REPORT_RBAC.md`, `docs/PROJECT_GOVERNANCE.md`, `docs/REPORT_AUTHORIZATION.md`,
`docs/USER_WISE_REPORT_RBAC_IMPLEMENTATION_REPORT.md` (this file).

**Modified:**
`backend/app/db/postgres.py` (model registration + light migrations),
`backend/app/main.py` (router registration),
`backend/app/models/gateway_usage_log.py` (`output_format`, `resource_scope`),
`backend/app/gateway/usage_tracker.py` (thread the two new fields through),
`backend/app/services/agents/planner.py` (`list_my_projects` tool, `report_row_filter`/`action` param
threading, `output_format`/`resource_scope` on `record_usage()`),
`backend/app/routers/chat.py` (`report_type` field, `authorize_report()` gate),
`backend/app/routers/documents.py` (`delete_document()` upgraded to a real approval queue entry,
`delete_document_row()` factored out for reuse by `approvals.py`),
`backend/app/services/llm_rbac/policy_loader.py` (`RoleConfig.reports_allowed`,
`role_config()` parses the new `reports:` YAML block),
`backend/app/services/llm_rbac/engine.py` (`_ALL_TOOLS` includes `list_my_projects`),
`backend/config/llm_rbac.yaml` (project_* catalog actions, `reports:` block per role,
`list_my_projects` added to PM/Admin `tools:`),
`backend/tests/llm_rbac/test_policy_engine.py` (project-action matrix cases),
`backend/tests/test_audit_logging.py` (`output_format`/`resource_scope` assertions),
`backend/tests/test_chat_auth.py` (`report_type` field check),
`docs/LLM_RBAC_ARCHITECTURE.md`, `docs/CLAUDE_GATEWAY_MODEL_ROUTING.md`.

**Removed:** none.

## 3. Database changes

Three new tables (`projects`, `project_members`, `approval_requests`), created via
`Base.metadata.create_all()` — no light-migration `ALTER` statements needed for brand-new tables, only
model registration in `ensure_schema()`. Two new columns on the existing `gateway_usage_logs` table
(`output_format`, `resource_scope`), added via the established append-only `_run_light_migrations()`
list, same pattern as every prior RBAC column. No changes to `documents`, `reports`, or `users`.

## 4. RBAC changes

- New `llm_rbac.yaml` catalog actions: `project_creation`, `project_update`, `project_allocation`,
  `project_submit` — Project Manager allow, Employee/HR explicit deny, Admin covered by wildcard.
- New `reports:` block per role (distinct from `permissions.allow`/`deny`) — see
  `docs/USER_WISE_REPORT_RBAC.md` §1 for the full matrix.
- `services/llm_rbac/report_policy.py::authorize_report()` — new, sibling to `authorize_llm_request()`.
- `services/llm_rbac/policy_loader.py::RoleConfig.reports_allowed` — new field, same
  load/cache/fallback pattern as every other `RoleConfig` field.
- CEO/Admin-only project actions use `require_role(Role.ADMIN)` directly, not the permission catalog
  — a deliberate choice (§7 of the plan) since these actions have exactly one authorized role and no
  approval step, matching how `/admin/*` and `/users` are already gated.

## 5. Report permissions

Full matrix in `docs/USER_WISE_REPORT_RBAC.md` §1. Summary: 3 report types are fully real and
row-scoped for Employee (`assigned_work`, `manual_summary`, `sop_summary`), 8 for Project Manager (all
project-scoped, own-managed-or-member-only), and CEO/Admin gets every type unrestricted via the
wildcard. HR's entire catalog (`attendance`, `employee_performance`, `leave`, `training`,
`certification`, `workforce`, `hr_analytics`, `employee_summary`) and several Employee/CEO types
(`machine_status`, `production`, etc.) are correctly policy-enforced but return `501 no_data_source`
on generation — no HR-operational or manufacturing-telemetry table exists in this schema, and none
was fabricated to make this report "work."

## 6. Project governance changes

Full detail in `docs/PROJECT_GOVERNANCE.md`. Summary: a 9-state lifecycle
(`draft→submitted→approved→active→paused→completed→closed`, plus `rejected`/`cancelled`), enforced by
an exhaustively-tested pure state machine (`services/projects/lifecycle.py`). Project Manager can
create/update/allocate/submit only their own projects; CEO/Admin has unrestricted direct control over
every lifecycle transition, with no approval step (they're the approver). The one real approval
trigger is project submission; a second target type (`delete_document`) reuses the same
`ApprovalRequestModel`/`routers/approvals.py` machinery, upgrading the prior pass's hard-blocked
document-delete-for-PM into an actually actionable queue.

## 7. Model routing changes

None to the routing mechanism itself (`gateway/model_router.py` untouched). `report_type` is
authorized independently of, and has no effect on, tier resolution — see
`docs/CLAUDE_GATEWAY_MODEL_ROUTING.md`'s new note in §3.

## 8. Tests executed

```
cd backend && python -m pytest tests/ -q
```
**258 passed**, 0 failed (up from 126 before this pass — 132 new/added test functions). New files:
`tests/projects/test_lifecycle.py` (exhaustive — every declared transition succeeds, every
undeclared one 409s, parametrized over the full 9×9 state matrix), `tests/projects/test_service.py`,
`tests/test_projects_rbac.py`, `tests/test_approvals_rbac.py` (including a real end-to-end
`require_role(Role.ADMIN)` check via `TestClient` — PM 403s on `/pause`, Admin 200s),
`tests/llm_rbac/test_report_policy.py` (the spec §19 report-type matrix). Extended:
`tests/llm_rbac/test_policy_engine.py`, `tests/test_audit_logging.py`, `tests/test_chat_auth.py`.

Same environment note as the prior pass: this sandbox's Python install was missing packages this
repo's own `requirements.txt` already lists (`redis`, `sqlparse`, `qdrant_client`); installed them to
run the suite — no code change required their absence.

## 9. Remaining limitations

- **Frontend is out of scope for this pass** (deliberately, per your answer during planning) — no
  role-gated nav, no Projects page, no report-type picker. The backend enforces every rule regardless.
- **`ApprovalRequestModel.payload` is unused.** Reserved for a future action that needs to propose
  specific new values (e.g. "change manager to X") rather than just referencing a target — today's two
  approval types (project submission, document deletion) don't need it.
- **`security_classification`/`project` (the free-text string column on `DocumentModel`) remain
  descriptive, not enforcement inputs** — unchanged from the prior pass, noted again since it's
  adjacent to this pass's work.
- **HR's report catalog is entirely inert** — no HR-operational data model exists, so every one of
  HR's 8 report types returns `501 no_data_source`. Wiring real data would need an actual
  attendance/performance/training data source, out of scope per "do not create fake manufacturing/HR
  data."
- **`list_my_projects` is the only planner tool for project data** — no equivalent exists for, say,
  "documents belonging to project X" cross-referencing; a project's linked documents (via
  `DocumentModel.project`, the pre-existing free-text field) aren't currently joined to the new
  `ProjectModel` at all — that string field and the new FK-based `ProjectModel.id` are still two
  unrelated concepts, same gap the prior pass's docs already flagged as future work.

## 10. Future MCP/MES integration points

- **New manufacturing roles / real operational data**: the moment a real machine/shift/attendance/
  production data source exists, the corresponding `llm_rbac.yaml` `reports:` entries need no
  structural change — just remove the report type from `report_policy.NO_DATA_REPORT_TYPES` and give
  it a real `row_filter` resolution branch in `_resolve_row_filter()`. The permission/ALLOW-DENY
  layer is already correct and already tested against that exact data-arriving day.
- **MCP tool execution**: same integration point the prior pass identified —
  `services/agents/planner.py::_build_tools()` — a new MCP-backed tool (e.g. a real "assign a machine
  to an employee" action) would be added there and gated by `allowed_tools`, with `ApprovalRequestModel`
  as the ready-made mechanism if that action needs a human sign-off before executing.
- **`ApprovalRequestModel.payload`**: the natural extension point for an approval type that needs to
  carry proposed new values through the request/decide round-trip, once one exists.
- **Linking `DocumentModel.project` (free text) to `ProjectModel.id` (real FK)**: would let
  `project_document_summary` pull real linked documents instead of relying on department-level
  document scoping alone.
