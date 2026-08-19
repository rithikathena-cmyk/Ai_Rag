"""In-process cache of GuardrailPolicyModel rows, with explicit invalidation
on write — the piece neither yaml_config.py's load_yaml_config() (cached
forever via @lru_cache on the FILE) nor services/llm_rbac/policy_loader.py
(cached forever via @lru_cache on role_config()) solve today: both require a
process restart to pick up a change. Every write path in
services/guardrail_policy/service.py calls invalidate() in the same
transaction as its DB commit, so a policy change is visible to the NEXT
guardrail check in THIS process immediately; other worker processes pick it
up on their own next TTL expiry (see _CACHE_TTL_SECONDS) — the same
documented single-process-state limitation services/guardrails/escalation.py
already carries, not solved here (a pub/sub invalidation bus is out of scope
for this pass).

Fail-safe by construction, not by a special case: a DB error while loading
falls back to the last-known-good cache (or an empty result if there isn't
one yet), so every caller's own "no override found" path — which already
means "use the existing YAML/settings default," never "allow everything" —
is exactly what runs when the policy store is unreachable. See module
docstrings on semantic_check.py/deberta_injection_check.py/length.py for
why "no override" is always the safe direction here.
"""

import logging
import threading
import time
from dataclasses import dataclass

from app.db.postgres import new_session
from app.models.guardrail_policy import GuardrailPolicyModel

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 5.0


@dataclass(frozen=True)
class CachedPolicy:
    policy_key: str
    name: str
    category: str
    enabled: bool
    action: str
    priority: int
    configuration: dict
    mode: str
    # Added for pii_policy.py's PIIPolicyResolution.policy_version (tags a
    # captured PII occurrence with which policy version governed it — see
    # models/pii_occurrence.py). Defaulted so this stays backward-compatible
    # with any other construction of this dataclass.
    version: int = 0


_lock = threading.Lock()
_cache: list[CachedPolicy] = []
_loaded_at: float = 0.0


def invalidate() -> None:
    """Forces the next read in THIS process to reload from the DB. Call
    right after any committed create/update/rollback/approval-applied
    write — see service.py."""
    global _loaded_at
    with _lock:
        _loaded_at = 0.0


def _load_from_db() -> list[CachedPolicy]:
    db = new_session()
    try:
        rows = db.query(GuardrailPolicyModel).all()
        return [
            CachedPolicy(
                r.policy_key, r.name, r.category, r.enabled, r.action, r.priority, r.configuration, r.mode, r.version,
            )
            for r in rows
        ]
    finally:
        db.close()


def _all() -> list[CachedPolicy]:
    global _cache, _loaded_at
    with _lock:
        if (time.monotonic() - _loaded_at) <= _CACHE_TTL_SECONDS:
            return _cache
        try:
            _cache = _load_from_db()
            _loaded_at = time.monotonic()
        except Exception:
            logger.exception("guardrail_policy store: DB load failed, serving last-known-good cache")
            # Deliberately does NOT bump _loaded_at — the next call retries
            # rather than trusting a stale cache for a full TTL window on a
            # transient failure.
        return _cache


def get_active_policies(category: str) -> list[CachedPolicy]:
    """Enabled, ENFORCE-mode rows for a multi-row category (REGEX,
    WORD_FILTER, PII), sorted by priority ascending (lower number = higher
    precedence, matching GuardrailPolicyModel.priority's docstring)."""
    matches = [p for p in _all() if p.category == category and p.enabled and p.mode == "ENFORCE"]
    return sorted(matches, key=lambda p: p.priority)


def get_all_policies(category: str) -> list[CachedPolicy]:
    """Every row for a category regardless of enabled/mode — for callers
    that must distinguish "explicitly disabled/dry-run by an admin" from
    "no policy exists at all" (get_active_policies() collapses both into
    "not returned", which is right for the checks that only care about
    active enforcement, but wrong for services/guardrail_policy/pii_policy.py's
    resolve_pii_policy(): an entity a row exists for but was deliberately
    disabled must NOT silently fall back to the safe default — that default
    is for "nothing configured", not "admin turned this off on purpose"."""
    return sorted((p for p in _all() if p.category == category), key=lambda p: p.priority)


def get_policy(policy_key: str) -> CachedPolicy | None:
    """Any single named policy row regardless of enabled/mode — for the
    singleton threshold/limit categories (SEMANTIC, PROMPT_INJECTION,
    MESSAGE_LIMIT), where the caller needs to distinguish "no override
    exists, use the YAML/settings default" (returns None) from "an override
    exists but is currently disabled" (returns a row with enabled=False) —
    get_active_policies() can't make that distinction since it only returns
    enabled rows."""
    for p in _all():
        if p.policy_key == policy_key:
            return p
    return None
