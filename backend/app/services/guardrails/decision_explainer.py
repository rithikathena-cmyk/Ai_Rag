"""Optional, post-hoc natural-language explanation of an ALREADY-FINALIZED
guardrail decision — the one place an LLM is allowed to participate in the
guardrail pipeline. Everything upstream of this module — every check in
pipeline.py, risk_analysis.classify_risk(), and policy_engine.decide() — is
deterministic Python and stays the sole authority on ALLOW/BLOCK/REDACT.
This module runs strictly AFTER a `PolicyDecision` already exists, can only
produce display text, and cannot influence, override, or feed back into any
decision — the split the agentic-orchestrator design calls for: "LLM =
ORCHESTRATION/REASONING; Python + existing guardrail engines = ENFORCEMENT;
Policy Engine = AUTHORITY."

Deliberately NOT called from chat.py / orchestrator_graph.py — adding a
synchronous LLM round-trip to every live chat request for an explanation
nobody asked for would be pure added latency and cost. This is called on
demand, after the fact, when a human wants a plain-English account of why a
specific already-decided request was handled the way it was — today, that's
the Policy Copilot's "why was my request blocked?" tool (see
policy_copilot/trace_lookup.py).

Same security posture as policy_copilot/llm_interpreter.py: the model is
sent only labels — a check name, one of the fixed guardrail actions, and the
short internal reason string pipeline.py's own decision map already
produces — never raw user content, never PII, never a real classifier
score. Its output is prose used ONLY as display text; nothing here parses
the reply back into a decision, an action, or anything re-ingested as a
control. On any failure (no API key, provider error, refusal, empty
response) this returns None and the caller keeps its existing deterministic
rendering — never a blank or broken reply.
"""

from __future__ import annotations

import logging

from app.gateway.claude_gateway import GenerationError, claude_gateway
from app.gateway.prompt_manager import load_prompt
from app.gateway.schemas import GenerateRequest, ModelTier

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = load_prompt("guardrail_decision_explainer", "v1").text


def explain_decision(*, blocking_check: str, action: str, detail: str | None = None) -> str | None:
    """`blocking_check`/`action`/`detail` are already-computed, non-sensitive
    labels — the same shape pipeline.py's own trace steps and
    policy_engine.PolicyDecision already carry. Returns None on any failure;
    every caller must already have a deterministic fallback to show instead
    (this function only ever adds to that, never replaces it)."""
    user_message = (
        f"Check: {blocking_check}\nAction: {action}\nReason: {detail or '(none recorded)'}\n\n"
        "Explain this decision in plain language, in one or two sentences."
    )
    try:
        result = claude_gateway.generate(
            GenerateRequest(
                agent_name="guardrail_decision_explainer",
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
                # FAST: a short, bounded explanation task, not a judgment
                # call — see llm_interpreter.py's identical reasoning for its
                # own ModelTier.FAST choice.
                tier=ModelTier.FAST,
                max_tokens=150,
                cache_system=True,
            )
        )
    except GenerationError as exc:
        logger.info("decision_explainer: gateway unavailable (%s)", exc.reason)
        return None

    if result.stop_reason == "refusal":
        return None

    text = result.text.strip()
    return text or None
