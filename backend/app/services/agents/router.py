"""Multi-agent supervisor/router — decides which specialized agent
(services/agents/policies.py's AGENT_TOOLS/AGENT_DEPARTMENTS) should handle
a request, and classifies its business intent. This is a BUSINESS
classification only, never a security decision: routing runs after the
existing input guardrails (run_input_guardrails(), unchanged) have already
passed the message, and every agent/tool this router can select is still
re-checked against the caller's real RBAC grant by policies.py before
anything executes — a wrong or even adversarially-influenced routing
decision can misroute a request to the wrong SPECIALIST, never grant access
to something RBAC wouldn't already allow.

Uses the exact same "prompt states the JSON shape, json.loads() + defensive
.get() extraction, typed fallback on any failure" pattern already
established by services/evaluation/generation_judge.py and
services/retrieval/query_rewrite.py — no .with_structured_output()/tool-
calling machinery exists anywhere in this codebase yet, so this doesn't
introduce a new one. An unrecognized agent name, malformed JSON, a timeout,
or any other failure ALWAYS falls back to AgentName.GENERAL_RAG with
confidence=0.0 — never lets an unvalidated string reach the caller.
"""

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from app.core.config import settings
from app.gateway.claude_gateway import GenerationError, claude_gateway
from app.gateway.prompt_manager import load_prompt
from app.gateway.schemas import GenerateRequest, ModelTier

ROUTER_PROMPT = load_prompt("supervisor_router", "v2")
ROUTER_SYSTEM_PROMPT = ROUTER_PROMPT.text

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-router")


class AgentName(StrEnum):
    PRODUCTION = "production"
    MAINTENANCE = "maintenance"
    QUALITY = "quality"
    INVENTORY = "inventory"
    HR = "hr"
    GENERAL_RAG = "general_rag"
    GENERAL_CONVERSATION = "general_conversation"


class Intent(StrEnum):
    DOCUMENT_QUERY = "DOCUMENT_QUERY"
    DATA_LOOKUP = "DATA_LOOKUP"
    SUMMARY = "SUMMARY"
    ANALYSIS = "ANALYSIS"
    COMPARISON = "COMPARISON"
    HOW_TO = "HOW_TO"
    ACTION_REQUEST = "ACTION_REQUEST"
    APPROVAL_REQUEST = "APPROVAL_REQUEST"
    GENERAL_CHAT = "GENERAL_CHAT"
    CLARIFICATION = "CLARIFICATION"


_CAPABILITIES = frozenset({"rag", "analytics", "report"})


class RoutingDecision(BaseModel):
    agent: AgentName
    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    required_capabilities: list[str] = Field(default_factory=list)
    # True when this decision is a fallback the router itself produced
    # (malformed model output, timeout, disallowed agent) rather than a
    # real classification — callers use this to skip the confidence-
    # threshold clarification path (a fallback already means "we don't
    # trust this," asking the model to clarify about its own fallback
    # would be a confusing UX) while still routing safely to general_rag.
    is_fallback: bool = False

    @field_validator("required_capabilities")
    @classmethod
    def _known_capabilities_only(cls, v: list[str]) -> list[str]:
        return [c for c in v if c in _CAPABILITIES]


def _fallback(reason: str) -> RoutingDecision:
    return RoutingDecision(
        agent=AgentName.GENERAL_RAG, intent=Intent.GENERAL_CHAT, confidence=0.0, reason=reason,
        required_capabilities=["rag"], is_fallback=True,
    )


def _resolve_tier() -> ModelTier:
    try:
        return ModelTier(settings.agent_router_tier)
    except ValueError:
        return ModelTier.FAST


def route(
    query: str,
    *,
    reachable_agents: list[AgentName],
    conversation_summary: str | None = None,
    request_id: str | None = None,
    user_id: uuid.UUID | None = None,
    role: str | None = None,
    department: str | None = None,
) -> RoutingDecision:
    """`reachable_agents` — the caller (routers/chat.py) computes this from
    the real RBAC decision via policies.py::agent_allowed_for_role() BEFORE
    calling route(), so the model is never even prompted with an agent this
    user structurally cannot reach. This is belt-and-suspenders with the
    post-hoc validation below, not a substitute for it."""
    if not reachable_agents:
        return _fallback("no agents reachable for this role")

    reachable_names = ", ".join(a.value for a in reachable_agents)
    user_message = (
        f"Reachable agents for this request (choose only from these): {reachable_names}\n\n"
        + (f"Conversation context:\n{conversation_summary}\n\n" if conversation_summary else "")
        + f"User's latest message: {query}"
    )
    request = GenerateRequest(
        agent_name="supervisor_router",
        system=ROUTER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
        tier=_resolve_tier(),
        max_tokens=300,
        request_id=request_id,
        cache_system=True,
        user_id=user_id,
        role=role,
        department=department,
    )

    try:
        future = _executor.submit(claude_gateway.generate, request)
        result = future.result(timeout=settings.agent_router_timeout_seconds)
    except FutureTimeoutError:
        return _fallback(f"routing timed out after {settings.agent_router_timeout_seconds}s")
    except GenerationError as exc:
        return _fallback(f"routing gateway error: {exc}")
    except Exception as exc:  # a routing failure must never fail the actual request
        return _fallback(f"unexpected routing error: {exc}")

    if result.stop_reason == "refusal":
        return _fallback("router model refused to classify")

    text = result.text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return _fallback("malformed JSON response from router")

    try:
        decision = RoutingDecision(
            agent=AgentName(str(data.get("agent", "")).strip().lower()),
            intent=Intent(str(data.get("intent", "")).strip().upper()),
            confidence=float(data.get("confidence", 0.0)),
            reason=str(data.get("reason", ""))[:500],
            required_capabilities=list(data.get("required_capabilities") or []),
        )
    except (ValueError, TypeError):
        # Unrecognized agent/intent string, or a non-numeric confidence —
        # never let an unvalidated value escape this module.
        return _fallback("router returned an unrecognized agent or intent")

    if decision.agent not in reachable_agents:
        # Model named a real agent enum value, just not one it was allowed
        # to pick from — same fail-safe outcome as an invalid string.
        return _fallback(f"router selected {decision.agent.value!r}, which was not in the reachable list")

    return decision
