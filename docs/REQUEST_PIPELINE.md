# Request Pipeline

This document assembles the full, end-to-end path a `POST /chat` request takes — identity through
retrieval through generation through the persisted trace — into one place. It cross-references, but
doesn't duplicate, the docs that cover each piece in depth: `LLM_RBAC_ARCHITECTURE.md` (authorization),
`GUARDRAILS_ARCHITECTURE.md` (the guardrail rail pipeline itself), `CLAUDE_GATEWAY_ARCHITECTURE.md`
(model access), and `AUDIT_LOGGING.md` (the audit trail). Read this doc first for the shape of the
whole thing; follow the cross-references for the "why" behind any one stage.

Twelve macro-stages, all invoked from `backend/app/routers/chat.py`'s `chat()` handler:

```
User
  │
  ▼
Authentication            services/auth/dependencies.py::get_current_user
  │                        JWT verified, user record loaded, active-account check.
  ▼
RBAC / Authorization      services/llm_rbac/engine.py::authorize_llm_request
  │                        Resolves the caller's role config — knowledge departments,
  │                        model tiers, allowed tools, quotas.
  ▼
Input Guardrails          services/guardrails/pipeline.py::run_input_guardrails
  │                        14 checks, see below. Wrapped by orchestrator_graph.py's
  │                        input_security → risk_analysis → policy_check nodes.
  ▼
Agent / RAG               services/agents/router.py + services/agents/retrieval_agent.py
  │                        Router selects a specialist agent; retrieval runs hybrid
  │                        (dense + BM25) search against Qdrant.
  ▼
Retrieval Guardrails      services/guardrails/retrieval_permissions.py
  │                        filter_by_permission / filter_by_category — per-document
  │                        authorization and department filtering on retrieved chunks.
  ▼
Context Validation        services/reranking/pipeline.py
  │                        Reranking, optional parent-context expansion, final
  │                        citation-ready context assembly.
  ▼
Claude / LLM               gateway/claude_gateway.py::generate
  │                        Generation via the Claude Gateway, at the model tier the
  │                        caller's role is permitted to reach.
  ▼
Output Guardrails          services/guardrails/pipeline.py::run_output_guardrails
  │                        8 checks, see below. Wrapped by orchestrator_graph.py's
  │                        output_security → citation → grounding → final_policy nodes.
  ▼
Final Decision              services/guardrails/policy_engine.py::decide
  │                        Resolves one outcome from every stage's findings.
  ▼
Response
  │                        Reply (possibly redacted), with sources/citations/confidence,
  │                        returned to the client.
  ▼
Trace + Audit              routers/chat.py (persists message + trace) +
                           services/audit/logger.py::log
                           Every check's outcome recorded on the message's `trace`
                           JSONB column; a separate audit event logged.
```

## Input guardrails — exact order

All 14 run against the same message (`services/guardrails/pipeline.py::run_input_guardrails`).
Scope-related blocks are **deferred**: every remaining check still runs, and a later, more specific
block always wins over a generic scope refusal.

| # | Check | Function | Module |
|---|---|---|---|
| 1 | Length Check | `check_length` | `length.py` |
| 2 | Secret / Credential Detection | `check_secrets` | `secrets.py` |
| 3 | Prompt Injection (regex) | `check_prompt_injection` | `injection.py` |
| 4 | Destructive Intent | `check_destructive_intent` | `destructive.py` |
| 5 | Custom Word Policy | `check_custom_word` | `custom_word_check.py` |
| 6 | Custom Regex Policy | `check_custom_regex` | `custom_regex_check.py` |
| 7 | Scope Check (keyword) | `check_scope` | `scope.py` |
| 8 | Semantic Risk | `check_semantic_risk` | `semantic_check.py` |
| 9 | Advanced Injection (DeBERTa classifier) | `check_with_deberta` | `deberta_injection_check.py` |
| 10 | Semantic Scope | `check_scope_semantic` | `semantic_check.py` |
| 11 | Toxicity | `check_toxicity` | `toxicity_check.py` |
| 12 | PII — Presidio | `check_with_presidio` | `presidio_check.py` |
| 13 | PII — GLiNER | `check_with_gliner` | `gliner_check.py` |
| 14 | PII Redaction | `redact_pii` | `pii.py` |

Followed by `risk_analysis` (aggregates findings into one risk level) and `policy_check`
(`policy_engine.decide`, audits blocks, records escalation via `escalation.py::record_block`) —
both LangGraph nodes in `orchestrator_graph.py`, not part of `pipeline.py` itself.

## Output guardrails — exact order

Sequential, first block wins (no deferral on output).

| # | Check | Function | Module |
|---|---|---|---|
| 1 | System-Prompt Leak Check | `check_system_prompt_leak` | `output.py` |
| 2 | Toxicity (output) | `check_toxicity` | `toxicity_check.py` |
| 3 | PII — Presidio (output) | `check_with_presidio` | `presidio_check.py` |
| 4 | PII — GLiNER (output) | `check_with_gliner` | `gliner_check.py` |
| 5 | PII Redaction (output) | `redact_pii` | `pii.py` |
| 6 | Citation Validation | `check_citations` | `citation_rail.py` |
| 7 | Groundedness Check | `check_groundedness` | `groundedness_check.py` |
| 8 | Final Policy Decision | `policy_engine.decide` | `policy_engine.py` |

## Action vocabulary

`GUARDRAIL_POLICY_ACTIONS` (`backend/app/models/guardrail_policy.py`): `ALLOW`, `FLAG`, `MASK`,
`REDACT`, `BLOCK`, `ESCALATE`.

Enforcement maturity varies by guardrail today:

- **PII** (`pii_policy.py::resolve_pii_policy`) is the one guardrail with all six actions fully wired,
  independently per entity type and per direction (input vs. output) — a real, admin-editable control
  via the Guardrail Policy Center.
- **Semantic risk / DeBERTa injection / message length** have a DB-overridable threshold and
  enabled/disabled flag, but the action taken on a trip is still a hardcoded `"block"` literal — no
  action-selection knob yet.
- **Prompt injection (regex) / toxicity / groundedness** currently have no per-instance action config
  at all — enable/disable and (for toxicity) a YAML threshold, nothing more.
- **Custom word/regex policies** (Guardrail Policy Center) only enforce the `BLOCK` action at
  runtime — `WARN`/`ALLOW`/`ESCALATE`/`REDACT` are stored and versioned but currently inert.
- `policy_engine.decide()` itself only ever constructs `"BLOCK"` or `"ALLOW"` today — `REDACT`,
  `REGENERATE`, and `ESCALATE` are declared in its type but not yet returned by any branch.

## Cross-references

- `LLM_RBAC_ARCHITECTURE.md` — the two-layer permission model (coarse `Permission` enum +
  fine-grained named actions) behind the RBAC/Authorization stage.
- `GUARDRAILS_ARCHITECTURE.md` — why each guardrail exists and the rail-pipeline design history.
- `CLAUDE_GATEWAY_ARCHITECTURE.md` — model tier resolution and the Claude Gateway itself.
- `AUDIT_LOGGING.md` — the audit event schema populated at the Trace + Audit stage.
- `ROLE_PERMISSION_MATRIX.md` — the concrete per-role permission table.

A visual, chart-rendered version of this same pipeline (with rendered flowcharts for the two
guardrail stages) exists as a published Claude Artifact, generated from this document's content —
ask for a fresh copy if you need one to share.
