# Policy Versioning & Rollback

Versioning is **pre-existing infrastructure** that the Policy Copilot reuses.
No new versioning system was introduced.

## Storage

| Table | Role |
| --- | --- |
| `guardrail_policies` | current state, with a `version` integer |
| `guardrail_policy_versions` | full history: `previous_configuration`, `new_configuration`, `changed_by`, `reason`, `changed_at` |

History is append-only. Nothing deletes a version row.

## Concurrency

`update_policy()` takes `expected_version` and rejects a stale write, so two
administrators editing the same policy cannot silently overwrite each other.

## Rollback

`POST /guardrail-policies/{id}/rollback` restores a prior configuration by
creating a **new** version whose `new_configuration` is the old one — it does
not delete or rewind history. The Copilot's `ROLLBACK_POLICY` intent
("rollback the credit card policy to version 18") produces a proposal targeting
this endpoint; it does not roll back directly.

## What a version does NOT capture

Honest limits, so nobody assumes more coverage than exists:

- **Safe defaults are not versioned.** `_SAFE_PII_DEFAULTS` lives in code. An
  entity with no row has no version history, because there is nothing to
  version — its protection changes only when the code changes.
- **A disabled row still has history**, but since SF-01 it is not what is in
  force. The resolution reports `source="default"` and
  `disabled_row_present=True`, so the UI can show "disabled rule — safe default
  in force" rather than implying the row is active.
- **RBAC and agent policy are not versioned at all** — they live in
  `llm_rbac.yaml`, which is a file. This is precisely why the Copilot is
  read-only for those domains. See `POLICY_COPILOT_ARCHITECTURE.md` §J.

## Cache behaviour

DB policy changes call `store.invalidate()` and take effect immediately, with
no restart. YAML-backed config (`llm_rbac.yaml`, `guardrails.yaml`) is
`@lru_cache`d with no invalidation and **requires a process restart** —
verified empirically during this work.
