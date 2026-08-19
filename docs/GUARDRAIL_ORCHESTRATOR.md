# Guardrail Orchestrator Agent

Documents the runtime orchestrator as implemented and verified — `backend/app/services/guardrails/orchestrator_graph.py`
and its companion modules. This is the **as-built** record of what runs in production (`routers/chat.py` calls it on
every `/chat` request). It is narrower in scope than `docs/AGENTIC_GUARDRAILS_ARCHITECTURE.md` (a Phase-1 target
design covering a larger multi-agent redesign that was not implemented this pass) — this document describes only what
actually exists and runs today.

---

## 1. The governing split

```
LLM / Agent              →  ORCHESTRATION and CLASSIFICATION
Existing guardrail engines →  DETECTION
Policy engine              →  ENFORCEMENT
Decision engine (policy_engine.decide) → FINAL AUTHORITY
```

The orchestrator is a LangGraph `StateGraph` that makes explicit, as named nodes with a typed shared state, the same
sequence `routers/chat.py` has always run as plain function calls. Every node body calls an **existing, unmodified**
function — `run_input_guardrails`, `run_output_guardrails`, `check_citations`, `check_groundedness`,
`classify_risk`, `policy_engine.decide`. The graph contributes orchestration and audit structure, not new detection
logic. No node performs its own detection; no LLM call anywhere in this graph can allow, block, or modify a
guardrail outcome.

---

## 2. LangGraph nodes and execution order

Two separately-compiled graphs, not one continuous graph spanning the whole request. The main LLM call
(`run_agent()`/`run_retrieval_fallback()`) and conversation persistence (`add_message()`) sit **between** them in
`chat.py`, exactly where they always have — `chat.py` must persist the (possibly redacted) user message to the
database between "did input security allow this" and "call the LLM," an existing ordering this design does not
change.

**Input stage** (`run_input_stage()`):

```
START → input_security → risk_analysis → policy_check → request_understanding → END
```

| Node | Wraps | Purpose |
|---|---|---|
| `input_security` | `run_input_guardrails()` | Runs the full mandatory guardrail chain (§3) |
| `risk_analysis` | `classify_risk()` | Aggregates the already-computed check outcomes into a risk level/type — a recommendation, not a gate |
| `policy_check` | `policy_engine.decide()` | Deterministic ALLOW/BLOCK decision; audits a BLOCK |
| `request_understanding` | `classify_request()` (LLM) | Trace-only classification label (§4) |

**Output stage** (`run_output_stage()`):

```
START → output_security → citation → grounding → final_policy → END
```

| Node | Wraps | Purpose |
|---|---|---|
| `output_security` | `run_output_guardrails()` | Full mandatory output guardrail chain (§3) |
| `citation` | `check_citations()` | Skipped (marked pass) if output was already blocked upstream |
| `grounding` | `check_groundedness()` | Skipped if already blocked; its only `block` path is a detector-failure fail-closed |
| `final_policy` | `policy_engine.decide()` | Deterministic decision over output/citation/grounding findings |

Both graphs are compiled once at import time (`_INPUT_GRAPH`/`_OUTPUT_GRAPH`) — same "build once, invoke many"
convention the ML-model-backed checks already use.

**Not graph nodes, and why:** Authentication and RBAC authorization (`get_current_user()`, `authorize_llm_request()`)
already run, deterministically, before `chat.py` ever calls into this graph — duplicating them as nodes would mean
either re-deriving a decision (risking disagreement with the real one) or rubber-stamping an already-made one.
Retrieval authorization, document-injection scanning, and the context firewall already happen inside `run_agent()`
today; splitting them out is a larger restructuring deferred to a future pass, not part of this graph.

---

## 3. Mandatory guardrails

Every check below runs unconditionally on every request, in this fixed order. Nothing in the orchestrator — including
the LLM-backed classification node — can skip, reorder, weaken, or disable any of them.

**Input** (`run_input_guardrails()`, `pipeline.py`):

```
length → secrets → prompt_injection → destructive_intent → custom_word → custom_regex → scope (keyword)
       → semantic_risk → deberta_injection → scope_semantic → toxicity → presidio → gliner → pii_redact
```

**Output** (`run_output_guardrails()`, `pipeline.py`):

```
prompt_injection → system_prompt_leak → toxicity → presidio → gliner → pii_redact → citation → grounding
```

First block wins, with one documented exception: a block from `scope_semantic_check` (or its `scope_unclear_*`
variants) is held as a *deferred* candidate rather than returned immediately, so a later, more specific check in the
same pass (toxicity, PII, a redacted-text scope re-check) can still supersede it. This exists so the final trace
always credits the check that actually determined the outcome, never a generic scope refusal that happened to run
first. See `pipeline.py`'s own module docstring for the full mechanism and its regression tests.

---

## 4. Request classification (`request_classifier.py`)

The one LLM-backed node in either graph. A single Claude call (`ModelTier.FAST`) labels the message into one of five
closed categories (`GENERAL_QUERY`, `PII_SENSITIVE`, `INJECTION_SUSPECTED`, `ACCESS_OR_POLICY_QUESTION`,
`AMBIGUOUS`) with a confidence score and a static, hand-authored list of which real checks a human reading the trace
would expect mattered most.

This is **trace annotation only**:

- It never decides whether the request is safe.
- `policy_engine.decide()` never reads it — the deterministic aggregation of the real checks' findings is the sole
  authority.
- Every check in §3 runs unconditionally regardless of what this returns.
- A wrong or adversarially-manipulated classification has no security consequence — the message still passes through
  every mandatory check unchanged.
- Any failure (no API key, timeout, refusal, malformed output, an unrecognized category) yields `None` and changes
  nothing else about the graph's behavior.

The output is sent through a closed Pydantic schema (`extra="forbid"`) and the classified text is wrapped in
explicit DATA delimiters before being sent to the model — the same anti-injection framing used throughout this
codebase's other LLM calls.

### Why `request_understanding` runs *after* `policy_check`, not in parallel

An earlier revision ran `request_understanding` in parallel with `input_security` — both edges off `START`, fanning
into `policy_check` — to keep the classifier's latency off the critical path, since nothing downstream reads its
result. That design was reverted after focused testing surfaced a real regression: LangGraph dispatches concurrent
branches onto worker threads, and running `policy_check`'s audit-log write off the main thread caused a raw,
uncaught `IntegrityError` to escape `audit_logger.log()`'s own broad exception handler. This reproduced
deterministically — including in complete isolation, ruling out test-order flakiness — in the pre-existing,
unmodified test `test_input_stage_blocks_and_records_the_block`.

A node whose entire purpose is a display-only label was not worth introducing real concurrency risk into a
security-critical audit path for. The graph now runs strictly sequentially — `request_understanding` executes last,
after the policy decision and its audit write are already complete — accepting a small amount of added latency
instead. Verified after the fix: the previously-failing test passes deterministically across repeated isolated
reruns, the full focused suite (18 tests across `test_orchestrator_graph.py`/`test_request_classifier.py`) passes,
and a full `tests/guardrails/` run (548 tests) is clean.

---

## 5. Deterministic policy decision (`policy_engine.py`)

`policy_engine.decide()` is the single place that turns findings from the guardrail stages into one of
`ALLOW / BLOCK / REDACT / REGENERATE / ESCALATE`. It makes no detection decisions of its own — it only composes
decisions the check modules already made (`GuardrailResult.blocked`, a `GuardrailStep.action`) into one explicit,
named outcome. Precedence is deterministic and order-independent: a hard block from any deterministic check always
wins, regardless of which finding object it arrived in. Called twice per turn — once after input security + risk
analysis, once after output security + citation + grounding — mirroring what were previously two separate implicit
decision points inline in `chat.py`.

`ESCALATE` is not decided here: a user who has accumulated enough recent guardrail blocks is turned away by
`escalation.py`'s pre-flight lockout gate in `chat.py`, *before* this graph is ever invoked — a request that reaches
the orchestrator is structurally guaranteed not to be currently locked out.

---

## 6. PII / RBAC enforcement

- **RBAC/authorization** — `authorize_llm_request()` (`services/llm_rbac/engine.py`) is the single upstream gate
  every request passes through before the orchestrator runs. A denial here is itself an auditable event and never
  reaches guardrail checks at all.
- **PII detection** is layered, not single-source: `presidio_check` (structural recognizers), `gliner_check`
  (curated natural-language label set), and `pii.py`'s regex layer (`redact_pii`, checksum-validated identifiers)
  each run at a fixed position in §3's order. Input-side PII is blocked outright by default
  (`guardrail_pii_block_input`); output-side PII is redacted-and-returned by default, since the model already
  generated it and redaction is what remains to do.
- Raw PII values never reach a trace, audit log, or frontend surface — every step's `.detail` string carries labels
  and spans, never matched values. This is enforced throughout `pipeline.py`/`scope_semantic_check.py` and is a
  standing invariant, not something this orchestrator pass introduced.
- A separate, dedicated employee-PII approval workflow (`pii_intent.py`, checked against the raw message before this
  graph runs at all) exists for a narrower "look up/update an employee record" capability, gated on
  `MANAGE_EMPLOYEE_PII` — out of scope for this document; see its own module docstring.

---

## 7. Audit / trace flow

- Every `GuardrailStep` produced by a check is appended to the request's trace (`GuardrailResult.steps`) and
  recorded via `record_guardrail_event()` (`services/monitoring/metrics.py`).
- A `BLOCK` decision from `policy_check`/`final_policy` triggers `audit_logger.log()` with a reason code derived from
  the blocking check's name (`event_types.reason_code_for_check`) — never the check's free-text detail, which may
  itself be a display string, not a machine key.
- `audit_logger.log()` is best-effort: it never raises, so a logging failure cannot affect the block decision it is
  describing (this is exactly the property whose narrow, threading-triggered failure mode is documented in §4's
  "why after `policy_check`" explanation).
- Post-hoc, on-demand explanation of an already-finalized decision is available via `decision_explainer.py` — a
  second, separate LLM call, invoked only when a human asks "why was this blocked" (via the Policy Copilot's trace
  lookup), never from the live request path. It receives only labels (check name, action, short reason string) and
  produces prose used solely as display text; nothing parses its output back into a decision.

---

## 8. Agent responsibilities vs. deterministic security responsibilities

| Component | Authority | Can it decide ALLOW/BLOCK? |
|---|---|---|
| `request_classifier.classify_request()` (LLM) | Advisory — trace label only | No |
| `decision_explainer.explain_decision()` (LLM) | Advisory — post-hoc prose only | No |
| `pipeline.run_input_guardrails()` / `run_output_guardrails()` | Deterministic detection | Yes — first-block-wins with the documented deferred-scope exception |
| `risk_analysis.classify_risk()` | Deterministic aggregation | No — a recommendation carried into the audit trail only |
| `policy_engine.decide()` | Deterministic enforcement | **Yes — sole final authority** |
| `orchestrator_graph.py` nodes | Orchestration/sequencing | No — every node calls straight into one of the above |

No LLM call anywhere in this graph is ever the final authority on a security outcome. The two LLM-backed modules
(`request_classifier.py`, `decision_explainer.py`) are both structurally incapable of influencing a decision: one
runs after the decision that matters is already made and audited; the other runs entirely outside the live request
path.

---

## 9. Known pre-existing failures — `tests/security/` (not fixed, not in scope)

Full suite: **263 passed, 4 failed, 5 xfailed, 2 xpassed** (`tests/security/`). These 4 failures predate this
orchestrator pass, are unrelated to it (they sit entirely inside `run_input_guardrails()`'s PII checks, which the
orchestrator calls unmodified), and were explicitly left untouched per instruction. In every case the outcome is
still safe — no raw PII leaks in any of them — the failures are attribution/labeling mismatches or a false-positive
redaction, not a security bypass.

| Test | Status | Expected | Actual | Root cause |
|---|---|---|---|---|
| `PII-SSN-01` | WRONG_ATTRIBUTION | redact via `pii_redact`, entity `SSN` | redact via `gliner_check`, entity `GOVERNMENT_ID` | GLiNER's `GOVERNMENT_ID` label fires on the SSN before/instead of `pii.py`'s dedicated SSN regex recognizer. The value is still correctly redacted (`leakage=clean`) — only the credited check/entity name is wrong. |
| `PII-SSN-04` | FAIL | redact via `pii_redact`, entity `SSN` | block via `scope_unclear_pii`, entity `GOVERNMENT_ID` | Same GLiNER `GOVERNMENT_ID` mislabel, compounded here by the input being a bare, context-free SSN with no surrounding request text — the scope-unclear path fires instead of a clean redact-and-continue. |
| `PII-PWD-01` | WRONG_ATTRIBUTION | block via `secret_detected_check`, entity `PASSWORD` | block via `deberta_injection_check`, entity `None` | `secrets.py` has no deterministic `PASSWORD` pattern (its `CREDENTIAL_PATTERNS` cover fixed-shape credentials — API keys, AWS keys, JWTs, GitHub/Slack/Google tokens, private keys — a stated plaintext password has no comparable fixed shape to regex-match). The message is still correctly blocked, just by DeBERTa's injection classifier rather than a password recognizer. Structural gap, not a regression. |
| `PII-FP-01` | UNEXPECTED_BLOCK | pass (employee/incident ID is not PII) | redact via `gliner_check`, entity `GOVERNMENT_ID` | The same over-broad GLiNER `GOVERNMENT_ID` label also fires on employee/incident-ID-shaped strings (e.g. `STF-MFG-41220`), redacting text that isn't PII at all. |

Three of the four (`PII-SSN-01`, `PII-SSN-04`, `PII-FP-01`) share one root cause: GLiNER's `GOVERNMENT_ID` label is
configured broadly enough to compete with, and sometimes misfire ahead of, both the dedicated deterministic SSN
recognizer and legitimate employee/incident-ID strings — the same `gliner_validators.py`-vs-`entities.py` conflict
identified and reported (not fixed, per instruction) in a prior investigation this session. `PII-PWD-01` is a
separate, structural gap: no deterministic credential pattern exists for a stated plaintext password. Both are
documented here as known issues for a future, separately-scoped fix — no code was changed to address them as part of
this pass.
