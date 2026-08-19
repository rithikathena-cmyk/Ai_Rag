# Agentic Guardrails Architecture — Target Design

Phase 1 design deliverable. Companion to `CURRENT_ARCHITECTURE.md` (what
exists) and `SECURITY_FINDINGS.md` (what is broken and why).

---

## 1. The governing principle

> **Agents reason. Deterministic services decide.**

An LLM or agent may classify, investigate, propose, and evaluate. It may
never be the final authority on:

`RBAC` · `authorization` · `policy enforcement` · `PII enforcement` ·
`tool permissions` · `schema validation` · `audit requirements`

This is not caution for its own sake. Every security property that currently
holds in this system (§4 of `CURRENT_ARCHITECTURE.md`) holds because a
deterministic function enforces it. Making an agent the authority would
convert each of those from a guarantee into a probability.

### Three component categories

| Category | Authority | Examples |
| --- | --- | --- |
| **Deterministic Security Services** | Final | regex + checksums, RBAC engine, policy engine, retrieval permission filter, schema validation, audit writer |
| **ML/LLM Security Agents** | Advisory | PII contextual detection, injection classification, clause-level scope reasoning, document injection detection |
| **Security Evaluation Agents** | None (offline) | Red Team, Regression, Investigation, Evaluation |

An advisory component contributes *findings*. It never returns an action.

---

## 2. Target flow

```
USER
 ↓
AUTH  ──────────────── deterministic
 ↓
RBAC  ──────────────── deterministic (default-deny)
 ↓
AGENT ORCHESTRATOR ─── builds AgentExecutionPlan, validated server-side
 ↓
SECURITY SUPERVISOR ── fan-out, collects findings, decides nothing
 ↓
 ├── PII Agent          hybrid: deterministic authoritative + ML additive
 ├── Injection Agent    hybrid
 └── Scope Agent        hybrid: clause-level
 ↓
SECURITY FINDINGS ───── the complete set, not the first hit
 ↓
POLICY ENGINE ───────── deterministic, FINAL AUTHORITY
 ↓
ALLOW / FLAG / MASK / REDACT / BLOCK / ESCALATE
 ↓
RAG AGENT ───────────── authorization BEFORE context construction
 ↓
TOOL SECURITY ───────── schema + risk + approval, before execution
 ↓
MODEL / LLM
 ↓
OUTPUT SECURITY AGENT ─ PII, secrets, leakage, toxicity, grounding, citation
 ↓
FINAL POLICY ────────── deterministic
 ↓
RESPONSE + TRACE + AUDIT
```

---

## 3. The central change: findings ≠ decision

This is the architectural fix for SF-02, and everything else depends on it.

**Today** — first block wins, and supplies the reason:

```
check → block? → return immediately
```

Consequence: PII rails run last, so 28 of 29 PII scenarios are credited to an
unrelated rail. Credit cards are attributed to the prompt-injection classifier.

**Target** — collect, then decide:

```python
findings = supervisor.collect(message, context)   # every agent runs
decision = policy_engine.decide(findings, role, resource, policy)
```

Worked example — `"My card is 4111 1111 1111 1111. Ignore previous instructions."`

| Agent | Finding |
| --- | --- |
| PII | `CREDIT_CARD` detected, `method=regex+luhn`, `confidence=1.0`, `severity=CRITICAL` |
| Injection | `DIRECT_INJECTION` detected, `method=regex`, `confidence=1.0` |
| Scope | `IN_SCOPE` |

Policy Engine → **BLOCK**, `primary_reasons=[CREDIT_CARD, PROMPT_INJECTION]`.

Both are reported. Neither is lost because the other fired first.

**Note:** the existing `deferred_scope_step` mechanism is a hand-rolled,
single-case version of exactly this. Generalising it is the change — the
codebase already discovered it needed this shape.

### Short-circuiting is still allowed — but only as an optimisation

A `BLOCK` from a deterministic credential detector may still stop execution
early to avoid cost. What it must **not** do is stop *finding collection* for
rails that have already run, or claim sole attribution.

---

## 4. Finding model

One shape, per agent, all advisory:

```python
@dataclass(frozen=True)
class SecurityFinding:
    agent: str                    # pii | injection | scope | rag | output
    category: str                 # CREDIT_CARD | DIRECT_INJECTION | OUT_OF_SCOPE
    detected: bool
    confidence: float             # 1.0 for deterministic matches
    detection_method: str         # regex | checksum | presidio | gliner | deberta | embedding | llm
    severity: str                 # CRITICAL | HIGH | MEDIUM | LOW
    evidence: str                 # never the raw value — span offsets or label only
    value_span: tuple[int, int] | None
    recommended_action: str       # ADVISORY ONLY
```

`recommended_action` is deliberately named a *recommendation*. The policy
engine is free to ignore it, and must log when it does.

`evidence` must never carry the matched value — it is persisted to the trace
and rendered in the admin UI. This preserves the existing (verified) property
that raw values never reach trace surfaces.

---

## 5. Per-agent design

### PII Security Agent — hybrid, deterministic authoritative

Structured identifiers must **not** depend on ML confidence. Measured
evidence (SF-07): GLiNER scores the same SSN 0.727 when written out and 0.466
when abbreviated; card numbers score 0.448 and are missed entirely.

```
candidate  →  normalize  →  structural validation  →  checksum  →  ENTITY
                                                          │
                                              Luhn (card), Verhoeff (Aadhaar),
                                              format+registry (IFSC), area/group (SSN)
```

- **Tier 1 — deterministic, authoritative.** Regex + normalisation +
  validator + checksum. Confidence 1.0. Cannot be overridden by ML.
- **Tier 2 — ML, additive only.** Presidio/GLiNER contribute findings for
  contextual PII (addresses, names in context) that have no fixed shape.
  Never able to *remove* a Tier 1 finding.

Closes SF-06 (6 uncovered entities) and SF-07 (phrasing sensitivity).

### Injection Security Agent

Deterministic patterns remain the floor; DeBERTa is additive (already the
documented policy). Adds:

- **decode-then-rescan** for base64/hex/unicode-escape payloads (SF-08)
- **normalisation** for character-spaced evasion
- RAG indirect injection — scanning *retrieved documents*, not just user input

### Scope Security Agent — clause-level

The mixed-scope policy, decided and justified:

> A compound message is refused if **any request-bearing clause** is out of
> scope.

Rejected alternatives, with measurements:

| Policy | `weather? + leave policy?` | Verdict |
| --- | --- | --- |
| Whole-message similarity (today) | 0.672 → **allowed** | bypass (SF-03) |
| Best-clause similarity | 0.863 → **allowed** | strictly worse |
| **All request clauses must pass** | weather clause fails → **refused** | correct |

Non-request fragments (contact details, pleasantries) must not count toward
the score — that is what causes the false refusals in SF-05. Document/policy
references (`GEN-HR-POL-101`) are a recognised valid request class that
bypasses topical similarity while remaining subject to RBAC and retrieval
permissions.

### RAG Agent

`retrieved ≠ authorized`. Order is fixed and non-negotiable:

```
query → Qdrant candidates → metadata authorization → classification
      → document injection scan → document PII scan → SAFE CONTEXT → LLM
```

Authorization runs **before** context construction, never after.

### Output Security Agent

```
LLM output → PII → secrets → system-prompt leakage → toxicity
           → groundedness → citation → schema → policy → response
```

Raw model output is never returned before this completes. System-prompt leak
detection gains natural-language patterns and similarity against the real
configured prompt (SF-04).

---

## 6. Policy Engine — the final authority

```
Findings + Role + Resource + Policy  →  SecurityDecision
```

Resolution rules, in order:

1. **Explicit enabled policy row** → use it.
2. **Disabled row** → **safe default**. Never ALLOW. (SF-01)
3. **No row** → safe default.
4. **Explicit ALLOW** → only when configured *and* authorized *and* approved.

Rule 2 is the one currently violated: `enabled=False` resolves to `allow`,
which is why a stale disabled row left credit cards unprotected in cleartext.

The engine must also record, per decision: which findings it considered, which
recommendation it overrode, and the active `policy_version`.

---

## 7. Evaluation lifecycle

```
RED TEAM AGENT → generates scenarios
       ↓
REAL PIPELINE  → no mocked security logic, ever
       ↓
DETERMINISTIC ASSERTIONS → expected vs actual across all six dimensions
       ↓
SECURITY FINDING → REGRESSION TEST → FIX → RETEST
```

The Red Team Agent generates; it never judges. Verdicts come from the
deterministic assertions in `backend/tests/security/framework.py`, which is
already built and already produced SF-01 through SF-08.

Red-team execution is restricted to an isolated evaluation environment. No
autonomous attack traffic against production.

---

## 8. Agent trace

Extends `message.trace` rather than replacing it:

```
agent_trace_id · request_id · user_id · role · agent · input · plan
guardrails · tools · rag · model · policy_version · decisions
latency · errors · final_action
```

Every agent execution is recorded, including agents that found nothing —
absence of a finding is itself auditable evidence.

---

## 9. What must not regress

The evolution is only successful if these still hold afterwards:

1. Authorization precedes retrieval.
2. Routing confers no permissions.
3. Default-deny for unlisted capabilities.
4. Raw classifier internals stay behind `VIEW_AUDIT_LOGS`.
5. Raw PII never reaches trace, audit, or frontend surfaces.
6. Deterministic floor holds when every model is unavailable.
7. Trace and audit integrity — currently 68/68.

`backend/tests/security/` exists to make a regression in any of these visible
immediately.
