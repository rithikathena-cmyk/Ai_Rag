# LLM RBAC — Testing

What's tested, where, and — since this suite has no real Postgres/Qdrant/Redis fixture (every test
either exercises a pure function or fakes the I/O boundary) — how each file gets around that. If
you're adding a new RBAC check, this is the map of which existing pattern to copy.

## 1. The no-real-infra convention

`backend/tests/` has no `conftest.py` spinning up a database or Redis. Every existing test picks one
of three approaches, and every test added in this pass follows the same three:

| Approach | When to use it | Examples |
|---|---|---|
| Pure function, no I/O | The function under test doesn't touch a DB/Redis/network at all | `apply_category_policy()`, `validate_select()`, `_resolve_tier()` |
| Fake session/client, monkeypatched in | The function needs *a* session/client but the test only cares about what gets read/written, not real persistence | `_FakeSession` (`test_audit_logging.py`), `_FakeRedis`/`_BrokenRedis` (`test_rate_limit.py`) |
| Structural/contract check | The behavior is "this route requires this dependency" or "this function is called with these arguments" — the actual request/response cycle needs infra this suite doesn't stand up | `test_chat_auth.py`'s `_depends_on_get_current_user()` |

## 2. Coverage by module

| Module | File(s) | What's covered |
|---|---|---|
| `services/llm_rbac/policy_loader.py` | `tests/llm_rbac/test_policy_engine.py` | Role config shape (tiers/tools/escalation/fallback), the `enabled: false` kill switch, missing-config fallback, `knowledge_departments_for()`'s kill-switch behavior (added this pass) |
| `services/llm_rbac/engine.py` | `tests/llm_rbac/test_policy_engine.py` | Full permission matrix: Employee/HR/PM/Admin allow/deny/escalate cases, department defaulting, the disabled-kill-switch always-allow path. Rate limit and budget checks are monkeypatched to no-ops here (see §3) — deliberately, so these tests isolate pure policy logic. Added this pass: PM-denied-HR-action, Admin routine-action-stays-Sonnet, and the new `upload_documents`/`delete_documents` allow/deny cases per role |
| `services/llm_rbac/quotas.py` | `tests/llm_rbac/test_quotas.py` (new this pass) | `check_budget()`'s actual threshold logic (daily requests/tokens, monthly tokens/cost, each raising 429) and the case where a role has no quotas configured (skips the DB lookup entirely) — previously untested, always stubbed elsewhere. `concurrency_slot()`: allows up to the limit, 429s over it, releases its slot on exit, `None` limit skips Redis entirely, fails open on a Redis outage |
| `services/guardrails/retrieval_permissions.py` | `tests/llm_rbac/test_category_policy.py` | `apply_category_policy()`/`filter_by_category()` — unclassified docs are public, department match, access_roles override, mixed candidate sets, admin's full department set, the `knowledge_departments=None` unrestricted case |
| `services/guardrails/deterministic/sql_guard.py` | `tests/guardrails/test_sql_guard.py` | SELECT-only enforcement, multi-statement/DDL/DML rejection, table allowlist (including role-narrowed `allowed_tables`), forbidden-function blocklist, CTE handling |
| `gateway/usage_tracker.py` | `tests/test_audit_logging.py` | `record_usage()`/`record_denied()` row shape, the quota-increment side effect when `user_id` is present, denial-reason truncation, best-effort failure handling. Added this pass: `requested_capability` propagation on both allowed and denied rows |
| `services/auth/dependencies.py::get_current_user` | `tests/test_auth_dependencies.py` (new this pass) | Deactivated user → 403 `user_inactive` (previously untested — every existing fake-user fixture across the suite hardcodes `is_active=True`), active user accepted, missing credentials → 401, a token for a since-deleted user → 401 |
| `services/auth/rbac.py::require_role` | `tests/test_rbac.py` | Matching/insufficient role, multiple allowed roles, unauthenticated request |
| `routers/chat.py`, `routers/search.py` | `tests/test_chat_auth.py` | Both routes require `get_current_user`; `ChatRequest`/`SearchRequest` no longer accept a client-supplied `user_id`; both accept the optional `action` field |
| `routers/documents.py` | `tests/test_documents_rbac.py` (new this pass) | Every route requires `get_current_user`; upload accepts optional `department`/`project`/`security_classification` form fields; upload/delete are RBAC-gated via `authorize_llm_request(action=...)`; delete checks `requires_approval`; the four read routes reuse `filter_by_category` |
| `routers/reports.py` | `tests/test_reports_rbac.py` (new this pass) | Both routes require `get_current_user`; `_visibility_filter()` returns `None` (unrestricted) when the kill switch is off and a real clause otherwise |
| `services/rate_limit/limiter.py` | `tests/test_rate_limit.py` | Under/over the per-window limit, disabled mode, fail-open on Redis outage |

## 3. Why rate-limit/budget are stubbed in `test_policy_engine.py` specifically

`_stub_rate_limit_and_budget` (an autouse fixture in that file) monkeypatches
`engine.check_rate_limit` and `engine.quotas.check_budget` to no-ops. This is intentional scope
control, not a coverage gap: `test_policy_engine.py` is about permission/tier/department logic, and
those two checks are I/O-bound side quests that would otherwise force every test in that file to also
fake a Redis client and a Postgres session. `tests/llm_rbac/test_quotas.py` (this pass) is what
actually exercises `check_budget()`'s and `concurrency_slot()`'s real logic, in isolation, with the
fakes contained to one file.

## 4. What the spec's test list (§16) maps to, item by item

| Spec case | Test |
|---|---|
| Employee → machine manual → ALLOW | `test_authorize_employee_allowed_for_its_own_action` (uses `manufacturing_qa`; no literal `machine_status` route exists — see `docs/ROLE_PERMISSION_MATRIX.md`'s inert-action note) |
| Employee → HR records → DENY | `test_authorize_employee_denied_for_hr_only_action` |
| Employee → Opus → DENY | `test_employee_role_is_sonnet_only` (structural: Opus isn't in `tiers_allowed` at all, so there's no request that could produce it) |
| HR → attendance → ALLOW | `test_authorize_hr_escalates_to_opus_for_workforce_planning` (closest wired analogue — no attendance table exists; see `docs/ROLE_PERMISSION_MATRIX.md`) |
| HR → machine shutdown → DENY | `test_authorize_hr_denied_for_pm_only_action` (closest analogue — no machine-control tool exists in this repo at all) |
| HR → Opus complex analysis → ALLOW | `test_authorize_hr_escalates_to_opus_for_workforce_planning` |
| Project Manager → engineering document → ALLOW | `test_department_defaults_from_role_when_user_has_none` (PM's `knowledge_departments`) + `test_authorize_project_manager_allowed_to_upload_and_delete_documents` (new) |
| Project Manager → HR payroll → DENY | `test_authorize_project_manager_denied_for_hr_only_action` (new) |
| CEO → enterprise report → ALLOW | `test_authorize_admin_wildcard_allows_any_action` + `test_authorize_admin_allowed_routine_action_at_sonnet` (new) |
| Admin → all configured capabilities → ALLOW | `test_authorize_admin_wildcard_allows_any_action` |
| Unauthorized Qdrant document → DENY/FILTER | `tests/llm_rbac/test_category_policy.py` (full suite) |
| Unauthorized SQL table → DENY | `tests/guardrails/test_sql_guard.py` |
| Quota exceeded → 429 | `tests/llm_rbac/test_quotas.py` (new) |
| Unauthorized model → DENY | `test_employee_role_is_sonnet_only` (structural — see above) |
| Expired/deactivated user → DENY | `tests/test_auth_dependencies.py::test_deactivated_user_is_rejected_with_403` (new) |

## 5. Running the suite

```
cd backend
python -m pytest tests/ -q
```

126 tests, no external services required — every test in the suite (existing and added this pass)
fakes its I/O boundary rather than needing a running Postgres/Qdrant/Redis instance.
