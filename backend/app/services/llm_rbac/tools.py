"""Thin, testable wrapper around RoleConfig.tools — the boundary
planner.py::_build_tools() calls through, so "which tools can this role's
agent turn use" has one named entrypoint independent of policy_loader's
internals.
"""

from app.services.llm_rbac import policy_loader


def allowed_tools_for(role: str) -> frozenset[str]:
    return policy_loader.role_config(role).tools
