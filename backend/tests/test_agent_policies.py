"""services/agents/policies.py — the multi-agent capability policy. Pure
functions only, deliberately derived from real, already-RBAC-resolved
values (PolicyDecision.knowledge_departments/allowed_tools) rather than a
second hand-maintained role list — see that module's docstring.

Real per-role knowledge_departments (backend/config/llm_rbac.yaml), used
here as the ground truth for "every real role x every agent":
  user (Employee)  -> [manufacturing]
  hr               -> [hr]
  project_manager  -> [engineering]
  ceo              -> [manufacturing, hr, engineering, executive]
  admin            -> [manufacturing, hr, engineering, executive]
"""

import pytest

from app.services.agents.policies import (
    AGENT_TOOLS,
    ALLOWED_HANDOFFS,
    agent_allowed_for_role,
    is_handoff_allowed,
    resolve_agent_tools,
)
from app.services.agents.router import AgentName

_DOMAIN_AGENTS = (AgentName.PRODUCTION, AgentName.MAINTENANCE, AgentName.QUALITY, AgentName.INVENTORY)
_UNRESTRICTED_AGENTS = (AgentName.GENERAL_RAG, AgentName.GENERAL_CONVERSATION)

_REAL_ROLE_DEPARTMENTS = {
    "user": ("manufacturing",),
    "hr": ("hr",),
    "project_manager": ("engineering",),
    "ceo": ("manufacturing", "hr", "engineering", "executive"),
    "admin": ("manufacturing", "hr", "engineering", "executive"),
}


@pytest.mark.parametrize("role,departments", _REAL_ROLE_DEPARTMENTS.items())
def test_unrestricted_agents_always_reachable_for_every_real_role(role, departments):
    for agent in _UNRESTRICTED_AGENTS:
        assert agent_allowed_for_role(agent, departments) is True


@pytest.mark.parametrize("role,departments", _REAL_ROLE_DEPARTMENTS.items())
def test_domain_agents_reachable_only_with_a_real_manufacturing_department(role, departments):
    expected = "manufacturing" in departments
    for agent in _DOMAIN_AGENTS:
        assert agent_allowed_for_role(agent, departments) is expected, (role, agent)


@pytest.mark.parametrize("role,departments", _REAL_ROLE_DEPARTMENTS.items())
def test_hr_agent_reachable_only_with_a_real_hr_department(role, departments):
    expected = "hr" in departments
    assert agent_allowed_for_role(AgentName.HR, departments) is expected


def test_knowledge_departments_none_means_no_restriction_at_all():
    # None is the LLM-RBAC kill-switch value ("no category restriction") —
    # every agent must be reachable, not just the unrestricted ones.
    for agent in AgentName:
        assert agent_allowed_for_role(agent, None) is True


def test_empty_departments_tuple_denies_every_domain_specific_agent():
    for agent in _DOMAIN_AGENTS + (AgentName.HR,):
        assert agent_allowed_for_role(agent, ()) is False
    for agent in _UNRESTRICTED_AGENTS:
        assert agent_allowed_for_role(agent, ()) is True


def test_resolve_agent_tools_never_widens_beyond_the_real_rbac_grant():
    # user (Employee) role's real grant is {search_documents} only —
    # AGENT_TOOLS[PRODUCTION] also lists only search_documents, so this is
    # already the full intersection, but assert the shape explicitly.
    assert resolve_agent_tools(AgentName.PRODUCTION, frozenset({"search_documents"})) == frozenset({"search_documents"})

    # hr role's real grant has query_analytics/generate_report; AGENT_TOOLS
    # for hr allows those too, but a caller with a NARROWER real grant must
    # still only get the narrower set — never the agent's full allowlist.
    assert resolve_agent_tools(AgentName.HR, frozenset({"search_documents"})) == frozenset({"search_documents"})
    assert resolve_agent_tools(AgentName.HR, frozenset()) == frozenset()

    # A caller whose real RBAC grant is broader than the agent's own
    # allowlist (e.g. a ceo/admin routed to a manufacturing-domain agent)
    # still only gets what AGENT_TOOLS allows for that agent, never their
    # full personal grant.
    broad_grant = frozenset({"search_documents", "query_analytics", "generate_report", "list_my_projects"})
    assert resolve_agent_tools(AgentName.PRODUCTION, broad_grant) == frozenset({"search_documents"})
    assert resolve_agent_tools(AgentName.HR, broad_grant) == frozenset(
        {"search_documents", "query_analytics", "generate_report"}
    )


def test_general_conversation_never_gets_any_tool_regardless_of_grant():
    broad_grant = frozenset({"search_documents", "query_analytics", "generate_report", "list_my_projects"})
    assert resolve_agent_tools(AgentName.GENERAL_CONVERSATION, broad_grant) == frozenset()


def test_agent_tools_table_never_lists_a_tool_outside_the_known_catalog():
    known_tools = {"search_documents", "query_analytics", "generate_report", "list_my_projects"}
    for agent, tools in AGENT_TOOLS.items():
        assert tools <= known_tools, agent


def test_handoff_graph_is_closed_and_matches_the_documented_design():
    assert is_handoff_allowed(AgentName.PRODUCTION, AgentName.MAINTENANCE) is True
    assert is_handoff_allowed(AgentName.PRODUCTION, AgentName.QUALITY) is True
    assert is_handoff_allowed(AgentName.MAINTENANCE, AgentName.PRODUCTION) is True
    assert is_handoff_allowed(AgentName.QUALITY, AgentName.PRODUCTION) is True


def test_handoff_not_defined_is_denied():
    assert is_handoff_allowed(AgentName.PRODUCTION, AgentName.HR) is False
    assert is_handoff_allowed(AgentName.HR, AgentName.PRODUCTION) is False
    assert is_handoff_allowed(AgentName.GENERAL_RAG, AgentName.PRODUCTION) is False
    assert is_handoff_allowed(AgentName.INVENTORY, AgentName.PRODUCTION) is False


def test_no_handoff_ever_targets_an_unrestricted_or_hr_agent():
    # ALLOWED_HANDOFFS is scoped to the manufacturing-domain agents only —
    # confirms no accidental entry sends production/maintenance/quality
    # traffic into hr/general_* via a handoff.
    for targets in ALLOWED_HANDOFFS.values():
        assert not targets & set(_UNRESTRICTED_AGENTS)
        assert AgentName.HR not in targets
