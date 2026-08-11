# LLM RBAC — Policy Reference

The spec asked for a single role→policy table (§17). `backend/config/llm_rbac.yaml` is the actual
source of truth (loaded by `services/llm_rbac/policy_loader.py`) and `docs/ROLE_PERMISSION_MATRIX.md`
already documents its allow/deny catalog per role in detail. This document is the companion piece:
the governance dimensions (model/tools/knowledge/SQL/quotas/approval) side by side, in one table, so
you don't have to reconstruct it from the YAML by hand. If you change `llm_rbac.yaml`, update this
table in the same commit — same rule `ROLE_PERMISSION_MATRIX.md` already states for itself.

## 1. The eleven governance dimensions, and where each is enforced

| # | Dimension | Enforced by | Config surface |
|---|---|---|---|
| 1 | Claude model access | `services/llm_rbac/engine.py::_resolve_tier()` | `roles.<role>.model.tiers_allowed`/`default_tier`/`escalate_to_opus_for` |
| 2 | Tool access | `services/agents/planner.py::_build_tools()` — disallowed tools are never bound to the model | `roles.<role>.tools` |
| 3 | Knowledge-base access | `services/guardrails/retrieval_permissions.py::apply_category_policy()` | `roles.<role>.knowledge_departments` |
| 4 | SQL table access | `services/guardrails/deterministic/sql_guard.py::validate_select()` | `roles.<role>.sql_allowed_tables` |
| 5 | Document access (read) | `routers/documents.py`'s read routes, reusing `apply_category_policy()` | same as #3 |
| 6 | Document access (write) | `routers/documents.py::upload_document()`/`delete_document()` via `authorize_llm_request(action=...)` | `roles.<role>.permissions.allow/deny` (`upload_documents`, `delete_documents`) |
| 7 | Token quotas | `services/llm_rbac/quotas.py::check_budget()`/`increment_usage()` | `roles.<role>.quotas.{daily,monthly}_tokens` |
| 8 | Request quotas | `services/llm_rbac/quotas.py::check_budget()` (daily) + `services/rate_limit/limiter.py` (per-minute) | `roles.<role>.quotas.{requests_per_minute,daily_requests}` |
| 9 | Cost quotas | `services/llm_rbac/quotas.py::check_budget()` | `roles.<role>.quotas.monthly_cost_usd` |
| 10 | Concurrent requests | `services/llm_rbac/quotas.py::concurrency_slot()` | `roles.<role>.quotas.max_concurrent_requests` |
| 11 | Agent capabilities | `services/llm_rbac/engine.py::_check_permission()` | `roles.<role>.permissions.allow/deny` |
| — | Report capabilities | `services/agents/report_agent.py::generate_report()` tags `owner_id`/`department`; `routers/reports.py` filters on read | Inherits #3's `knowledge_departments`, not separately configured |
| — | Approval requirements | `PolicyDecision.requires_approval`, checked in `routers/documents.py::delete_document()` | `roles.<role>.approval_required_actions` |

## 2. Role → policy, side by side

| | Employee (`user`) | HR | Project Manager | CEO/Admin |
|---|---|---|---|---|
| **Model** | Sonnet only, structurally | Sonnet default → Opus for `workforce_planning`/`leave_analytics` | Sonnet default → Opus for `engineering_planning`/`risk_assessment` | Sonnet default → Opus for the union of every other role's triggers (dynamic) |
| **Tools** | `search_documents` | `search_documents`, `query_analytics`, `generate_report` | same as HR | same as HR |
| **Knowledge departments** | `manufacturing` | `hr` | `engineering` | all four |
| **SQL tables** | none (`[]`) | `conversations`, `messages`, `eval_queries`, `eval_runs`, `reports` | `documents`, `chunks`, `entities`, `terms`, `reports`, `conversations`, `messages` | unrestricted (`null` — `sql_guard`'s own 8-table default) |
| **Document upload/delete** | denied (both) | denied (both) | allowed (delete requires approval — currently **blocked**, no workflow exists) | allowed, no approval needed |
| **Requests/min · daily · daily tokens · monthly tokens · monthly cost · concurrent** | 20 · 200 · 100K · 2M · $50 · 2 | 30 · 300 · 300K · 6M · $200 · 3 | 30 · 300 · 300K · 6M · $200 · 3 | unlimited · unlimited · unlimited · unlimited · unlimited · 10 |
| **Permission catalog** | see `docs/ROLE_PERMISSION_MATRIX.md` §Employee | §HR | §Project Manager | `*` (wildcard) |

The 11 manufacturing roles (`plant_manager`, `operator`, etc.) have no entry in `llm_rbac.yaml` and
fall through to `policy_loader.py::_fallback_role_config()` — conservative Employee-equivalent
defaults (Sonnet-only, `search_documents` only, `permissions_allow={"*"}` since there's no catalog to
check a role that isn't configured against). They stay inert until a future MES increment gives them
real YAML entries and underlying data/tools — see §5 below.

## 3. What changed in this pass vs. what already existed

`llm_rbac.yaml`'s four configured roles, their model/knowledge/SQL/quota shape, and the guardrail
order were already fully built (see `docs/LLM_RBAC_ARCHITECTURE.md`). This pass added two entries
that were missing or inconsistent:

- **`upload_documents`/`delete_documents` as explicit permission-catalog actions.** Neither existed
  before — `routers/documents.py` had no LLM RBAC gate at all (see `docs/KNOWLEDGE_ACCESS_CONTROL.md`
  §6, `docs/AUDIT_LOGGING.md` §3). Employee already denied both by name (unused, since nothing
  checked it); HR now explicitly denies both too (previously implicit-by-absence); Project Manager
  now explicitly allows both.
- **Fixed a latent inconsistency**: Project Manager's `approval_required_actions` already listed
  `delete_documents`, implying delete-with-approval, but `delete_documents` was never in PM's
  `permissions.allow` — so a PM's delete would have 403'd at the permission check, before ever
  reaching the approval logic. Adding it to `permissions.allow` makes the approval-required path
  actually reachable (see §4).

## 4. Approval requirements — what "required" means today

`PolicyDecision.requires_approval` was already computed by `engine.py` before this pass, but nothing
consumed it (`docs/AUDIT_LOGGING.md` §3 flagged this explicitly: "no enforcement consumes it yet").
`routers/documents.py::delete_document()` is now the one consumer: if `decision.requires_approval` is
true, the request is **blocked with `403 approval_required`**, not silently allowed. This is a
deliberate choice, not a partial implementation — there is no `ApprovalRequest` model or grant
mechanism anywhere in this repo, and building one wasn't in scope for this pass (the spec explicitly
says not to fake capabilities that don't exist). Blocking honestly is the correct behavior until a
real approval workflow ships; silently allowing would defeat the point of marking the action
approval-required in the first place.

## 5. Extension point

Wiring a real manufacturing role (e.g. `plant_manager`) into this table means: add a `roles.<role>`
entry to `llm_rbac.yaml` with its own model/knowledge/tools/quotas, and — separately — build whatever
underlying data/tool that role's permissions should actually gate (there is currently no machine/
production/attendance table in this schema at all; see `docs/LLM_GATEWAY_ANALYSIS.md` §0). Until
both exist, adding a YAML entry alone would enforce a policy with nothing behind it to enforce.
