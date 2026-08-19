"""Multi-agent capability policy — which specialized agent a role can reach,
and which tools/departments each agent is allowed. Deliberately NOT a second
authorization system: every check here is derived from the real, already-
resolved RBAC decision (services/llm_rbac/engine.py::PolicyDecision) rather
than a hand-maintained parallel role list. See the approved plan's Context
section for why production/maintenance/quality/inventory share the same real
"manufacturing" department scope instead of each getting a fabricated,
narrower one that doesn't correspond to any real document tagging.
"""

from app.services.agents.router import AgentName

# No department restriction at all for these two — general_rag inherits
# whatever the caller's own knowledge_departments already allow (no
# widening, no narrowing beyond RBAC); general_conversation does no
# retrieval at all so a department concept doesn't apply.
_UNRESTRICTED_AGENTS = frozenset({AgentName.GENERAL_RAG, AgentName.GENERAL_CONVERSATION})

AGENT_DEPARTMENTS: dict[AgentName, tuple[str, ...]] = {
    AgentName.PRODUCTION: ("manufacturing",),
    AgentName.MAINTENANCE: ("manufacturing",),
    AgentName.QUALITY: ("manufacturing",),
    AgentName.INVENTORY: ("manufacturing",),
    AgentName.HR: ("hr",),
}

# Every value here is intersected with the caller's REAL RBAC-granted
# allowed_tools by resolve_agent_tools() below — this table can only ever
# narrow what a role already has, never grant something new. hr's set
# matches the hr role's real llm_rbac.yaml tool grant exactly (not a guess);
# the manufacturing-domain agents match the user (Employee) role's real
# grant (search_documents only) — a ceo/admin routed to one of them still
# only gets what this table allows for that agent, even though their own
# RBAC grant is broader, because the agent itself has no legitimate use for
# query_analytics/generate_report/list_my_projects.
AGENT_TOOLS: dict[AgentName, frozenset[str]] = {
    AgentName.PRODUCTION: frozenset({"search_documents"}),
    AgentName.MAINTENANCE: frozenset({"search_documents"}),
    AgentName.QUALITY: frozenset({"search_documents"}),
    AgentName.INVENTORY: frozenset({"search_documents"}),
    AgentName.HR: frozenset({"search_documents", "query_analytics", "generate_report"}),
    AgentName.GENERAL_RAG: frozenset({"search_documents"}),
    AgentName.GENERAL_CONVERSATION: frozenset(),
}

# Explicit, closed handoff graph (spec's own example, adapted to the real
# agent set — no hr/general_* handoffs since nothing here proposes them).
# See router.py's module docstring on why this is policy-only this pass:
# defined and authorization-checked, not yet triggered by live agent
# reasoning mid-run.
ALLOWED_HANDOFFS: dict[AgentName, frozenset[AgentName]] = {
    AgentName.PRODUCTION: frozenset({AgentName.MAINTENANCE, AgentName.QUALITY}),
    AgentName.MAINTENANCE: frozenset({AgentName.PRODUCTION, AgentName.QUALITY}),
    AgentName.QUALITY: frozenset({AgentName.PRODUCTION}),
}


def agent_allowed_for_role(agent: AgentName, knowledge_departments: tuple[str, ...] | None) -> bool:
    """True iff this agent is reachable given the caller's REAL, already
    RBAC-resolved knowledge_departments (PolicyDecision.knowledge_departments
    — None means "no category restriction," e.g. the LLM-RBAC kill switch).
    Agents with no department restriction (general_rag/general_conversation)
    are always reachable; every other agent requires a real overlap with
    what this caller can actually retrieve."""
    if agent in _UNRESTRICTED_AGENTS:
        return True
    if knowledge_departments is None:
        return True
    required = AGENT_DEPARTMENTS.get(agent, ())
    return bool(set(required) & set(knowledge_departments))


def resolve_agent_tools(agent: AgentName, allowed_tools: frozenset[str]) -> frozenset[str]:
    """The actual tool-execution security boundary (spec §8): intersects the
    agent's own tool allowlist with the caller's real RBAC-granted
    allowed_tools. Never a union — an agent can only end up with a subset of
    what the role already has, regardless of what AGENT_TOOLS lists for it."""
    return AGENT_TOOLS.get(agent, frozenset()) & allowed_tools


def is_handoff_allowed(from_agent: AgentName, to_agent: AgentName) -> bool:
    return to_agent in ALLOWED_HANDOFFS.get(from_agent, frozenset())
