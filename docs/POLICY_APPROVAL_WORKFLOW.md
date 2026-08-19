# Policy Approval Workflow

How a natural-language request becomes enforced policy — and the points at
which it can stop.

```
ADMIN / CEO
    |
    v
POLICY COPILOT CHAT            POST /policy-copilot/chat   (POLICY_PROPOSE)
    |
    v
INTENT INTERPRETER             refuse -> deterministic -> llm
    |
    +--> REFUSED               hostile request, audited, no proposal
    +--> CLARIFICATION_NEEDED  ambiguous, no proposal
    |
    v
POLICY VALIDATION              10 deterministic checks
    |
    +--> INVALID               unknown entity / inert entity / no authority
    |
    v
IMPACT ANALYSIS + SIMULATION   synthetic values only, read-only
    |
    v
POLICY PROPOSAL                ApprovalRequestModel, status=pending
    |
    +--> REJECT                POST /proposals/{id}/reject  (POLICY_APPROVE)
    |
    v
APPROVE                        existing Policy Center
    |
    v
guardrail_policy/service.py    INDEPENDENT re-validation + its own gating
    |
    v
POLICY VERSION                 guardrail_policy_versions row
    |
    v
store.invalidate()             takes effect immediately, no restart
    |
    v
DETERMINISTIC GUARDRAIL ENGINE runtime enforcement
```

## Why a proposal is an ApprovalRequestModel

Reusing the existing approval table rather than adding a parallel one. It
already has role scoping, decision recording and audit, and a second workflow
would mean two places to look for "what is pending". Copilot proposals are
distinguished by `target_type = "policy_proposal"`.

## When explicit approval is required

A proposal is marked `requires_approval` when either holds:

1. **Critical entity weakened** — the entity is in `CRITICAL_PII_ENTITIES` and
   the proposed action is `ALLOW` or `FLAG`.
2. **Overall risk is HIGH or CRITICAL** — computed deterministically from the
   strength delta, not by an LLM.

`CRITICAL_PII_ENTITIES` was widened during this work from credentials only to
also include `SSN`, `CREDIT_CARD`, `AADHAAR`, `PAN`, `PASSPORT`,
`BANK_ACCOUNT` and `JWT`. Permitting a card number is a disclosure decision of
the same magnitude as permitting an API key.

## Risk model

Deterministic and stated, so the same proposal always carries the same rating
and an approver can predict it.

| Direction | Entity | Proposed | Risk |
| --- | --- | --- | --- |
| WEAKENS | critical | ALLOW | CRITICAL |
| WEAKENS | critical | other | HIGH |
| WEAKENS | ordinary | ALLOW | HIGH |
| WEAKENS | ordinary | other | MEDIUM |
| STRENGTHENS / UNCHANGED | any | any | LOW |

Strength order: `ALLOW < FLAG < MASK < REDACT < ESCALATE < BLOCK`.

## Disabling is not permitting

Since SF-01, disabling a policy row reverts the entity to the **safe default**
rather than permitting it. A `DISABLE_POLICY` proposal therefore carries a
mandatory warning saying exactly that, and requires approval — an admin who
believes "disable == permit" must be corrected, not silently obeyed.

To genuinely permit an entity, set the action to `ALLOW` on an **enabled** row.
That is visible in the UI, risk-classified, approval-gated and versioned.

## Two independent gates

The Copilot's `requires_approval` is advisory to the reviewer. The authoritative
gate is `guardrail_policy/service.py::update_policy`, which runs its own
`_is_critical_pii_weakening()` check at write time. A defect in the Copilot's
risk model cannot produce an unreviewed change.
