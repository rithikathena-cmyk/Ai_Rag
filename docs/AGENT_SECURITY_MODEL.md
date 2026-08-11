# Agent Security Model

How this system's agent (the LangGraph planner + its 3 tools) is actually governed today —
authentication, authorization, tool-level protection, and what's explicitly not implemented yet.
Cross-references `CLAUDE_GATEWAY_ARCHITECTURE.md` and `GUARDRAILS_ARCHITECTURE.md`, which cover the
mechanisms this document assembles into a security model.

## 1. Identity

`services/auth/` (built in the prior increment): password hashing (PBKDF2-HMAC-SHA256, 390k
iterations), JWT access (30 min) / refresh (7 day) tokens, `get_current_user` — a FastAPI dependency
that reads the bearer token, looks up the user **fresh from Postgres on every request** (not from
the token payload), and rejects deactivated accounts. A role or deactivation change takes effect on
the very next request, not the next login.

**Where auth is actually enforced today**: `/admin/*` (Qdrant collection management, metrics,
gateway usage, guardrail analytics), `GET /users`, `PATCH /users/{id}` — all via
`require_role(Role.ADMIN)` (`services/auth/rbac.py`). **`/chat`, `/search`, `/documents/*` are still
not auth-gated** — gating those routes would change what an anonymous demo user can do, which was
out of scope for the login-flow work. `request.user_id` on `/chat` and `/search` remains an
optional, *unauthenticated* field.

**The frontend now has a real login flow** (`frontend/app/views/login.py` +
`api_client.py::login/get_current_user_info/logout`): email/password login, JWT access+refresh
tokens held in `st.session_state` (never a module-level variable — this Streamlit app can serve
multiple browser sessions from one process, so anything shared at module scope would leak between
users), silent refresh-and-retry on an expired access token (`api_client._request`'s 401 handling),
and a self-registration form (`POST /users`, already existed). The `/admin/*` tabs
(`views/admin.py`) that previously 401'd with no way to authenticate now work end-to-end once
logged in as an admin — including a bootstrap path for the very first admin account
(`db/postgres.py::_bootstrap_admin_user`, seeded from `BOOTSTRAP_ADMIN_EMAIL`/
`BOOTSTRAP_ADMIN_PASSWORD`, since `PATCH /users/{id}` — the only way to *grant* admin — itself
requires an existing admin).

**What login does *not* close**: `/chat`'s User ID field now defaults to the logged-in user's ID
(`views/chat.py`), but it's still just a form field — nothing on the backend checks it against the
caller's bearer token. A logged-in user can still hand-edit that field to another user's ID and get
retrieval filtered as if they were that user, since `/chat` doesn't require (or even accept) a
bearer token at all. Closing this means the same thing §1 said before this pass: gate `/chat`/
`/search` behind `get_current_user` and always use the *verified* user's ID, ignoring any
client-supplied `user_id` once that's in place.

## 2. Roles

`core/roles.py::Role` — `ADMIN`/`USER` (original, load-bearing — existing data/API contracts depend
on these two values) plus a set of manufacturing-domain role names (`plant_manager`,
`maintenance_engineer`, `quality_engineer`, etc.) added in the prior increment for a future
manufacturing-agent buildout. **These manufacturing roles have no agent, tool, or data model behind
them yet** — see `docs/LLM_GATEWAY_ANALYSIS.md` §0. `require_role(*roles)` is the enforcement
primitive; it's a `Depends()` factory, applied per-route or per-router.

## 3. Data-level authorization: retrieval permissions

The one piece of this task that turns "roles exist" into "roles actually restrict what an agent can
retrieve": `services/guardrails/retrieval_permissions.py`, detailed in
`GUARDRAILS_ARCHITECTURE.md` §4. Summary of the security-relevant part:

- Enforcement point: `retrieval/metadata_filter.py::resolve_document_ids()` — before any chunk ID
  reaches Qdrant, so a permission-denied document's content never enters the LLM's context window,
  not just "isn't cited."
- Trust boundary: **this only restricts what's returned once a `user_id` is supplied.** Since
  `/chat`/`/search` aren't authenticated (§1), `user_id` today is caller-asserted, not
  verified — a client can currently claim any `user_id` on an unauthenticated `/chat` call.
  **This is a real gap to close before this rail is a genuine security boundary**: either gate
  `/chat`/`/search` behind `get_current_user` and always pass the *verified* user's ID (ignoring any
  client-supplied `user_id`), or treat `user_id`-driven filtering as a UX feature (hide documents a
  user *usually* shouldn't see) rather than a security guarantee until that's done.
- What it does enforce correctly, once wired to a real identity: a document with any
  `PermissionModel` row becomes invisible to every user except those explicitly granted access —
  including via the agent's `search_documents` tool, not just the direct `/documents/*` API.

## 4. Execution-level authorization: SQL guard

`services/guardrails/deterministic/sql_guard.py::validate_select()` (moved from `sql_agent.py`,
§5 of `GUARDRAILS_ARCHITECTURE.md`) is the only tool-execution guard in this system today: the
`query_analytics` tool's SQL is restricted to a single `SELECT`, an allow-listed table set
(explicitly excluding `users`, which holds `password_hash`), and a forbidden-keyword blocklist
(DDL/DML, superuser functions). Every query also gets wrapped in an outer `LIMIT 500` and is always
rolled back, never committed, as defense in depth beyond the syntactic check.

This is role-blind — any caller who can invoke the planner can run any allowed analytics query.
There is no per-role restriction on *which* analytics tables/columns a given role can see (e.g. no
"only `ADMIN` can query `permissions`"). Adding that would mean parameterizing
`ALLOWED_TABLES`/`allowed_tables` in `validate_select()` by the calling user's role — the function
signature already supports a caller-supplied `allowed_tables` set for exactly this.

## 5. What's not covered: MCP / tool-execution approval

The original request's Task 6 (protect MCP tools, gate dangerous actions like `shutdown_machine()`
behind role + approval-workflow checks) has **no MCP integration and no such tool to protect** in
this repo — confirmed in `docs/LLM_GATEWAY_ANALYSIS.md` §0/§2. The 3 tools that do exist
(`search_documents`, `query_analytics`, `generate_report`) are all read-only or additive
(a report file is created, never a system state change) — none of them need an approval workflow
under any reasonable policy, since none can mutate manufacturing state (there is no manufacturing
state in this system).

**If/when MCP tools with real side effects are added**, the shape this security model would need:

1. An `ApprovalRequest` model (status: pending/approved/denied, requested_by, approved_by, tool_name,
   arguments, created_at) — doesn't exist yet.
2. A `services/guardrails/execution/` module: `validate_tool_call(tool_name, arguments, user) ->
   Allow | RequireApproval | Deny`, consulting role + a per-tool risk policy.
3. A dispatch-time hook — LangGraph's `ToolNode` would need either a pre-execution wrapper per tool
   or a custom node that intercepts calls to risky tools before they run, creates an
   `ApprovalRequest` instead of executing, and returns a "pending approval" tool result the planner
   can relay to the user.

None of this exists today because there's nothing in this repo for it to protect yet.

## 6. Guardrail-level protections (recap)

Full detail in `GUARDRAILS_ARCHITECTURE.md`. Summary of what protects the agent loop itself, not
just data access:

- **Input**: prompt-injection / jailbreak-pattern / destructive-intent / scope regex checks, PII
  redaction — block before the planner ever sees the message.
- **Output**: system-prompt-leak check (blocks), PII redaction (always rewrites), citation/
  confidence check (flags, never blocks — new this pass).
- **SQL execution**: §4 above.
- **Retrieval**: §3 above.

## 7. Deployment considerations

- Auth is currently **partial by design** — see §1. Before exposing this system beyond a trusted
  internal network, gate `/chat` and `/search` behind `get_current_user` and stop trusting a
  client-supplied `user_id`.
- The manufacturing `Role` values (§2) exist in the schema but grant no actual access today —
  assigning a user `maintenance_engineer` currently has zero effect beyond `require_role()` checks
  that explicitly list it (none do yet). Don't rely on these role names as evidence of manufacturing
  RBAC being live.
- `GatewayUsageLogModel` stores `agent_name`/`model`/token counts/cost per request, not the
  request/response content itself — no additional PII exposure from usage tracking.
- `BOOTSTRAP_ADMIN_EMAIL`/`BOOTSTRAP_ADMIN_PASSWORD` (`.env`) seed one admin account on startup if
  set — set them once to create the first admin, then either unset them or rotate that account's
  password (the bootstrap check is keyed on the email already existing, so leaving them set is
  idempotent/harmless but the plaintext password sits in `.env` for as long as they're there).

## 8. Extension points

- Verify `user_id` via `get_current_user` on `/chat`/`/search` instead of trusting a client-supplied
  field — the single highest-value next step for this security model.
- Role-scoped SQL guard (§4) — parameterize `allowed_tables` by role.
- Document-category → role access matrix (`GUARDRAILS_ARCHITECTURE.md` §9) — the actual "Maintenance
  Engineer can't see financial documents" capability the original request described.
- MCP execution rail + approval workflow (§5) — once there are real manufacturing tools to protect.
