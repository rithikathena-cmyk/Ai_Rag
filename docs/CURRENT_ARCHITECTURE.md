# Current Architecture — Inspection & Reuse Assessment

Phase 1 deliverable for the agentic security platform. This is an **inspection
report**, not a re-description of the system: `SYSTEM_ARCHITECTURE.md` already
documents components and flow, and `REQUEST_PIPELINE.md` the ordered checks.
What follows is what exists, what is reusable as-is, what must change, and the
one architectural property that causes most of the open security findings.

---

## 1. Inventory

| Layer | Present | Notes |
| --- | --- | --- |
| Backend | FastAPI, 17 routers | `chat`, `documents`, `traces`, `audit`, `approvals`, `guardrail_policies`, `admin`, `evaluation`, … |
| Auth | JWT HS256, access + refresh | `services/auth/dependencies.py::get_current_user` |
| RBAC | Two layers, one config | coarse `Permission` enum + fine-grained named actions, both from `llm_rbac.yaml` |
| Orchestration | LangGraph `StateGraph` | `guardrails/orchestrator_graph.py` |
| Agents | router + planner + 4 specialists | `agents/{router,policies,planner,retrieval_agent,sql_agent,report_agent,project_agent}.py` |
| Input guardrails | 14 checks | `guardrails/pipeline.py::run_input_guardrails` |
| Output guardrails | 8 checks | `guardrails/pipeline.py::run_output_guardrails` |
| PII | regex + Presidio + GLiNER | `pii.py` (8 labels), `presidio_check.py`, `gliner_check.py` |
| Injection | regex + DeBERTa classifier | `injection.py`, `deberta_injection_check.py` |
| Scope | keyword + embedding + re-check | `scope.py`, `scope_semantic_check.py` |
| Other ML | toxic-bert, NLI groundedness | `toxicity_check.py`, `groundedness_check.py` |
| Policy engine | exists | `guardrails/policy_engine.py`, `guardrail_policy/pii_policy.py` |
| Policy Center | full CRUD + versions + approvals | `routers/guardrail_policies.py`, `GuardrailPolicyPage.tsx` |
| Traces | per-message JSONB | `message.trace`, `routers/traces.py`, `TracesPage.tsx` |
| Audit | dedicated table + logger | `models/audit_event.py`, `services/audit/logger.py` |
| Escalation | block-count lockout | `guardrails/escalation.py` — 5 blocks / 600s / 300s lockout |
| Vector store | Qdrant, dense + sparse hybrid | `retrieval/` |
| Frontend | React 18 + Vite + TS + Tailwind v4 | 16 pages |
| Tests | 1268 backend + new security suite | plus `backend/tests/security/` |

---

## 2. Reuse assessment

### Reuse as-is — no change needed

- **Auth & RBAC.** `get_current_user`, `authorize_llm_request`,
  `_check_permission` (default-deny, verified). The target architecture's
  AUTH → RBAC stages are already correct and already run before anything else.
- **Policy Center.** Versioning, rollback, approval workflow, per-entity
  actions, dry-run mode, `redaction_format` — §21's Policy Copilot needs a
  *proposal* front end onto this, not a new policy store.
- **Approvals.** `models/approval*`, `routers/approvals.py` already implement
  request → decide with role scoping. §11's tool approval and §21's policy
  approval both map onto it.
- **Trace + audit.** `message.trace` JSONB plus `audit_events`. §16's agent
  trace is an extension of this shape, not a replacement.
- **Escalation.** Already the "repeat offender" control.
- **Retrieval permissions.** `apply_permission_policy` is a pure, tested
  function and is exactly §9's metadata authorization step.
- **Deterministic detectors.** Every regex/validator in `pii.py`,
  `secrets.py`, `injection.py`, `destructive.py` becomes a *Deterministic
  Security Service* in the target model, unchanged.

### Reuse with extension

- **LangGraph orchestrator.** Already the substrate for §2/§3, but currently
  **linear**: `START → input_security → risk_analysis → policy_check → END`,
  all `add_edge`, no `add_conditional_edges`, no loops, no LLM in the decision
  path. It is LangGraph in shape only. Adding the Security Supervisor means
  adding *decisions*, not rebuilding.
- **Agent router/policies.** `router.py` already does intent classification
  and `policies.py` already filters tools by role — the Orchestrator (§2)
  formalises what these do into an `AgentExecutionPlan`.
- **`GuardrailStep`.** Close to §4/§5/§6's `PIIFinding` / `InjectionFinding` /
  `ScopeFinding`, but lacks `confidence`, `detection_method`, `severity`,
  `value_span`, `recommended_action`. Extending this record is the smallest
  change that unlocks the findings model.
- **Guardrail Policy actions.** `ALLOW/FLAG/MASK/REDACT/BLOCK/ESCALATE`
  already exist as a real constant. Only PII wires all six; ESCALATE is
  currently treated as BLOCK.

### Must change

- **`run_input_guardrails` control flow.** See §3 below. This is the single
  most consequential change.
- **`_resolve_match` disabled-row semantics.** Disabled currently means
  ALLOW; §7 requires it to mean safe default (SF-01).
- **Scope decisioning.** Whole-message similarity must become clause-level
  with a mixed-scope policy (SF-03).
- **`system_prompt_leak_check`.** No natural-language leak patterns (SF-04).

### Do not build — already exists

Building these fresh would duplicate working code: a policy store, an approval
workflow, an audit logger, a trace viewer, an RBAC engine, a document
permission filter, or any of the deterministic detectors.

---

## 3. The architectural property behind most findings

`run_input_guardrails()` is a **short-circuit** pipeline. The first check to
return `block` returns immediately and supplies the user-facing reason:

```python
for check in (...):
    step = check(current)
    if step.action == "block":
        if step.name in _DEFERRABLE_SCOPE_STEP_NAMES:
            deferred_scope_step = step      # the one hand-rolled exception
            continue
        return _blocked_result(current, step, steps)
```

The PII rails sit **last** in that order. So whichever unrelated rail fires
first claims the attribution — measured: 28 of 29 PII scenarios were credited
to the wrong check, including credit cards attributed to the *prompt-injection
classifier*.

`deferred_scope_step` is a partial fix for exactly this collision, but only
for scope. Every other ordering collision is unhandled, and each new one would
need its own bespoke deferral.

**This is what §8 of the target architecture fixes.** Collecting all findings
and letting the policy engine choose both the action and the primary reason
generalises `deferred_scope_step` into a principle. It is not a new feature
bolted on — it is the correct form of a mechanism the codebase already
discovered it needed.

---

## 4. Security properties already correct

Worth stating so the evolution does not regress them:

1. **Authorization precedes retrieval.** An agent cannot search a corpus the
   caller has no claim to.
2. **Routing is not authorization.** `policies.py` filters tools from the
   RBAC decision already made; being routed to the SQL agent grants nothing.
3. **Default-deny.** `_check_permission` refuses any action absent from the
   allow list (verified behaviourally).
4. **Trace/audit integrity.** All 68 evaluation cases passed trace and audit
   checks — the pipeline reports faithfully what it did.
5. **Output PII redaction works** for SSN/email/phone.
6. **Deterministic floor beneath classifiers.** Regex injection detection
   remains authoritative regardless of DeBERTa availability, with documented
   fail-open/fail-closed posture per check.
7. **Classifier internals are not leaked.** Raw guardrail detail is gated on
   `VIEW_AUDIT_LOGS`, server-side.

---

## 5. Mapping: existing component → target role

| Target (§) | Existing component | Gap |
| --- | --- | --- |
| Auth + RBAC | `dependencies.py`, `engine.py` | none |
| Agent Orchestrator (§2) | `router.py` + `planner.py` | no explicit `AgentExecutionPlan`; no server-side plan validation |
| Security Supervisor (§3) | `orchestrator_graph.py` | linear; no finding aggregation |
| PII Agent (§4) | `pii.py`, `presidio_check.py`, `gliner_check.py` | no Luhn/checksums; 6 entities uncovered; no `PIIFinding` |
| Injection Agent (§5) | `injection.py`, `deberta_injection_check.py` | no decode-then-rescan; no `InjectionFinding` |
| Scope Agent (§6) | `scope*.py` | whole-message only; no clause model |
| Policy Engine (§7) | `policy_engine.py`, `pii_policy.py` | disabled→ALLOW; decides from one step, not a finding set |
| RAG Agent (§9) | `retrieval_agent.py`, `retrieval_permissions.py` | no document injection/classification scan |
| Output Agent (§10) | `run_output_guardrails` | system-prompt leak gap |
| Tool Security (§11) | `policies.py` | no schema validation, risk tiers, or approval gate |
| Red Team (§12) | `tests/security/` | static case tables, not generative |
| Evaluation (§15) | `tests/security/framework.py` | complete |
| Regression (§13) | `tests/security/regression/` | no persisted cross-version baseline |
| Investigation (§14) | `routers/traces.py`, `audit` | no query agent |
| Agent trace (§16) | `message.trace` | no per-agent execution record |

---

## 6. Recommended sequencing

Ordered by security value per unit of change, not by the numbering in the
target spec:

1. **SF-01** — delete the stale policy row, then fix disabled-row semantics.
   Smallest change, closes an active cleartext exposure.
2. **§8 finding aggregation** — fixes SF-02 wholesale and is the structural
   prerequisite for every specialised agent.
3. **§6 clause-level scope** — fixes SF-03 and SF-05 together.
4. **§4 deterministic PII hardening** — Luhn/Verhoeff, and the 6 uncovered
   entities.
5. **§10 output leak patterns** — fixes SF-04.
6. Then the agent layer (§2, §3, §5, §9, §11) on top of a correct core.

Building agents before step 2 would mean building them onto a pipeline whose
attribution is known-wrong — the agents would inherit the defect and make it
harder to see.
