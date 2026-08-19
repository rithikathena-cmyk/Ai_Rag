# Policy Copilot — Architecture & Inspection Report

Phase 1 deliverable. **No code has been written yet**, per instruction.

Headline conclusion: **the enforcement plane you describe already exists.**
The Policy Copilot is a natural-language *shell* over control-plane machinery
this repo already has — proposals, validation, risk classification, approvals,
versioning, rollback, simulation and audit are all built and working for the
PII domain. The genuinely new work is the NL interpreter, the chat surface,
and a writable store for two of the three policy domains.

---

## A. Current policy architecture

**Store:** `guardrail_policies` table (`models/guardrail_policy.py`)

| Column | Purpose |
| --- | --- |
| `policy_key` | unique identifier, e.g. `pii.ssn` |
| `category` | `PII` \| `REGEX` \| `WORD_FILTER` \| `SEMANTIC` \| `PROMPT_INJECTION` \| `MESSAGE_LIMIT` |
| `action` | one of `GUARDRAIL_POLICY_ACTIONS` |
| `configuration` | JSONB — `input_action`, `output_action`, `detection_sources`, `redaction_format`, `severity` |
| `mode` | `ENFORCE` \| `DRY_RUN` |
| `enabled`, `priority`, `version` | |
| `created_by`, `updated_by`, timestamps | |

**History:** `guardrail_policy_versions` — `previous_configuration`,
`new_configuration`, `changed_by`, `reason`, `changed_at`. Never deleted.

**Resolution:** `guardrail_policy/pii_policy.py::resolve_pii_policy(entity)`
→ DB row if present, else `_SAFE_PII_DEFAULTS`, else `_GENERIC_DEFAULT`.

**Cache invalidation:** `guardrail_policy/store.py::invalidate()` — called
after every committed write, so DB policy changes take effect **immediately,
without a restart**.

**Action vocabulary:** `ALLOW · FLAG · MASK · REDACT · BLOCK · ESCALATE`
(real constant, `models/guardrail_policy.py`). ESCALATE currently resolves as
BLOCK.

---

## B. Current RBAC architecture

**Two layers, one file — `backend/config/llm_rbac.yaml`:**

1. Coarse `Permission` enum (`core/permissions.py`) → `rbac_permissions:` per
   role → gates nav and endpoints via `require_permission()`.
2. Fine-grained named actions → `permissions.allow` / `permissions.deny` →
   gates what `/chat` and `/search` may do, via
   `llm_rbac/engine.py::authorize_llm_request` → `_check_permission`.

**Verified properties:** default-deny (an action absent from `allow` is
refused, engine.py:108); CEO is not a superset of Admin; 5 roles each with
`knowledge_departments`, `tiers_allowed`, `tools`, `sql_allowed_tables`,
`quotas`, `approval_required_actions`.

**⚠ Critical constraint:** `policy_loader.role_config()` and
`load_yaml_config()` are both `@lru_cache(maxsize=None)` with **no
invalidation function**. RBAC config is read once per process.

This was proven empirically during this session: after editing
`llm_rbac.yaml`, the running backend continued serving the old policy and
returned 403 until the process was restarted.

---

## C. Current PII architecture

| Layer | Component | Coverage |
| --- | --- | --- |
| Deterministic | `guardrails/pii.py` | 8 labels: `AADHAAR, CREDIT_CARD, DATE_OF_BIRTH, EMAIL, IP_ADDRESS, PAN, PHONE, SSN` |
| Deterministic | `guardrails/secrets.py` | credential shapes (API keys, tokens, private keys) |
| ML | `presidio_check.py` | 6 allowlisted exotic types — **never fired in 68 evaluation scenarios** |
| ML | `gliner_check.py` | 4 natural-language labels (address, government ID, financial account, medical) |

**Policy tiers** (`pii_policy.py`): personal data → `MASK`/`REDACT`;
credentials → `BLOCK`/`BLOCK`; unlisted → personal-data default.

**Uncovered entities** requested in Phase 5A: `PASSPORT`, `BANK_ACCOUNT`,
`IFSC`, `EMPLOYEE_ID`, `CUSTOMER_ID`, `JWT` have no deterministic recognizer.
`ADDRESS` is GLiNER-only.

**Open defect (SF-01):** a *disabled* policy row resolves to `allow`, removing
protection entirely rather than reverting to the safe default. This is
precisely the invariant your Phase 7 requires, and it is currently violated —
a stale disabled row is leaving credit cards in cleartext right now.

---

## D. Current guardrail architecture

14 input checks → risk analysis → policy check → agent/RAG → 8 output checks →
final policy, orchestrated by a LangGraph `StateGraph`
(`guardrails/orchestrator_graph.py`).

Runtime enforcement is fully deterministic and does not call an LLM for
security decisions. Phase 13's requirement — the Copilot must not participate
in runtime enforcement — is already satisfied by construction, provided the
Copilot only ever writes policy rows.

---

## E. Existing Policy Center — **most of the Copilot's backend already exists**

`routers/guardrail_policies.py`, gated on `MANAGE_GUARDRAIL_POLICIES`
(CEO + Admin only):

| Endpoint | Maps to your phase |
| --- | --- |
| `GET /guardrail-policies` | Phase 15 (list) |
| `GET /guardrail-policies/{id}` | Phase 15 |
| `GET /guardrail-policies/{id}/versions` | Phase 12 (versioning) |
| `POST /guardrail-policies` | Phase 10 |
| `PATCH /guardrail-policies/{id}` | Phase 10/11 |
| `POST /guardrail-policies/{id}/rollback` | Phase 22 (rollback) |
| `POST /guardrail-policies/test` | **Phase 9 (simulator)** |

**Already implemented in `guardrail_policy/service.py`:**

- `validate_configuration()` / `validate_action()` — Phase 6
- `_is_critical_pii_weakening()` / `_is_significant_threshold_weakening()` —
  Phase 8 risk classification
- Automatic `ApprovalRequestModel` creation that **blocks** the change pending
  approval, with `approval_reason_code` — Phase 11
- `expected_version` optimistic concurrency
- Audit on every path — Phase 18
- `playground.py::evaluate()` with `_risk_for_action()` — Phase 9

Frontend `GuardrailPolicyPage.tsx` already has three tabs: **Policies**,
**Test Playground**, **Approvals**.

---

## F. Existing trace / audit architecture

- **Trace:** `message.trace` JSONB per message; `routers/traces.py` with
  self-scoping; `TracesPage.tsx`. Raw detail gated on `VIEW_AUDIT_LOGS`.
- **Audit:** `audit_events` table, `services/audit/logger.py::log`,
  `AuditEventType` incl. `POLICY_DISABLED`, outcomes incl. `DENIED`.
- **Approvals:** `ApprovalRequestModel` — `action`, `target_type`,
  `target_id`, `requested_by`, `role`, `payload` JSONB, `status`,
  `decided_by`, `decided_at`, `reason`.

---

## G. Existing frontend structure

React 18 + Vite + TS + Tailwind v4, 16 pages, nav in
`components/layout/nav.ts` filtered by `isNavItemVisible(item, hasPermission, role)`,
routes gated by `<ProtectedRoute permission=... denyRoles=...>`.

Chat UI already exists (`ChatPage.tsx`, `SecurityActivityPanel.tsx`) and is
reusable as the Copilot's conversation surface.

---

## H. Components reusable as-is

1. **Approval workflow** — `ApprovalRequestModel` + `routers/approvals.py`.
   A `PolicyProposal` is an approval request with a richer payload.
2. **Policy versioning + rollback** — complete, including history retention.
3. **Simulator** — `playground.py::evaluate()` already runs a candidate policy
   against text and returns action + risk. This is Phase 9.
4. **Risk classification** — `_is_critical_pii_weakening` already detects
   exactly your Phase 11 "high-risk" cases.
5. **Deterministic validation** — `validate_configuration`/`validate_action`.
6. **Audit + trace** infrastructure.
7. **`MANAGE_GUARDRAIL_POLICIES`** permission and its CEO+Admin grant.
8. **Policy resolution with safe defaults** — the pattern (DB row overlays a
   hardcoded safe default) is exactly right and should be copied for RBAC.
9. **Claude gateway** — for the interpreter's LLM call, with per-role tiering
   and cost logging already handled.

## I. Components needing modification

| Component | Change | Why |
| --- | --- | --- |
| `pii.py::_resolve_match` | disabled row → safe default, not `allow` | Phase 7 invariant, currently violated (SF-01) |
| `permissions.py` | add granular `policy.read/propose/simulate/approve/manage` | Phase 2 asks for finer control than one flag |
| `policy_loader.py` | add cache invalidation | RBAC changes currently need a restart |
| `llm_rbac.yaml` | needs a DB overlay | see below |
| `nav.ts`, `App.tsx` | add gated Policy Copilot route | Phase 16 |
| `GuardrailPolicyPage.tsx` | surface proposals from the Copilot | Phase 17 — do not build a second UI |

## J. The decisive finding — and the decision you need to make

**Two of your three policy domains have no writable store.**

| Domain | Store | Versioned | Hot-reload | Copilot can apply? |
| --- | --- | --- | --- | --- |
| **PII** (5A) | DB `guardrail_policies` | yes | yes (`store.invalidate()`) | **yes, today** |
| **RBAC** (5B) | `llm_rbac.yaml` file | no | **no** (`lru_cache`, no invalidation) | **no** |
| **AGENT** (5C) | `llm_rbac.yaml` (`tools:` per role) | no | **no** | **no** |

A Copilot that "applies" an RBAC change today would either have to rewrite a
YAML file on disk — with no versioning, no rollback, no audit trail, and no
effect until someone restarts the process — or silently fail. Both are
unacceptable under your own Phase 24 rules (7, 12, 13, 14, 18).

### Three options

**Option 1 — DB overlay (recommended).** Add `rbac_policies` and
`agent_policies` tables that *overlay* the YAML defaults, exactly as
`guardrail_policies` overlays `_SAFE_PII_DEFAULTS`. YAML remains the
checked-in baseline; the DB holds deltas with versions, approvals and
rollback. Requires adding invalidation to `policy_loader`.
*Pro:* one proven pattern, all three domains behave identically, no restart.
*Con:* two new tables + resolution changes in a security-critical path.

**Option 2 — PII-only first.** Ship the Copilot for the PII domain, where
everything already works, and defer RBAC/agent domains.
*Pro:* deliverable quickly with near-zero risk; proves the NL layer.
*Con:* only one of three domains.

**Option 3 — Copilot proposes, human applies.** For RBAC/agent, the Copilot
emits a reviewed YAML diff rather than applying anything.
*Pro:* no schema change; keeps config in version control.
*Con:* no runtime apply, no rollback via the product, needs a deploy.

**My recommendation: Option 2 then Option 1.** Build the interpreter,
validation, proposal and chat surface against the PII domain where the
enforcement plane is complete and proven — that is genuinely a thin layer over
existing machinery. Then migrate RBAC/agent to the DB overlay as a separate,
carefully-tested phase, because changing how RBAC resolves is the single most
security-sensitive change in this codebase.

---

## Proposed implementation plan

| Step | Scope | Gate |
| --- | --- | --- |
| 0 | Fix SF-01 (disabled → safe default) | **Prerequisite.** Phase 7 is a stated invariant; shipping a policy tool onto a store that mis-handles "disabled" would be building on a known hole. |
| 1 | Granular permissions `policy.*`; backend 403 `POLICY_MANAGEMENT_FORBIDDEN` | Phase 2 |
| 2 | `PolicyIntent` Pydantic schemas + `PolicyIntentInterpreter` (LLM → strict JSON, rejected if it fails validation) | Phase 4 |
| 3 | `PolicyValidationEngine` wrapping existing `validate_*` + the 10 checks | Phase 6 |
| 4 | `PolicyImpactAnalyzer` extending `_is_critical_pii_weakening` with affected roles/flows | Phase 8 |
| 5 | Wire simulation to existing `playground.evaluate()` | Phase 9 |
| 6 | `POST /policy-copilot/chat`, `/proposals`, `/proposals/{id}/approve|reject` on top of `ApprovalRequestModel` | Phases 3, 10, 11 |
| 7 | Copilot-specific guardrails: run the request through the existing injection rails first; never let interpreted intent widen the caller's own authority | Phase 21 |
| 8 | Frontend `PolicyCopilotPage` + proposal card; surface proposals in the existing Policy Center | Phases 16, 17 |
| 9 | Tests: NL→intent, validation, authorization 403, injection rejection, safe defaults, rollback | Phase 20 |
| 10 | RBAC/agent DB overlay (Option 1) | separate phase |

Tests run after each step; no security-sensitive behaviour changed silently.
