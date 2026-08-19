"""Guardrail Policy Research Agent — integrated extension to Policy Copilot.

Dedicated module for researching guardrail policies, analyzing gaps, simulating
changes, and proposing improvements. Strict GUARDRAIL_ONLY scope enforcement
with multi-layered security boundaries.

SAFETY BOUNDARIES:
- HTTP layer: RBAC permission gating
- Request layer: Scope classification rejects non-guardrail queries
- Tool layer: Strict allowlist enforcement for read-only guardrail tools only
- Code layer: Assert-level checks at every resource access
- Audit layer: All operations logged separately for security review

All tools are read-only (no policy writes). Proposals are human-reviewable
before any approval and share the existing approval infrastructure.
"""

from __future__ import annotations

from app.services.policy_copilot.research.orchestrator import ResearchOrchestrator
from app.services.policy_copilot.research.scope import classify_scope, ScopeClassification

__all__ = ["ResearchOrchestrator", "classify_scope", "ScopeClassification"]
