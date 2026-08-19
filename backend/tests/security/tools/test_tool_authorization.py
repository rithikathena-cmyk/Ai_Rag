"""Tool authorization: no tool may be reachable before authorization.

Exercises the REAL resolver `services/agents/policies.py::resolve_agent_tools`,
which narrows an agent's toolset to what the caller's role may actually call.
The security property is that routing is not authorization: being routed to
the SQL agent must not confer SQL access.
"""

import pytest

from app.services.agents.policies import agent_allowed_for_role, is_handoff_allowed, resolve_agent_tools
from app.services.agents.router import AgentName

SEARCH = "search_documents"
ANALYTICS = "query_analytics"
REPORT = "generate_report"
PROJECTS = "list_my_projects"


def test_tools_are_intersected_with_the_role_grant():
    """The resolver must return only tools present in BOTH the agent's set and
    the role's grant — never the union, never the agent's set alone."""
    allowed = frozenset({SEARCH})
    for agent in AgentName:
        resolved = resolve_agent_tools(agent, allowed)
        assert resolved <= allowed, (
            f"{agent} resolved {resolved - allowed} which the role was never granted"
        )


def test_routing_to_an_agent_does_not_confer_its_tools():
    """The core property: a role with only document search, routed to the SQL
    agent, must not come away with analytics access."""
    search_only = frozenset({SEARCH})
    for agent in AgentName:
        assert ANALYTICS not in resolve_agent_tools(agent, search_only)
        assert REPORT not in resolve_agent_tools(agent, search_only)


def test_empty_grant_yields_no_tools():
    for agent in AgentName:
        assert resolve_agent_tools(agent, frozenset()) == frozenset(), (
            f"{agent} produced tools for a role granted none"
        )


def test_unknown_tool_in_the_grant_is_not_invented():
    """A bogus grant entry must not materialise as a callable tool."""
    for agent in AgentName:
        resolved = resolve_agent_tools(agent, frozenset({"definitely_not_a_real_tool"}))
        assert "definitely_not_a_real_tool" not in resolved or resolved == frozenset()


@pytest.mark.parametrize("departments", [None, (), ("manufacturing",), ("hr",), ("engineering",)])
def test_agent_gating_is_deterministic_for_a_department_set(departments):
    """Same inputs, same answer — an authorization decision that varies run to
    run cannot be audited."""
    first = {a: agent_allowed_for_role(a, departments) for a in AgentName}
    second = {a: agent_allowed_for_role(a, departments) for a in AgentName}
    assert first == second


def test_handoff_is_explicitly_allowlisted():
    """Agent-to-agent handoff must be an allowlist decision, not open by
    default — otherwise an agent can reach tools its own role gate excluded."""
    results = {(a, b): is_handoff_allowed(a, b) for a in AgentName for b in AgentName}
    assert any(v is False for v in results.values()), (
        "every agent handoff is permitted; handoff is not gated at all"
    )


def test_none_departments_is_documented_as_unrestricted():
    """`knowledge_departments is None` means "no category restriction" — the
    LLM-RBAC kill switch — and unlocks every agent by design. Pinned here
    because it is a FAIL-OPEN shape: it is correct only while None can never
    arise accidentally. If a caller's departments ever become None through a
    lookup miss rather than a deliberate kill-switch, every agent opens.
    Callers must pass an empty tuple, not None, to mean "no access"."""
    assert all(agent_allowed_for_role(a, None) for a in AgentName)


def test_empty_department_tuple_restricts_to_unrestricted_agents_only():
    """The safe way to express "this caller has no corpus": an empty tuple
    leaves only the department-agnostic agents reachable."""
    reachable = [a for a in AgentName if agent_allowed_for_role(a, ())]
    assert len(reachable) < len(list(AgentName)), (
        "an empty department tuple unlocked every agent; it must not behave like None"
    )


@pytest.mark.parametrize("departments,forbidden", [
    (("manufacturing",), "hr"),
    (("hr",), "production"),
])
def test_a_real_department_set_excludes_other_departments_agents(departments, forbidden):
    """Corpus scoping must actually exclude: an HR caller cannot reach the
    production agent and vice versa."""
    target = next((a for a in AgentName if a.value == forbidden), None)
    if target is None:
        pytest.skip(f"no agent named {forbidden}")
    assert agent_allowed_for_role(target, departments) is False
