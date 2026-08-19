# Role Permission Matrix

Generated to match `backend/config/llm_rbac.yaml` exactly — if you change that file, update this
table in the same commit so it can't silently drift. `✅ wired` means a real route/tool implements the
action; `⚪ inert` means the policy check is enforced (allow/deny works correctly) but there is no
route to reach it, because the underlying data model doesn't exist in this repo yet — see
`docs/LLM_RBAC_ARCHITECTURE.md` §5. This is the same status the 11 manufacturing `Role` values already
have for everything except `require_role()` checks.

## Employee (`user`)

Model: **Sonnet only** (structurally — cannot escalate). Knowledge departments: `manufacturing`.
Tools: `search_documents`. Quotas: 20 req/min · 200 req/day · 100K tokens/day · 2M tokens/month ·
$50/month · 2 concurrent.

| Allowed | Status | Denied | Status |
|---|---|---|---|
| search_manuals | ✅ wired (search_documents + department filter) | upload_documents | ✅ wired (`POST /documents/upload` now RBAC-gated — 403 for Employee) |
| search_sops | ✅ wired | delete_documents | ✅ wired (`DELETE /documents/{id}` now RBAC-gated — 403 for Employee) |
| search_safety_manuals | ✅ wired | edit_manuals | ✅ wired |
| search_work_instructions | ✅ wired | project_creation | ✅ wired |
| search_inspection_procedures | ✅ wired | hr_information | ✅ wired |
| explain_manuals | ✅ wired (chat synthesis over search results) | executive_reports | ✅ wired |
| explain_sops | ✅ wired | financial_information | ✅ wired |
| machine_status | ⚪ inert (no machine table) | administration | ✅ wired (`/admin/*` is `require_role(ADMIN)`-gated separately) |
| production_status | ⚪ inert (no production table) | prompt_configuration | ✅ wired |
| personal_task_guidance | ✅ wired (chat) | system_settings | ✅ wired |
| training_assistant | ✅ wired (chat + assigned training docs) | | |
| manufacturing_qa | ✅ wired (chat) | | |
| simple_summary | ✅ wired (chat synthesis) | | |

## HR

Model: Sonnet default, escalates to Opus for `workforce_planning`/`leave_analytics`. Knowledge
departments: `hr`. Tools: `search_documents`, `query_analytics` (table-scoped, see
`docs/AUDIT_LOGGING.md`'s SQL guard note), `generate_report`. Quotas: 30 req/min · 300 req/day · 300K
tokens/day · 6M tokens/month · $200/month · 3 concurrent.

| Allowed | Status | Denied | Status |
|---|---|---|---|
| attendance_analytics | ⚪ inert (no attendance table) | machine_control | ✅ wired (denied) |
| performance_analytics | ⚪ inert (no performance table) | engineering_document_upload | ✅ wired |
| leave_analytics | ⚪ inert (no leave table) | production_scheduling | ✅ wired |
| workforce_planning | ⚪ inert (no workforce table) | maintenance_operations | ✅ wired |
| hr_document_search | ✅ wired | | |
| hr_report_generation | ✅ wired (generate_report tool) | | |
| training_analytics | ⚪ inert (no training-records table) | upload_documents | ✅ wired (denied — HR has no document-upload capability) |
| certification_reports | ⚪ inert (no certification table) | delete_documents | ✅ wired (denied) |
| policy_assistant | ✅ wired (chat + HR policy docs) | | |

## Project Manager

Model: Sonnet default, escalates to Opus for `engineering_planning`/`risk_assessment`. Knowledge
departments: `engineering`. Tools: `search_documents`, `query_analytics`, `generate_report`. Quotas:
same as HR. Approval required for: `delete_documents` (see `docs/AUDIT_LOGGING.md`'s Approval note).

| Allowed | Status | Denied | Status |
|---|---|---|---|
| upload_manuals | ✅ wired (`/documents/upload`, now RBAC-gated via the generic `upload_documents` action — see §Extension point) | hr_administration | ✅ wired |
| upload_sops | ✅ wired | payroll | ✅ wired |
| upload_engineering_documents | ✅ wired | ceo_dashboards | ✅ wired |
| upload_project_documents | ✅ wired | enterprise_administration | ✅ wired |
| upload_documents | ✅ wired (the actual action `/documents/upload` checks — see §Extension point) | | |
| delete_documents | ✅ wired (`DELETE /documents/{id}`; `approval_required_actions` still blocks it pending an approval workflow — see `docs/LLM_RBAC_POLICY.md` §4) | | |
| project_creation | ⚪ inert (no projects table — see `docs/LLM_RBAC_ARCHITECTURE.md` §5) | | |
| project_allocation | ⚪ inert | | |
| assign_engineers | ⚪ inert | | |
| project_status_reports | ✅ wired (generate_report tool) | | |
| engineering_search | ✅ wired | | |
| risk_assessment | ✅ wired (query_analytics escalation trigger) | | |
| document_summarization | ✅ wired | | |
| technical_report_generation | ✅ wired | | |
| engineering_qa | ✅ wired | | |

## CEO/Admin (`admin`)

Model: **Dynamic** — Sonnet default, Opus for the union of every other role's escalation triggers.
Knowledge departments: all four. Tools: all three, unrestricted SQL table allowlist. Permissions:
`*` (unlimited). Quotas: unlimited except 10 concurrent requests (a resource-protection ceiling, not
a policy restriction). Plus the `/admin/*` settings routes (Qdrant collection management,
model availability) and user/role management (`GET /users`, `PATCH /users/{id}`).

`/admin/*` is **not** one blanket `require_role(Role.ADMIN)` gate — `routers/admin.py` splits it
three ways by permission, and the analytics group is no longer Admin-only at all:

| Route group | Gate | Who reaches it |
| --- | --- | --- |
| `/admin/collections` (GET/POST/DELETE), `/admin/index-consistency`, `/admin/model-availability` (GET/PUT) | `SYSTEM_SETTINGS` | Admin only |
| `/admin/metrics`, `/admin/query-metrics`, `/admin/gateway-usage`, `/admin/guardrail-analytics` | `VIEW_ANALYTICS` | **every role** |
| `/admin/roles` | `VIEW_ROLES` | roles holding `VIEW_ROLES` |

## Coarse permission catalog (`rbac_permissions`)

Separate from the capability actions tabled above: `core/permissions.py`'s `Permission` enum answers
"can this role reach this feature at all" (nav visibility + endpoint 403s), granted per role via
`llm_rbac.yaml`'s `rbac_permissions` key. The one entry worth calling out here is `VIEW_ANALYTICS`,
which **every role holds** — the read-only Metrics dashboards (latency/tokens, retrieval, gateway
cost in USD, guardrail pass/redact/block counts) are deliberately org-wide, not an admin surface.

Two consequences that are easy to get wrong:

- **Raw guardrail detail is still restricted.** `pipeline.py::_record()` stores each step's `detail`
  verbatim, and those strings embed classifier internals (`best score=0.50`, `Classified as SAFE
  (score=1.00)`, the literal configured scope topics) that the chat UI deliberately hides from
  non-privileged users. `get_guardrail_analytics()` therefore replaces `detail` with
  `"Details restricted"` for callers without `VIEW_AUDIT_LOGS` (CEO/Admin only). Counts, direction,
  check name, and action stay visible to everyone, so the dashboard is fully usable either way.
- **The four `POLICY_*` permissions gate the Policy Copilot**, and are held by
  **CEO and Admin only**: `POLICY_READ`, `POLICY_SIMULATE`, `POLICY_PROPOSE`,
  `POLICY_APPROVE`. Deliberately split rather than reusing
  `MANAGE_GUARDRAIL_POLICIES` for everything — proposing a change and approving
  one are different authorities, and a deployment wanting four-eyes review needs
  to grant `POLICY_PROPOSE` without `POLICY_APPROVE`, which a single combined
  permission cannot express. All four are currently granted together, so CEO's
  and Admin's effective authority is unchanged. Verified live: Employee, HR and
  Project Manager receive 403 from every `/policy-copilot/*` endpoint.
- **`VIEW_ANALYTICS` is necessary but not sufficient for `/evaluation`.** `routers/evaluation.py`
  enforces a stricter `require_role(ADMIN, CEO, PROJECT_MANAGER)`, so HR and Employee hold the
  permission but must not be shown the link — `components/layout/nav.ts` denies both by role via
  `denyRoles`, mirrored on the route by `ProtectedRoute`.

## Extension point — closed

`upload_manuals`/`upload_sops`/etc. are marked ✅ wired because `/documents/upload` exists and works.
`authorize_llm_request()` is now wired into `/documents/upload` (`action="upload_documents"`) and
`/documents/{id}` DELETE (`action="delete_documents"`) the same way it's wired into `/chat`/`/search`
— this table used to flag that gap as open; it no longer applies. `/documents/upload` doesn't know a
document's sub-type at request time, so it checks the single generic `upload_documents` action rather
than PM's four sub-type-specific entries (`upload_manuals`/`upload_sops`/`upload_engineering_documents`/
`upload_project_documents`) — those four remain in the catalog as descriptive, more granular hooks a
future sub-type-aware upload flow could check instead, but nothing currently reads them directly.

Document *visibility* (which department's documents a role can retrieve) was already fully enforced
via `apply_category_policy()` inside `search_documents`/`/search` before this pass; it's now also
enforced on the direct document-browsing REST routes (`GET /documents`, `.../text`, `.../chunks`),
which previously had no authentication or department filtering at all — see
`docs/KNOWLEDGE_ACCESS_CONTROL.md` §6.
