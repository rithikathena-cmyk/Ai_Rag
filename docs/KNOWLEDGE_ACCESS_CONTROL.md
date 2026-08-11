# Knowledge Access Control (LLM RBAC)

Extends `docs/GUARDRAILS_ARCHITECTURE.md` §4 ("Retrieval rail") and §9 (which named this exact
extension point: *"add a category/department field to `DocumentModel`, a `Role → allowed categories`
policy... and a new `apply_category_policy()` function called alongside `apply_permission_policy()`"*).
This document covers what was built.

## 1. New `DocumentModel` fields

| Field | Type | Default | Purpose |
|---|---|---|---|
| `department` | `str \| None` | `NULL` | Which knowledge department this document belongs to (`manufacturing`/`hr`/`engineering`/`executive` — matches `llm_rbac.yaml`'s `departments` list). `NULL` = unclassified, treated as visible to every role (see §3). |
| `access_roles` | `JSONB \| None` | `NULL` | Optional explicit role-allowlist override for one-off exceptions on top of the department default — e.g. a single engineering document an Employee should also see. |
| `security_classification` | `str` | `"internal"` | `public`/`internal`/`confidential`/`restricted`. **Distinct from the existing `classification` field** (see §2). |
| `project` | `str \| None` | `NULL` | Free-text project code/name, for project-scoped filtering. No new `projects` table — out of scope, see `docs/LLM_RBAC_ARCHITECTURE.md` §5. |
| `owner_id` | `UUID \| None` | `NULL` | FK to `users.id`. |
| `approval_status` | `str` | `"approved"` | `draft`/`pending_approval`/`approved`/`rejected`. All pre-existing rows default to `approved` — nothing currently visible becomes invisible on deploy day. |

`version_number`/`lineage_id`/`is_latest_version` (pre-existing) already cover the spec's "Version"
requirement — no new field was needed there.

## 2. `department`/`security_classification` vs. the existing `classification` field

`DocumentModel.classification` already existed before LLM RBAC — it's a **content-taxonomy** label
(`services/classification/` sets it to things like "SOP", "Policy", "Research Paper") used for search
filtering (`document_type`/`classification` query params on `/search`). It has nothing to do with
access control.

`security_classification` (new) is an **access-control sensitivity** label
(`public`/`internal`/`confidential`/`restricted`). The two can and will diverge — an "SOP" (content
classification) can be `restricted` (security classification) or `public`. Don't confuse them; they
answer different questions ("what kind of document is this" vs. "who should see it").

`security_classification` is currently descriptive/audit-facing only — `apply_category_policy()`
(§3) doesn't branch on it yet; `department` + `access_roles` are the two fields that actually gate
visibility today. It's there so the audit trail and a future stricter policy (e.g. "restricted
documents also require an explicit grant regardless of department") have somewhere to read from
without another migration.

## 3. The two-stage filter

`retrieval/metadata_filter.py::resolve_document_ids()` narrows a candidate document-ID set in this
order, before anything reaches Qdrant (i.e. before it can ever enter an LLM's context window):

1. **Metadata filters** (`document_type`/`classification`/`language`/`latest_version_only`) —
   unchanged, pre-existing.
2. **Category policy** (new) — `services/guardrails/retrieval_permissions.py::apply_category_policy()`,
   called when a `role` is supplied. Pure function, unit-tested without a database
   (`tests/llm_rbac/test_category_policy.py`), same shape as the existing `apply_permission_policy()`
   next to it:

   ```python
   def apply_category_policy(candidate_ids, doc_departments, doc_access_roles, role, knowledge_departments):
       return [
           d for d in candidate_ids
           if doc_departments.get(d) is None                        # unclassified — public
           or doc_departments.get(d) in knowledge_departments        # role's department matches
           or role in (doc_access_roles.get(d) or [])                # explicit per-document override
       ]
   ```

3. **Permission policy** (pre-existing, unchanged) — `apply_permission_policy()`, narrows further by
   per-user `PermissionModel` grants when a `user_id` is supplied.

Both narrowing stages are opt-in (a caller that supplies neither `role` nor `user_id` gets the
pre-existing, unfiltered behavior) — but `/chat` and `/search` now always supply both, since they're
gated behind `get_current_user` (see `docs/LLM_RBAC_ARCHITECTURE.md` §1).

## 4. Semantics: why `NULL` department = visible to everyone

Same opt-in-ACL principle the existing permission rail already uses (`GUARDRAILS_ARCHITECTURE.md` §4:
*"a document with zero permission rows is public"*) — applied consistently here: a document with no
`department` set is unclassified, not implicitly restricted. This is a deliberate backward-compatible
choice for the migration: every document that exists before this feature ships has `department=NULL`,
so nothing currently visible becomes invisible the day this deploys. Category-based restriction only
takes effect once a document is explicitly assigned a department.

**Practical consequence, stated plainly**: until documents are actually categorized (see the
extension point below), `apply_category_policy()` is enforced-but-not-yet-restrictive for the
existing corpus — it's correct code, not yet exercised against real data.

## 5. Extension point: categorizing documents — closed for new uploads

`POST /documents/upload` now accepts optional `department`/`project`/`security_classification` form
fields, and always sets `owner_id` to the uploader. When `department` is omitted, it defaults to the
uploader's resolved department (`decision.department` from `authorize_llm_request()`) rather than
falling through to `NULL` — so a new upload is department-scoped by default, not merely capable of
being scoped. `POST /documents/upload` itself is now also RBAC-gated
(`action="upload_documents"`) — see `docs/TOOL_AUTHORIZATION.md` §4.

Still open: an admin-only bulk "categorize existing documents" action for the corpus that predates
this change (every document uploaded before this pass keeps `department=NULL`, i.e. visible to
everyone, per §4's backward-compatibility rule). No route or UI for that bulk operation exists yet.

## 6. Read-side enforcement now matches retrieval

Before this pass, `apply_category_policy()` only ran inside the `search_documents`/`/search`
retrieval path — the direct document-browsing REST API (`GET /documents`, `GET /documents/{id}`,
`GET /documents/{id}/text`, `GET /documents/{id}/chunks`) had no authentication at all, so it
trivially bypassed the department filtering retrieval enforced. All four routes now require
`get_current_user` and reuse the exact same `filter_by_category()` function retrieval calls — not a
reimplementation, the same call — via a new lighter-weight resolver,
`services/llm_rbac/policy_loader.py::knowledge_departments_for(role)`, that returns the same
`knowledge_departments` `authorize_llm_request()` would, without that function's rate-limit/budget
side effects (which are scoped to Claude Gateway requests, not plain reads — see
`docs/TOOL_AUTHORIZATION.md` §4). A document that exists but isn't visible to the caller's department
returns `404`, not `403`, matching this codebase's existing don't-confirm-existence convention (see
`routers/reports.py`'s download endpoint for the same choice).
