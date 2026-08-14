from enum import StrEnum


class Permission(StrEnum):
    """Coarse, REST-resource-shaped permission catalog for UI/endpoint gating —
    distinct from services/llm_rbac's free-text capability strings
    (upload_documents, hr_report_generation, ...), which gate fine-grained
    *actions* inside /chat and /search. This catalog answers "can this role
    reach this feature at all" (nav visibility + endpoint 403s); the
    llm_rbac.yaml capability strings still answer "which named action can
    this role ask the agent to perform." Granted per role via
    llm_rbac.yaml's `rbac_permissions` key, resolved through
    services/llm_rbac/policy_loader.py::RoleConfig.granted_permissions —
    same config file, same loader, no second RBAC system.
    """

    CHAT = "CHAT"
    VIEW_CONVERSATIONS = "VIEW_CONVERSATIONS"
    VIEW_DOCUMENTS = "VIEW_DOCUMENTS"
    UPLOAD_DOCUMENTS = "UPLOAD_DOCUMENTS"
    DELETE_DOCUMENTS = "DELETE_DOCUMENTS"
    MANAGE_DOCUMENTS = "MANAGE_DOCUMENTS"
    VIEW_ANALYTICS = "VIEW_ANALYTICS"
    VIEW_USERS = "VIEW_USERS"
    MANAGE_USERS = "MANAGE_USERS"
    VIEW_ROLES = "VIEW_ROLES"
    MANAGE_ROLES = "MANAGE_ROLES"
    VIEW_AUDIT_LOGS = "VIEW_AUDIT_LOGS"
    SYSTEM_SETTINGS = "SYSTEM_SETTINGS"
    # Gates whether a role can even *create* an employee-PII approval request
    # at all (routers/chat.py's pre-flight branch) — a role without this
    # permission never reaches the new capability, and sees today's ordinary
    # input-PII block instead. See docs/GUARDRAILS_ARCHITECTURE.md §14.
    MANAGE_EMPLOYEE_PII = "MANAGE_EMPLOYEE_PII"


PERMISSION_VALUES: tuple[str, ...] = tuple(p.value for p in Permission)
