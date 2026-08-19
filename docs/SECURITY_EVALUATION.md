# Guardrail Security Evaluation Suite

`backend/tests/security/` — a deterministic, repeatable evaluation of the
guardrail pipeline. Findings it produced are in `SECURITY_FINDINGS.md`.

## The governing rule

> **A test is not successful merely because the request was blocked.**

A message stopped by the wrong rail is a failure. It looks green on a
pass/fail chart while the rail that *should* have caught it is unverified —
and the protection disappears the moment that unrelated rail is retuned. Six
dimensions are evaluated independently:

| # | Dimension | Question |
| --- | --- | --- |
| 1 | Detection | did anything fire? |
| 2 | Attribution | did the **right** rail fire? |
| 3 | Action | ALLOW / FLAG / MASK / REDACT / BLOCK / ESCALATE as policy requires? |
| 4 | Leakage | did the synthetic value survive anywhere readable? |
| 5 | Trace | can the decision be explained afterwards? |
| 6 | Audit | is every step individually attributable? |

## Design constraints

**No duplicated security logic.** Every verdict comes from the same functions
the application calls at runtime — `run_input_guardrails`,
`run_output_guardrails`, `resolve_pii_policy`, `apply_permission_policy`,
`resolve_agent_tools`, `_check_permission`. The framework only *reads* what
they produce. It must never be "fixed" by teaching it to detect PII itself.

**Synthetic data only.** Published Luhn-valid test PANs, the reserved
`123-45-6789` SSN shape, the `555-01xx` fictional phone range, and
`example.com` (RFC 2606).

**Never weaken a guardrail to make a test pass.** When a test fails the
implementation is investigated and the root cause recorded. Two tests in this
suite were themselves wrong and were corrected — see *Harness correctness*
below — but no production behaviour was softened.

## Layout

```
backend/tests/security/
    framework.py          result model, runner, the six checks, reporting
    run_evaluation.py     consolidated scorecard
    pii/                  17 entity types x format/context variations
    injection/            direct, jailbreak, extraction, evasion, false alarms
    scope/                in/out/mixed/compound/document-reference
    rag/                  document authorization + hostile document content
    rbac/                 role x permission x corpus x model tier
    tools/                tool authorization, agent gating, handoff
    output/               leakage, output PII, groundedness, citation
    policy/               resolution + mutation safety
    leakage/              cross-surface leakage + multi-turn
    regression/           locks for every reproduced defect
```

## Result model

Each case yields `test_id, category, scenario, input, expected/actual
detection, expected/actual primary guardrail, expected/actual action,
expected/actual entity, leakage_check, trace_check, audit_check, severity,
status` plus `secondary_findings`.

Statuses: `PASS`, `FAIL`, `REGRESSION`, `UNEXPECTED_BLOCK`, `UNEXPECTED_ALLOW`,
`WRONG_ATTRIBUTION`, `LEAKAGE`, `TRACE_FAILURE`, `AUDIT_FAILURE`.

Classification is **ordered by security relevance**, not by first mismatch: a
leak outranks everything, because a request can be correctly blocked by the
correct rail and still be critical if the value survived into the trace.

## Running it

```bash
# consolidated scorecard
cd backend && python -m tests.security.run_evaluation

# as CI tests
pytest backend/tests/security -q

# one category
pytest backend/tests/security/pii -q
```

Known-open defects are marked `xfail` with their reason, so they do not mask
new failures — and flip to a hard failure the moment someone fixes one without
unmarking it.

## Baseline (first full run)

68 scenario cases through the consolidated runner:

| Metric | Count |
| --- | --- |
| Total | 68 |
| Passed | 30 |
| Failed | 38 |
| Critical | 20 |
| Unexpected allow | 4 |
| Unexpected block | 8 |
| Wrong attribution | 5 |
| Leakage | 1 |
| Trace failures | 0 |
| Audit failures | 0 |

By category: Injection 15/17 · Scope 10/15 · OutputSafety 4/7 · **PII 1/29**.

Full pytest suite: **127 passed, 37 failed, 11 xfailed** at the time this
report was written.

**Current (per-role PII work):** `pytest tests/security` reports
**232 passed, 29 failed, 11 xfailed**. The failure count moved 37 → 29 as
SF-01 and SF-09 were closed and the suite grew; the remaining 29 are the
open findings below, SF-02 accounting for most of the PII block. The
non-security suite is **1309 passed, 0 failed** — every failure in this
repository is a recorded finding, not an unexplained one.

The PII column is the headline, and the two numbers there mean different
things — worth separating, because conflating them overstates the problem:

- **1/29 passed** counts cases clean on *all six* dimensions.
- **3/25 attributed correctly** is the attribution figure specifically (25 of
  the 29 expect a named rail; the other 4 are false-positive probes).

Detection mostly works. Attribution almost never does. See SF-02.

Trace and audit integrity passed on every single case — the pipeline records
what it did faithfully. The defects are in *what it decides*, not in *what it
reports*.

## Harness correctness

A security suite that over-reports is as dangerous as one that under-reports:
it trains reviewers to ignore it. Two harness defects were found and fixed:

1. **Leakage over-reporting (11 → 1).** `check_leakage()` inspected
   `GuardrailResult.text` on blocked results, but `routers/chat.py` discards
   that field on a block. Now `.text` is only in scope when the request was
   allowed; `block_reason` and step details are always in scope.
2. **Wrong-premise assertions.** Two RBAC/tool tests asserted a *mechanism*
   rather than an *outcome* — explicit deny-listing when the engine is
   default-deny, and treating `knowledge_departments=None` as a bug when it is
   the documented LLM-RBAC kill switch. Both were corrected to assert real
   security properties.

## Coverage matrix

| Category | Cases | Real functions exercised |
| --- | --- | --- |
| PII | 29 | `run_input_guardrails`, `redact_pii`, `check_with_gliner`, `check_secrets` |
| Injection | 17 | `run_input_guardrails` (regex + DeBERTa) |
| Scope | 15 | `run_input_guardrails` (keyword + semantic + re-check) |
| OutputSafety | 7 + 2 | `run_output_guardrails` |
| Policy | 9 | `resolve_pii_policy` |
| RBAC | 30 | `policy_loader.role_config`, `engine._check_permission` |
| Tools | 36 | `resolve_agent_tools`, `agent_allowed_for_role`, `is_handoff_allowed` |
| RAG | 9 | `apply_permission_policy`, `run_output_guardrails` |
| Leakage | 12 | both pipelines, cross-surface + multi-turn |
| Regression | 7 | both pipelines |

## Not yet covered

Honest gaps, so nobody mistakes this for full coverage:

- **Live HTTP path.** Everything runs against pipeline functions directly. The
  router layer (escalation gate, persistence, trace assembly) is covered by the
  existing `backend/tests/` suite, not this one.
- **Database/audit persistence.** Leakage is checked on pipeline surfaces, not
  by querying `messages`/`audit_events` after a real request.
- **Tool execution.** Authorization resolution is tested; actual invocation,
  schema validation and approval flow are not.
- **Multi-turn conversational state.** Turn independence is asserted; genuine
  cross-turn context poisoning through stored history is not.
- **Latency/regression-over-time.** No baseline is persisted between runs, so
  `REGRESSION` status is available but never currently emitted.
