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
    # Gates the Guardrail Policy Center (routers/guardrail_policies.py) —
    # deliberately its own permission, not a reuse of SYSTEM_SETTINGS (which
    # llm_rbac.yaml grants to Admin only): the spec for this feature requires
    # BOTH Admin and CEO to manage guardrail policy, and SYSTEM_SETTINGS
    # already has an established, narrower meaning (Qdrant/model-availability
    # config in routers/admin.py) that CEO is intentionally excluded from.
    # Same "new named permission for a new capability" precedent as
    # MANAGE_EMPLOYEE_PII above.
    MANAGE_GUARDRAIL_POLICIES = "MANAGE_GUARDRAIL_POLICIES"
    # Policy Copilot — deliberately split rather than reusing
    # MANAGE_GUARDRAIL_POLICIES for everything. Proposing a change and
    # approving one are different authorities: a deployment that wants
    # four-eyes review needs to grant POLICY_PROPOSE without
    # POLICY_APPROVE, which a single combined permission cannot express.
    # POLICY_READ/POLICY_SIMULATE are deliberately the weakest — inspecting
    # and dry-running policy changes nothing, and gating them as tightly as
    # mutation would just push admins toward editing live policy to find
    # out what it does.
    POLICY_READ = "POLICY_READ"
    POLICY_SIMULATE = "POLICY_SIMULATE"
    POLICY_PROPOSE = "POLICY_PROPOSE"
    POLICY_APPROVE = "POLICY_APPROVE"
    # Reveals the ORIGINAL, pre-redaction value of a detected PII entity via
    # the dedicated GET /admin/traces/{message_id}/pii/{entity_id} endpoint
    # (routers/pii_access.py) — a materially different authority from
    # VIEW_AUDIT_LOGS (seeing THAT PII was detected, and its already-redacted
    # form, in a trace) or MANAGE_GUARDRAIL_POLICIES (configuring how PII is
    # handled). Deliberately its own permission, not folded into either:
    # granting "read the audit trail" must never implicitly grant "unmask
    # what it redacted". Not in any role's rbac_permissions list by default
    # except admin — see llm_rbac.yaml's admin section for why, and for how
    # an operator grants it to another role explicitly.
    PII_VIEW_RAW = "PII_VIEW_RAW"


PERMISSION_VALUES: tuple[str, ...] = tuple(p.value for p in Permission)
