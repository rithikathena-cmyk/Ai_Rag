# System Architecture

The single "whole picture" reference: system components, the multi-agent execution layer, and the
guardrail pipeline, in one diagrammatic document. For exact detail, follow the cross-references rather
than expecting duplication here: `REQUEST_PIPELINE.md` (the precise, ordered guardrail check tables
with function/module references), `GUARDRAILS_ARCHITECTURE.md` (why each guardrail exists),
`LLM_RBAC_ARCHITECTURE.md` (the permission model), `AGENT_SECURITY_MODEL.md` (security boundaries),
`CLAUDE_GATEWAY_ARCHITECTURE.md` (model access), `ROLE_PERMISSION_MATRIX.md` (the concrete per-role
table).

## 1. System components

```
React (Vite + TS + Tailwind v4)
        │  REST + JWT bearer — axios + @tanstack/react-query
        ▼
FastAPI backend (17 routers)
        │                                   │
        ▼                                   ▼
PostgreSQL                              Qdrant
users, conversations, messages          vector store — dense + sparse
(+ trace JSONB), documents, audit_events,  BM25 hybrid search per
guardrail_policies (+ versions),          document collection
approvals, projects, eval_*,
gateway_usage_logs
        │
        ▼
Claude Gateway → Anthropic API (haiku / sonnet / opus — tier resolved per role)
        +
In-process ML: BGE-M3 embeddings, cross-encoder reranker, GLiNER NER,
Presidio PII, toxic-bert, NLI groundedness classifier, DeBERTa injection classifier
```

No Alembic/migrations exist — schema is managed via SQLAlchemy `create_all()`, not versioned
migrations.

## 2. Full request flow — pipeline, agent, and guardrails together

```
User
  │
  ▼
Authentication ──────────── services/auth/dependencies.py::get_current_user
  ▼
RBAC / Authorization ────── services/llm_rbac/engine.py::authorize_llm_request
  ▼
┌─────────────────────────────────────────────────────────────────────┐
│ INPUT GUARDRAILS — 14 checks, pipeline.py::run_input_guardrails      │
│ length → secrets → injection → destructive-intent → custom word/    │
│ regex → scope → semantic-risk → deberta-injection → scope-semantic  │
│ → toxicity → PII (Presidio) → PII (GLiNER) → PII redaction          │
│ (scope blocks deferred — a later, more specific block always wins)  │
│ then: risk_analysis → policy_check   (orchestrator_graph.py)        │
└─────────────────────────────────────────┬───────────────────────────┘
                                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│ AGENT / RAG                                                          │
│                                                                       │
│   router.py ── classifies intent (fast-tier model, confidence-      │
│                 gated) → picks a specialist agent. Business          │
│                 classification only — never a security decision.    │
│         │                                                            │
│         ▼                                                            │
│   policies.py ── resolves which TOOLS this role may use with the    │
│                   selected agent, from the same LLM-RBAC decision    │
│                   already made above (not a second auth system)      │
│         │                                                            │
│         ▼                                                            │
│   planner.py::run_agent() ── the LLM tool-calling loop (bounded by  │
│                   agent_max_tool_iterations). A deterministic floor  │
│                   search always runs once first, on the user's       │
│                   literal message, as a reliability baseline.        │
│         │                                                            │
│         ▼                                                            │
│   one of: retrieval_agent.py (RAG search) · sql_agent.py (read-only,│
│           table-allowlisted analytics) · report_agent.py (CSV/XLSX/ │
│           DOCX/PDF) · project_agent.py (project data)                │
└─────────────────────────────────────────┬───────────────────────────┘
                                            ▼
Retrieval Guardrails ────── retrieval_permissions.py (per-document + department filtering)
  ▼
Context Validation ──────── reranking/pipeline.py (rerank, parent-context expansion)
  ▼
Claude / LLM ─────────────── gateway/claude_gateway.py::generate
  ▼
┌─────────────────────────────────────────────────────────────────────┐
│ OUTPUT GUARDRAILS — 8 checks, sequential, pipeline.py::              │
│ run_output_guardrails                                                │
│ system-prompt-leak → toxicity → PII (Presidio) → PII (GLiNER) →     │
│ PII redaction → citation validation → groundedness → final policy   │
└─────────────────────────────────────────┬───────────────────────────┘
                                            ▼
Final Decision ───────────── policy_engine.py::decide
  ▼
Response ─── reply (possibly redacted) + sources/citations/confidence
  ▼
Trace + Audit ───────────── message.trace (JSONB) + services/audit/logger.py::log
```

## 3. The agent system

Two-step selection, not one monolithic agent:

1. **Routing** (`services/agents/router.py`) — a cheap, fast-tier model classifies the request's
   *intent* into an `AgentName` (general RAG, SQL analytics, report generation, project data). This
   is a business classification only — never a security decision. Below the confidence floor
   (`agent_router_confidence_threshold`, 0.5), `chat.py` asks the user to clarify rather than guess.
2. **Execution** (`services/agents/planner.py::run_agent()`) — the actual LLM tool-calling loop,
   bounded by `agent_max_tool_iterations` (6) so it can't loop forever.

RBAC gates the *tools*, not just the routing: `services/agents/policies.py` resolves which tools the
selected agent may actually call for this specific role, derived from the same LLM-RBAC decision
already made for the request — not a second, independent authorization system. Picking "SQL agent"
doesn't help a role with no SQL access; the tool itself is filtered out before the loop starts.

**The four specialist agents:**

- `retrieval_agent.py` — `search_documents()`, the RAG tool. Wraps hybrid search + reranking, then
  applies `retrieval_permissions.py`'s department/document filtering *before* results reach the LLM.
- `sql_agent.py` — read-only analytics queries, restricted to an explicit table allowlist
  (`deterministic/sql_guard.py`) that deliberately excludes `users`.
- `report_agent.py` — generates CSV/XLSX/DOCX/PDF report artifacts.
- `project_agent.py` — project-status data, scoped to "my projects" vs. "all projects" by role.

**Deterministic floor search**: before the agent's own (LLM-sampled) tool calls, a floor search
always runs once using the user's literal message verbatim (`deterministic_floor_search_enabled`, on
by default). Every part of the agent's own retrieval decision — whether to search, how to phrase the
query, when to give up — is itself LLM-sampled and therefore variable; the floor search guarantees a
baseline lookup regardless of how that sampling draw went. The agent can still search further on top
of it if it judges that necessary.

## 4. The guardrail system

**Input** (14 checks, all run against the same message; scope-related blocks are deferred so a later,
more specific finding always wins): length, secrets, prompt injection (regex), destructive intent,
custom word policy, custom regex policy, scope (keyword), semantic risk, advanced injection
(DeBERTa classifier), semantic scope, toxicity, PII (Presidio), PII (GLiNER), PII redaction.

**Output** (8 checks, sequential, first block wins): system-prompt leak, toxicity, PII (Presidio),
PII (GLiNER), PII redaction, citation validation, groundedness, final policy decision.

**Action vocabulary** (`GUARDRAIL_POLICY_ACTIONS`): `ALLOW`, `FLAG`, `MASK`, `REDACT`, `BLOCK`,
`ESCALATE`. PII is the one guardrail where all six are fully wired, per entity and per direction;
most others still resolve to a hardcoded `ALLOW`/`BLOCK`. See `REQUEST_PIPELINE.md` for the exact
ordered tables with function/module references, and `GUARDRAILS_ARCHITECTURE.md` for why each check
exists.

## 5. RBAC summary

Two layers, both driven by one `backend/config/llm_rbac.yaml`:

1. A coarse `Permission` enum (`core/permissions.py`) — gates frontend nav/pages and backend
   endpoints via `require_permission()`.
2. Fine-grained named business actions — gates what `/chat` and `/search` can actually retrieve/do,
   resolved via `services/llm_rbac/engine.py::authorize_llm_request()`.

5 roles (Employee / HR / Project Manager / CEO / Admin), each with its own department scope,
model-tier ceiling, and permission set. CEO does **not** inherit every Admin capability (no
`MANAGE_USERS` / `MANAGE_ROLES` / `SYSTEM_SETTINGS`). Full table: `ROLE_PERMISSION_MATRIX.md`.

## 6. Frontend

React + Vite + TypeScript + Tailwind v4 (CSS-first tokens, no config file — theme lives in
`index.css`'s `@theme` block). 16 pages: Landing (`/welcome`), Login, Dashboard, Chat, Chat History,
Documents, Search, Settings, Evaluation, Metrics, Users, Roles, Audit Logs, Admin, Guardrail
Policies, Traces. Auth state lives in one `AuthContext`; theme is a plain hook, not a context.
Dark mode via `[data-theme]` + OS `prefers-color-scheme` fallback — every component repaints in both
themes with zero per-component overrides.
