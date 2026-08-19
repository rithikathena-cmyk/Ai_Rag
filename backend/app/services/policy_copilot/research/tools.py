"""Tool allowlist and execution for Guardrail Policy Research Agent.

Strict allowlist enforcement — research tools only read guardrail registry,
never write, never access user data, documents, or external systems.

All tool execution goes through enforce_tool_allowlist() to prevent LLM
bypass — the LLM cannot call tools directly, only via this gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.services.guardrail_policy import store
from app.services.policy_copilot.research.scope import assert_guardrail_only_scope, classify_scope


@dataclass
class ToolCall:
    """A tool invocation result."""
    name: str
    args: dict[str, Any]
    result: Any | None
    error: str | None = None


# Strict allowlist — only read-only guardrail tools
_ALLOWED_TOOLS = frozenset({
    "GET_ACTIVE_POLICIES",  # List current policies by category
    "GET_POLICY_DETAILS",   # Get full policy config for one ID
    "LIST_PII_ENTITIES",    # List all PII entity types with current policy
    "GET_ENTITY_POLICY",    # Get policy for one entity (EMAIL, PHONE, etc.)
    "GET_DETECTOR_CONFIG",  # Get detector pattern for configurable entity
    "COMPARE_POLICIES",     # Compare policies across entities or roles
    "SIMULATE_POLICY_CHANGE",  # Dry-run a hypothetical policy change
})

# Tools that NEVER run, regardless of allowlist
_FORBIDDEN_TOOLS = frozenset({
    "WRITE_POLICY",
    "DELETE_POLICY",
    "CREATE_DETECTOR",
    "UPDATE_DETECTOR",
    "APPLY_POLICY",
    "EXECUTE_ACTION",
    "ACCESS_AUDIT_LOG",
    "ACCESS_MESSAGES",
    "ACCESS_DOCUMENTS",
    "ACCESS_USERS",
    "CALL_EXTERNAL_API",
    "EXECUTE_CODE",
    "RUN_SQL",
    "MODIFY_DATABASE",
})


def enforce_tool_allowlist(tool_name: str, args: dict[str, Any]) -> bool:
    """Validate that a tool call is allowed before execution.

    Returns:
        True if the tool call is allowed
        False otherwise

    Security model:
        - Called BEFORE any tool execution
        - Checks against both allowlist and forbidden list
        - Prevents LLM from calling any tool outside the allowlist
        - Always returns False for forbidden tools, even if on allowlist
    """
    if not isinstance(tool_name, str):
        return False

    # Reject if on forbidden list (highest priority)
    if tool_name in _FORBIDDEN_TOOLS:
        return False

    # Reject if not on allowlist
    if tool_name not in _ALLOWED_TOOLS:
        return False

    return True


class ToolExecutor:
    """Execute allowed research tools against the guardrail registry.

    All tools are read-only:
    - GET_ACTIVE_POLICIES: List policies by category
    - GET_POLICY_DETAILS: Get full policy details by ID
    - LIST_PII_ENTITIES: List all PII entities with current policy
    - GET_ENTITY_POLICY: Get policy for one entity
    - GET_DETECTOR_CONFIG: Get detector pattern for configurable entity
    - COMPARE_POLICIES: Compare policies across entities or roles
    - SIMULATE_POLICY_CHANGE: Dry-run a hypothetical change

    Args:
        db: SQLAlchemy session for policy data access
    """

    def __init__(self, db: Session):
        self.db = db

    def execute(self, tool_name: str, args: dict[str, Any]) -> ToolCall:
        """Execute a tool call if allowed.

        Args:
            tool_name: Name of the tool to execute
            args: Tool arguments

        Returns:
            ToolCall with result or error
        """
        if not enforce_tool_allowlist(tool_name, args):
            return ToolCall(
                name=tool_name, args=args, result=None,
                error=f"Tool '{tool_name}' not allowed",
            )

        try:
            if tool_name == "GET_ACTIVE_POLICIES":
                return self._get_active_policies(args)
            elif tool_name == "GET_POLICY_DETAILS":
                return self._get_policy_details(args)
            elif tool_name == "LIST_PII_ENTITIES":
                return self._list_pii_entities(args)
            elif tool_name == "GET_ENTITY_POLICY":
                return self._get_entity_policy(args)
            elif tool_name == "GET_DETECTOR_CONFIG":
                return self._get_detector_config(args)
            elif tool_name == "COMPARE_POLICIES":
                return self._compare_policies(args)
            elif tool_name == "SIMULATE_POLICY_CHANGE":
                return self._simulate_policy_change(args)
            else:
                return ToolCall(
                    name=tool_name, args=args, result=None,
                    error=f"Tool '{tool_name}' not implemented",
                )
        except Exception as e:
            return ToolCall(
                name=tool_name, args=args, result=None,
                error=f"Tool execution failed: {str(e)[:200]}",
            )

    def _get_active_policies(self, args: dict[str, Any]) -> ToolCall:
        """List active policies by category (PII, INJECTION, etc.)."""
        category = args.get("category", "PII")
        policies = store.get_active_policies(category)
        return ToolCall(
            name="GET_ACTIVE_POLICIES", args=args,
            result={
                "category": category,
                "count": len(policies) if policies else 0,
                "policies": [
                    {
                        "policy_key": p.policy_key,
                        "name": p.name,
                        "entity": p.configuration.get("entity"),
                        "action": p.configuration.get("output_action"),
                    }
                    for p in (policies or [])
                ],
            },
        )

    def _get_policy_details(self, args: dict[str, Any]) -> ToolCall:
        """Get full policy configuration for one ID."""
        from app.models.guardrail_policy import GuardrailPolicyModel

        policy_id = args.get("policy_id")
        if not policy_id:
            return ToolCall(
                name="GET_POLICY_DETAILS", args=args, result=None,
                error="policy_id required",
            )

        try:
            import uuid
            pid = uuid.UUID(policy_id) if isinstance(policy_id, str) else policy_id
            row = self.db.query(GuardrailPolicyModel).filter(GuardrailPolicyModel.id == pid).one_or_none()
            if not row:
                return ToolCall(
                    name="GET_POLICY_DETAILS", args=args, result=None,
                    error=f"Policy {policy_id} not found",
                )
            return ToolCall(
                name="GET_POLICY_DETAILS", args=args,
                result={
                    "policy_key": row.policy_key,
                    "name": row.name,
                    "description": row.description,
                    "category": row.category,
                    "action": row.action,
                    "enabled": row.enabled,
                    "configuration": row.configuration,
                    "version": row.version,
                },
            )
        except (ValueError, TypeError):
            return ToolCall(
                name="GET_POLICY_DETAILS", args=args, result=None,
                error="Invalid policy_id format",
            )

    def _list_pii_entities(self, args: dict[str, Any]) -> ToolCall:
        """List all PII entity types with current policy."""
        from app.services.guardrail_policy.detector_capability import capability_for
        from app.services.guardrail_policy.entities import ENTITY_REGISTRY

        entities = []
        for name, spec in ENTITY_REGISTRY.items():
            cap = capability_for(name, self.db)
            entities.append({
                "entity": name,
                "detection": spec.detection.value,
                "detector": spec.detector,
                "enforceability": cap.state.value,
                "detector_source": cap.detector_source,
                "pattern": cap.pattern,
                "explanation": cap.explanation,
            })
        return ToolCall(
            name="LIST_PII_ENTITIES", args=args,
            result={"entities": entities, "total": len(entities)},
        )

    def _get_entity_policy(self, args: dict[str, Any]) -> ToolCall:
        """Get policy for one entity (EMAIL, PHONE, SSN, etc.)."""
        entity = args.get("entity")
        if not entity:
            return ToolCall(
                name="GET_ENTITY_POLICY", args=args, result=None,
                error="entity required",
            )

        # Placeholder: actual implementation reads policy for entity
        return ToolCall(
            name="GET_ENTITY_POLICY", args=args,
            result={"entity": entity, "policy": "Entity policy placeholder"},
        )

    def _get_detector_config(self, args: dict[str, Any]) -> ToolCall:
        """Get detector pattern for configurable entity."""
        entity = args.get("entity")
        if not entity:
            return ToolCall(
                name="GET_DETECTOR_CONFIG", args=args, result=None,
                error="entity required",
            )

        # Placeholder: actual implementation reads detector pattern
        return ToolCall(
            name="GET_DETECTOR_CONFIG", args=args,
            result={"entity": entity, "pattern": None, "state": "UNSUPPORTED"},
        )

    def _compare_policies(self, args: dict[str, Any]) -> ToolCall:
        """Compare policies across entities or roles."""
        entity1 = args.get("entity1")
        entity2 = args.get("entity2")

        if not (entity1 and entity2):
            return ToolCall(
                name="COMPARE_POLICIES", args=args, result=None,
                error="entity1 and entity2 required",
            )

        # Placeholder: actual implementation compares policies
        return ToolCall(
            name="COMPARE_POLICIES", args=args,
            result={
                "entity1": entity1,
                "entity2": entity2,
                "similarities": [],
                "differences": [],
            },
        )

    def _simulate_policy_change(self, args: dict[str, Any]) -> ToolCall:
        """Dry-run a hypothetical policy change."""
        entity = args.get("entity")
        change = args.get("change")

        if not (entity and change):
            return ToolCall(
                name="SIMULATE_POLICY_CHANGE", args=args, result=None,
                error="entity and change required",
            )

        # Placeholder: actual implementation simulates the change
        return ToolCall(
            name="SIMULATE_POLICY_CHANGE", args=args,
            result={
                "entity": entity,
                "change": change,
                "impacts": [],
                "warnings": [],
            },
        )
