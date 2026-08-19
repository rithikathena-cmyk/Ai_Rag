# Per-role PII policy and partial masking

Two things a PII policy can now say that it could not before:

- **How much of a masked value survives.** `reveal_last: 4` on a `MASK` leaves
  the last four characters visible and replaces everything before them.
- **Which roles are exempt.** `role_overrides` names roles that resolve to
  different actions from everybody else.

Together they express the request this was built for:

> Mask phone numbers in output, only show the last four digits, visible for
> employees; HR can see the whole number.

```
user             -> You can reach the site manager on ###0142.
hr               -> You can reach the site manager on 555-0142.
project_manager  -> You can reach the site manager on ###0142.
ceo              -> You can reach the site manager on ###0142.
admin            -> You can reach the site manager on ###0142.
(no role)        -> You can reach the site manager on ###0142.
```

---

## The stored shape

Both live in a PII policy row's `configuration`, alongside the base actions:

```json
{
  "entity": "PHONE",
  "input_action": "MASK",
  "output_action": "MASK",
  "reveal_last": 4,
  "role_overrides": {
    "hr": { "output_action": "ALLOW" }
  }
}
```

`role_overrides` is an **override, not a replacement**. A role with no entry
gets the row's base actions, so adding a role to the system can never leave it
silently unprotected — the failure mode of a replacement map, where an
unlisted role falls through to nothing.

Role keys are spelled as `llm_rbac.yaml` spells them: `user`, `hr`,
`project_manager`, `ceo`, `admin`.

---

## Resolution

`pii_policy.resolve_pii_policy(entity, role)` is still the only place any
detector asks "what should happen to this entity". `role` is new and
optional:

| Call | Resolves to |
| --- | --- |
| `resolve_pii_policy("PHONE", "hr")` | HR's override, falling back to the base actions for any slot the override omits |
| `resolve_pii_policy("PHONE", "user")` | the base actions — `user` has no entry |
| `resolve_pii_policy("PHONE")` | the base actions |

**`role=None` never picks up a role's relaxation.** Audit sanitisation, the
evaluation harness and the Copilot's own simulation all call without a role,
and a caller that cannot state who it is does not get an exemption.

The role reaches the resolver through the pipeline, not around it:

```
chat.py  ->  orchestrator_graph (state["role"])
         ->  run_input_guardrails(text, role)   -> redact_pii(..., role=role)
         ->  run_output_guardrails(text, role)  -> redact_pii(..., role=role)
         ->  resolve_pii_policy(entity, role)
```

`redact_pii(text)` with no direction — the "just scrub this string" call used
for scope re-scoring and for logging — takes no role and never resolves one.

---

## Masking

`reveal_last` is honoured by `pii.build_redaction_token`, which is the single
token builder shared by the regex path and GLiNER's span path. Two properties
worth stating because they are load-bearing:

- **A MASK can never reveal the whole value.** A `reveal_last` at or beyond the
  value's length is reduced to `len - 1`.
- **The count is bounded to 1–8 at the schema.** Anything outside that range
  is dropped back to the entity's built-in mask shape rather than clamped —
  clamping "show the last 40 digits" to 8 would hand out more than the
  built-in shape does, in the one direction that matters.

`reveal_last` only applies to `MASK`. `REDACT`, `BLOCK` and `ESCALATE` replace
or refuse the value entirely, so a count stored against them would sit inert
in the row until a later switch back to `MASK` silently resurrected it — the
Copilot therefore never records one on a non-MASK action.

---

## Getting there from natural language

`policy_copilot/interpreter.py` parses both deterministically, before any model
is consulted.

**The reveal count** comes from the phrasings admins actually use — "show last
four digits", "reveal last 2 characters", "keeping the last 6 visible" — as
digits or words, and only when the action is `MASK`.

**A role exception is deliberately hard to trigger.** It requires an explicit
full-visibility phrase — the role, a seeing verb, and one of
`all / full / whole / entire / complete / unmasked / raw`:

| Sentence | Exception? |
| --- | --- |
| `... and hr can see all the number` | yes |
| `mask phone in output for hr` | no — HR is the subject of the restriction |
| `hr should not see phone numbers` | no |
| `what can hr see?` | no — a question, answered rather than applied |

Naming a role must never, by itself, widen that role's access. The
alternative — inferring an exemption from a role mention — fails open on
exactly the sentences where an admin is describing who a restriction is *for*.

---

## Gating

A role exception is a relaxation for the role that receives it, and is gated
like one.

`policy_copilot/validation.py`:

1. The role must be known.
2. The exception's direction must be one the request actually changes.
   Otherwise the override would be written against the safe default, exempting
   a role from a rule the proposal never displayed.
3. An exception on a **critical entity** requires explicit approval.
4. An exception **weaker than the base action** requires explicit approval.

**The write path enforces the same rule independently.** Both critical-entity
gates in `guardrail_policy/service.py` compared only the top-level
`input_action`/`output_action`, and "HR sees the whole SSN" changes neither —
so a per-role exception would have been the one way to relax a critical entity
without an approval step. `_overrides_weaken()` now closes that: a create or
update whose `role_overrides` give any role less than the base actions routes
through approval, whether it came from the Copilot or from the Policy Center.

The row itself is validated too. `PIIPolicyConfig` (still `extra="forbid"`)
accepts `reveal_last` only as an integer 1–8, and `role_overrides` only as a
map from a **real role name** to `input_action` / `output_action` /
`reveal_last`. An unknown role, an unknown action, or any other key is
rejected at the write, not stored and ignored — a typo'd role would otherwise
sit in the row looking like an active exception while matching nobody.

`policy_copilot/impact.py` rates the proposal on **the worst of the base change
and every exception**. Rating "MASK PHONE, HR sees everything" on its base
action alone would print `LOW` next to a row handing a role every digit.

The proposal shows one line per role, always — not only the exempted ones:

```
PHONE OUTPUT: REDACT -> MASK (last 4 visible)   WEAKENS   risk HIGH
  blast: Every request from every role except hr, which is exempted.

  Employee         MASK    ###0142
  HR               ALLOW   555-0142   <- exception
  Project Manager  MASK    ###0142
  CEO              MASK    ###0142
  Admin            MASK    ###0142
```

Those strings are produced by `pii.preview_redaction()`, which normalises
through the real recognizer and builds the token with the real builder. The
digit count an approver reads is the digit count they will get. The sample
values are synthetic throughout — `555-0142` is in the reserved fictional
range.

---

## Applying

`policy_copilot/apply.py` writes through `guardrail_policy/service.py`, the
same create/update path the Policy Center uses. There is no second write path.

Overrides are **merged per role**, not replaced wholesale: approving an
exception for HR must not silently revoke one granted to another role by an
earlier, separately-approved proposal.

`store.invalidate()` runs afterwards, so the change takes effect on the next
request — unlike YAML-backed configuration, which is `lru_cache`d for the life
of the process.

---

## Where it shows up

- **Policy Copilot proposal card** — the per-role table above, plus a
  `last N visible` marker next to the proposed action.
- **Active PII policy sidebar** — `reveal_last` and any role that resolves
  differently from the base.
- **`explain_policy`** — asking "explain the PHONE policy" lists the reveal
  count and every per-role exception.

The sidebar's overrides are derived by asking the resolver once per role,
rather than by reading the row's `role_overrides` map. That view cannot
disagree with enforcement: what a role actually gets at runtime is what
appears in the UI.

---

## Not covered

**Role exceptions reach only the deterministic recognizers.** GLiNER and
Presidio redact their spans earlier in the pipeline, with no role and no
policy — `check_with_gliner(text)` takes neither. So an exception on an entity
those detectors claim (`ADDRESS`, government ID, financial account, medical)
applies to the regex-detected values and not to the ML-detected ones. The
proposal says so explicitly whenever the entity's detection is not
`DETERMINISTIC`, rather than presenting an exception that half-works as one
that works.

`role_overrides` is per-entity PII policy only. It is **not** a general RBAC
overlay: it cannot grant permissions, change what documents a role retrieves,
or alter any non-PII guardrail. Those remain governed by `llm_rbac.yaml` and
the retrieval permission filter, neither of which the Copilot can write.
