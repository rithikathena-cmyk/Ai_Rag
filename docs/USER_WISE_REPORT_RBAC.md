# User-Wise Report RBAC

The role → report-type matrix and data-access matrix the spec asks for, in one place — mirrors
`docs/LLM_RBAC_POLICY.md`'s style for the general permission catalog, but for the `reports:` block
specifically. See `docs/REPORT_AUTHORIZATION.md` for how `authorize_report()` actually evaluates this
table, and `docs/PROJECT_GOVERNANCE.md` for the project-governance side (create/update/submit/
approve/pause/close/...) — this document is scoped to report *generation*, not project management.

## 1. Role → report-type matrix

`✅ real` = allowed and backed by actual data. `⚪ inert` = allowed (the policy check correctly
returns `status="allowed"`) but `data_available=False` — generating one returns `501 no_data_source`
rather than a fabricated report, since no machine/shift/attendance/production table exists in this
schema. `—` = not in the role's `reports:` catalog at all → `403 denied`.

| Report type | Employee | HR | Project Manager | CEO/Admin |
|---|---|---|---|---|
| `machine_status` | ⚪ inert | — | — | ⚪ inert (via `*`) |
| `shift_report` | ⚪ inert | — | — | ⚪ inert |
| `production_summary` | ⚪ inert | — | — | ⚪ inert |
| `machine_performance` | ⚪ inert | — | — | ⚪ inert |
| `assigned_work` | ✅ real (owner-scoped) | — | — | ✅ real |
| `manual_summary` | ✅ real (department-scoped) | — | — | ✅ real |
| `sop_summary` | ✅ real (department-scoped) | — | — | ✅ real |
| `attendance` | — | ⚪ inert | — | ⚪ inert |
| `employee_performance` | — | ⚪ inert | — | ⚪ inert |
| `leave` | — | ⚪ inert | — | ⚪ inert |
| `training` | — | ⚪ inert | — | ⚪ inert |
| `certification` | — | ⚪ inert | — | ⚪ inert |
| `workforce` | — | ⚪ inert | — | ⚪ inert |
| `hr_analytics` | — | ⚪ inert | — | ⚪ inert |
| `employee_summary` | — | ⚪ inert | — | ⚪ inert |
| `project_status` | — | — | ✅ real (own-scoped) | ✅ real (all) |
| `project_progress` | — | — | ✅ real (own-scoped) | ✅ real (all) |
| `project_summary` | — | — | ✅ real (own-scoped) | ✅ real (all) |
| `project_risk` | — | — | ✅ real (own-scoped) | ✅ real (all) |
| `resource_allocation` | — | — | ✅ real (own-scoped) | ✅ real (all) |
| `project_performance` | — | — | ✅ real (own-scoped) | ✅ real (all) |
| `project_document_summary` | — | — | ✅ real (own-scoped) | ✅ real (all) |
| `engineering_report` | — | — | ✅ real (own-scoped) | ✅ real (all) |
| `project_portfolio` | — | — | — | ✅ real (all) |
| `executive_report` / `enterprise` / `management_summary` / `risk` | — | — | — | ✅ real (all, composed from projects/documents/reports) |
| `production` / `maintenance` / `quality` / `inventory` / `warehouse` / `procurement` / `hr` | — | — | — | ⚪ inert |

Employee/HR "—" rows for CEO-exclusive types and vice versa aren't a limitation of the *system* —
they're the actual, intended role boundary. CEO/Admin's catalog is `reports: allowed: ["*"]`
(matching spec §16's own example config), so every row is at minimum `⚪ inert`/`✅ real` for that
column, never denied.

## 2. Data access matrix

| Role | Scoping mechanism | Enforced by |
|---|---|---|
| Employee | Own documents (`owner_id`) + department (`knowledge_departments=[manufacturing]`) | Existing `search_documents` permission + category filters — no new mechanism |
| HR | Department (`knowledge_departments=[hr]`) | Same, unchanged — HR's report types with real data would use this if any existed; today all of HR's real-data types (`manual_summary`-equivalent) aren't in HR's catalog, so this row is currently theoretical |
| Project Manager | Own managed + member projects (`manager_id`/`ProjectMemberModel`) | New: `services/agents/project_agent.py::list_my_projects(scope="own")` |
| CEO/Admin | Unrestricted | `scope="all"` — no filter applied, consistent with `knowledge_departments` already covering all four departments for this role |

## 3. Model routing for reports

Report generation doesn't get its own model-routing table — it reuses `services/llm_rbac/engine.py`'s
existing tier resolution exactly as any other chat turn does (`docs/CLAUDE_GATEWAY_MODEL_ROUTING.md`).
A client requesting a report can additionally set `action` (unrelated to `report_type`) to trigger
Opus escalation for a role/action pair that qualifies — e.g. HR requesting `report_type="attendance"`
*and* `action="workforce_planning"` in the same turn would both authorize the (currently inert)
attendance report type and escalate to Opus for the workforce-planning-flavored synthesis, if the
underlying data existed. Today, since every report type with a real `escalate_to_opus_for` trigger
(`workforce_planning`, `leave_analytics`, `engineering_planning`, `risk_assessment`) corresponds to a
report type that's `⚪ inert` or project-scoped, this composes correctly but isn't independently
report-type-aware — see `docs/REPORT_AUTHORIZATION.md` §6 for why `action` and `report_type` are kept
as two distinct request fields rather than one overloaded concept.

## 4. Frontend

Out of scope for this pass — role-gated report-type pickers and a Projects page were deliberately
deferred; see the final implementation report's "remaining limitations" section. The backend enforces
every rule in §1 regardless of what the frontend does or doesn't show.
