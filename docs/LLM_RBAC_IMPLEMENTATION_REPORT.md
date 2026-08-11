# LLM RBAC — Implementation Report

Final deliverable for the "ATHENA MES AI — Implement Enterprise LLM RBAC" request. Per the request's
own instructions ("analyze first," "do not rewrite working components unnecessarily"), this pass
began with a full inspection of the existing implementation before writing any code. That inspection
found nearly the entire spec already built (all of it in this repo's current, uncommitted working
tree — see `git status`). This report separates what already existed from what this pass actually
changed, and closes with the matrices and remaining-gaps list the spec asked for.

## 1. What already existed (verified, not re-implemented)

- **`services/llm_rbac/{engine,policy_loader,quotas,schemas,tools}.py` + `config/llm_rbac.yaml`** — a
  single centralized policy engine. `authorize_llm_request()` resolves model tier, tool access,
  knowledge departments, SQL table allowlist, and quotas per role from one YAML file, with a
  conservative built-in fallback if the YAML is missing/corrupt.
- **`core/roles.py::Role`** — all 15 roles (4 LLM-RBAC-configured + 11 inert manufacturing roles for
  future MES work); nothing deleted or renamed.
- **`gateway/claude_gateway.py`** — confirmed by grep to be the *only* place `anthropic`/
  `ChatAnthropic` is instantiated anywhere under `backend/app/`. Model choice is tier-only; no code
  path anywhere accepts a raw model-name string from a caller.
- **`services/agents/planner.py`** — zero hardcoded role checks. Every tool/model/department/SQL-table
  restriction is passed in as data and enforced structurally (a disallowed tool is never bound to the
  model, not merely rejected if called).
- **Knowledge-base RBAC** — `apply_category_policy()` narrows document IDs by department/access_roles
  *before* the Qdrant query is built, not by filtering results after the fact.
- **SQL RBAC** — two independent layers already existed: `sql_allowed_tables` role-narrowing plus
  `sql_guard.py`'s unconditional SELECT-only/no-DDL check. Neither can short-circuit the other.
- **Audit logging** — `GatewayUsageLogModel` already recorded both allowed and denied requests with
  role/department/tool_calls/documents_retrieved.
- **Guardrail order** — already matched the spec's required sequence exactly: JWT → LLM RBAC → quota
  → input guardrails → planner → tool RBAC → retrieval/SQL guardrails → gateway → output guardrails →
  citation check.
- **5 of the 6 requested docs** already existed under adjacent names: `LLM_RBAC_ARCHITECTURE.md`,
  `KNOWLEDGE_ACCESS_CONTROL.md`, `AUDIT_LOGGING.md`, `CLAUDE_GATEWAY_MODEL_ROUTING.md` (≈
  `MODEL_ROUTING.md`), plus a bonus `ROLE_PERMISSION_MATRIX.md` not in the spec's list at all.
- **Solid existing test coverage** — `tests/llm_rbac/test_policy_engine.py` (14 tests),
  `tests/llm_rbac/test_category_policy.py` (7 tests), `tests/guardrails/test_sql_guard.py` (9 tests).

## 2. What this pass changed

Three concrete, verified gaps — each either a place direct code inspection showed zero enforcement,
or an extension point the repo's own docs already flagged as open.

### Gap 1 — Reports had zero access control
`routers/reports.py` had no auth dependency on either route, and `ReportModel` had no owner/
department column to filter on even if auth were added — any caller could list or download any
role's generated report. Fixed: `ReportModel` gained `owner_id`/`department`; `generate_report()` now
tags both at creation; `_build_tools()`/`run_agent()` thread `department` through (it previously
reached `record_usage()`'s closure but not `_build_tools()`); `routers/reports.py` now requires
`get_current_user` and filters both routes to reports the caller owns, generated, or can see by
department (NULL department = visible to everyone, matching the same backward-compatible precedent
`apply_category_policy()` already established for documents).

### Gap 2 — `routers/documents.py` had no authentication on any route
Every route — upload, delete, list, get, text, chunks, reindex, versions, entities, permissions —
had zero auth. Two consequences: Employee-must-not-upload/delete was completely unenforced, and the
department filtering already correctly built for chat/search retrieval was trivially bypassable via
a direct `GET /documents` or `GET /documents/{id}/text` call. Fixed: every route now requires
`get_current_user`. Upload and delete are RBAC-gated through `authorize_llm_request()` (same function
chat/search already use) with new `upload_documents`/`delete_documents` catalog actions; upload
accepts optional `department`/`project`/`security_classification` form fields (defaulting department
to the uploader's own); delete blocks with `403 approval_required` when the role's
`requires_approval` flag is set, since no approval-grant workflow exists to honor it. The four read
routes reuse the existing `apply_category_policy()`/`filter_by_category()` — not a reimplementation —
via a new lightweight resolver, `policy_loader.knowledge_departments_for(role)`, that skips the quota/
rate-limit side effects `authorize_llm_request()` would otherwise apply to a plain read.

### Gap 3 — Audit log lacked the spec's `requested_capability` field
`GatewayUsageLogModel` recorded role/department/tool_calls/documents_retrieved but never the `action`
capability name as structured data. Fixed: added a `requested_capability` column, threaded through
`record_usage()`/`record_denied()`, `run_agent()`, and both `chat.py`/`search.py`/`documents.py`
denial sites.

### A pre-existing YAML inconsistency, fixed as part of Gap 2
Project Manager's `approval_required_actions` already listed `delete_documents` (implying
delete-with-approval), but `delete_documents` was never actually in PM's `permissions.allow` — so a
PM's delete would have 403'd at the permission check before ever reaching the approval logic. Added
`upload_documents`/`delete_documents` to PM's allow list (and explicitly to HR's deny list, matching
the pattern already used for HR's other out-of-scope actions).

## 3. Files

**Modified:**
`backend/app/models/report.py`, `backend/app/models/gateway_usage_log.py`,
`backend/app/db/postgres.py`, `backend/app/services/agents/report_agent.py`,
`backend/app/services/agents/planner.py`, `backend/app/gateway/usage_tracker.py`,
`backend/app/routers/chat.py`, `backend/app/routers/search.py`, `backend/app/routers/documents.py`,
`backend/app/routers/reports.py`, `backend/app/services/llm_rbac/policy_loader.py`,
`backend/config/llm_rbac.yaml`, `backend/tests/llm_rbac/test_policy_engine.py`,
`backend/tests/test_audit_logging.py`, `docs/LLM_RBAC_ARCHITECTURE.md`,
`docs/KNOWLEDGE_ACCESS_CONTROL.md`, `docs/AUDIT_LOGGING.md`, `docs/ROLE_PERMISSION_MATRIX.md`.

**Added:**
`backend/tests/llm_rbac/test_quotas.py`, `backend/tests/test_auth_dependencies.py`,
`backend/tests/test_documents_rbac.py`, `backend/tests/test_reports_rbac.py`,
`docs/LLM_RBAC_POLICY.md`, `docs/TOOL_AUTHORIZATION.md`, `docs/LLM_RBAC_TESTING.md`,
`docs/LLM_RBAC_IMPLEMENTATION_REPORT.md` (this file).

**Removed:** none. Nothing already working was deleted or replaced.

## 4. RBAC policy matrix

See `docs/LLM_RBAC_POLICY.md` §2 for the full role × dimension table (model/tools/knowledge/SQL/
quotas/upload-delete) and `docs/ROLE_PERMISSION_MATRIX.md` for the complete per-action allow/deny
catalog with wired-vs-inert status for every permission name in `llm_rbac.yaml`.

## 5. Model routing matrix

See `docs/CLAUDE_GATEWAY_MODEL_ROUTING.md` §2 for the full table. Summary: Employee is Sonnet-only,
structurally (Opus isn't in `tiers_allowed` at all). HR escalates to Opus for `workforce_planning`/
`leave_analytics`. Project Manager escalates for `engineering_planning`/`risk_assessment`. CEO/Admin
escalates for the union of every other role's triggers, computed at config-load time. The model is
never client-chosen — `ChatRequest`/`SearchRequest` have no model/tier field, only the optional
`action` capability name that (indirectly) drives escalation.

## 6. Tool permission matrix

See `docs/TOOL_AUTHORIZATION.md` §1 and §3. Summary: Employee gets `search_documents` only; HR/PM/
Admin get all three (`search_documents`, `query_analytics`, `generate_report`). A disallowed tool is
never bound to the model (structural denial, not a runtime check). `query_analytics` and
`search_documents` each have a second, independent narrowing (SQL table allowlist / knowledge
department) once called; `generate_report` now tags its output with the caller's identity/department
for later read-side filtering (Gap 1).

## 7. Knowledge access matrix

See `docs/KNOWLEDGE_ACCESS_CONTROL.md` §1–§3. Summary: Employee → `manufacturing`; HR → `hr`; Project
Manager → `engineering`; CEO/Admin → all four. A document is visible if its `department` is `NULL`
(unclassified, backward-compatible), matches the caller's knowledge departments, or the document's
own `access_roles` override explicitly lists the caller's role. This rule is now enforced identically
across retrieval (`search_documents`/`/search`) and the direct document-browsing REST API (Gap 2) —
previously only the former.

## 8. Tests executed

```
cd backend && python -m pytest tests/ -q
```
**126 passed**, 0 failed. New this pass: `tests/llm_rbac/test_quotas.py` (11 tests — `check_budget()`
and `concurrency_slot()`, previously stubbed everywhere else), `tests/test_auth_dependencies.py` (4
tests — deactivated-user rejection, previously untested), `tests/test_documents_rbac.py` (6 tests —
structural auth/RBAC-wiring checks on every route), `tests/test_reports_rbac.py` (4 tests — same for
reports). Extended: `tests/llm_rbac/test_policy_engine.py` (+7 cases: PM-denied-HR-action,
Admin-routine-stays-Sonnet, `knowledge_departments_for()`, and the new upload/delete permission
cases per role), `tests/test_audit_logging.py` (+`requested_capability` assertions). Full mapping of
spec §16's test list to actual test functions is in `docs/LLM_RBAC_TESTING.md` §4.

Note on the local run: this sandbox's Python environment was missing `redis`, `sqlparse`, and
`qdrant_client` (packages this repo's own `requirements.txt` already lists) — installed them to run
the suite; no code required them to be absent, this was purely a stale local environment.

## 9. Remaining gaps (deliberately out of scope this pass)

- **11 manufacturing roles stay inert.** No `llm_rbac.yaml` entry, no underlying data/tool — adding
  a YAML entry alone would enforce a policy with nothing behind it. See `docs/LLM_RBAC_POLICY.md` §5.
- **No approval-grant workflow.** `delete_documents` for Project Manager is now correctly *blocked*
  (not silently allowed) pending approval, but there's no `ApprovalRequest` model or grant mechanism
  to actually approve one. Building a real workflow was out of scope — the spec explicitly says not
  to fake capabilities that don't exist.
- **Some `routers/documents.py` sub-routes are login-gated only.** `progress`, `versions`,
  `entities`, `permissions` grant/list/revoke, and `reindex` now require `get_current_user` but don't
  have a dedicated RBAC action of their own. Upload/delete (the two spec explicitly calls out) and
  the read paths that leak document content (list/get/text/chunks) got the fine-grained treatment;
  the rest was left bounded rather than expanded further.
- **No bulk document re-categorization.** Documents uploaded before this pass keep `department=NULL`
  (visible to everyone, by the same backward-compatible rule new uploads used to rely on before this
  pass gave them a default). An admin-only bulk "assign department to existing documents" action
  doesn't exist yet.
- **`security_classification`/`project` remain descriptive, not enforcement inputs.** Both are
  accepted on upload and stored, but `apply_category_policy()` still only branches on `department`/
  `access_roles` — a document marked `security_classification="restricted"` gets no additional
  scrutiny from this rail. Unchanged from before this pass; flagged again here since it's adjacent to
  what did change.
- **Report visibility inherits department, not fine-grained content classification.** A report
  generated under an HR-department session is visible to any HR-department caller, not gated per
  report the way individual documents can be via `access_roles`. Matches the granularity the spec's
  §11 examples describe (role-level report categories), not per-document-level overrides.

## 10. Future MES/MCP integration points

- **New manufacturing roles**: add a `roles.<role>` entry to `llm_rbac.yaml` (model/knowledge/tools/
  quotas) once the underlying data model (machine/production/attendance tables) and at least one real
  tool exist to gate. `policy_loader.py`'s fallback mechanism means an unconfigured role is never
  unsafe in the meantime — it gets conservative Employee-equivalent defaults automatically.
- **MCP tool execution**: `services/agents/planner.py::_build_tools()` is the single integration
  point — a new MCP-backed tool would be added to `all_tools` there and gated by the same
  `allowed_tools` mechanism every existing tool uses, with no changes needed to `engine.py` itself.
- **Approval workflow**: `PolicyDecision.requires_approval` and `llm_rbac.yaml`'s
  `approval_required_actions` are already the policy signal a real approval system would consume —
  see `docs/LLM_RBAC_POLICY.md` §4 for exactly where in `routers/documents.py` that signal is
  currently read (and currently just blocks, pending a real grant mechanism).
- **Model-routing complexity classifier**: `engine.py::_resolve_tier()`'s public interface
  (`role`, `action`) doesn't need to change to swap the current action-name-based escalation for an
  NLP intent classifier later — see `docs/CLAUDE_GATEWAY_MODEL_ROUTING.md` §3.
