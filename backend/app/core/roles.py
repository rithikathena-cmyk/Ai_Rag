from enum import StrEnum


class Role(StrEnum):
    """User roles. `ADMIN` and `USER` are the original generic roles (kept as-is so
    existing data/API contracts don't break) and double as the LLM-RBAC "Admin"
    and "Employee" roles respectively. `HR`, `PROJECT_MANAGER`, and `CEO` are the
    other LLM-RBAC org roles (see `backend/config/llm_rbac.yaml`). `CEO` was split
    out from `ADMIN` (previously "CEO/Admin" was one combined role) so the
    permission matrix can give CEO executive/approval powers without System
    Settings or Manage Roles — see docs on the enterprise permission model.
    The remaining values are manufacturing-specific roles from the ATHENA MES AI
    spec, added for RBAC-gated domain tools in a later increment — they are
    independent of LLM RBAC.
    """

    ADMIN = "admin"
    USER = "user"
    HR = "hr"
    PROJECT_MANAGER = "project_manager"
    CEO = "ceo"
    PLANT_MANAGER = "plant_manager"
    PRODUCTION_MANAGER = "production_manager"
    PRODUCTION_SUPERVISOR = "production_supervisor"
    OPERATOR = "operator"
    MAINTENANCE_ENGINEER = "maintenance_engineer"
    MAINTENANCE_MANAGER = "maintenance_manager"
    QUALITY_ENGINEER = "quality_engineer"
    WAREHOUSE_STAFF = "warehouse_staff"
    INVENTORY_MANAGER = "inventory_manager"
    PROCUREMENT_OFFICER = "procurement_officer"
    PLANNER = "planner"


ROLE_VALUES: tuple[str, ...] = tuple(r.value for r in Role)
