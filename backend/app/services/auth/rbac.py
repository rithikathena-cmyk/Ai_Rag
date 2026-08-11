from fastapi import Depends

from app.core.errors import AppError
from app.core.permissions import Permission
from app.core.roles import Role
from app.models.user import UserModel
from app.services.auth.dependencies import get_current_user
from app.services.llm_rbac import policy_loader


def require_role(*allowed_roles: Role | str):
    """FastAPI dependency factory: 401 if unauthenticated, 403 if the
    caller's role isn't one of `allowed_roles`. Role is read fresh from the
    database on every request (via get_current_user), not from the token, so
    a role change or deactivation takes effect immediately."""

    allowed = {Role(r) if not isinstance(r, Role) else r for r in allowed_roles}

    def _check(user: UserModel = Depends(get_current_user)) -> UserModel:
        if user.role not in {r.value for r in allowed}:
            raise AppError(403, "insufficient_role", f"This action requires one of: {', '.join(sorted(allowed))}")
        return user

    return _check


def require_permission(permission: Permission | str):
    """FastAPI dependency factory for the coarse REST-resource permission
    catalog (app/core/permissions.py) — the declarative counterpart to
    require_role() above, but checking policy_loader's per-role
    `granted_permissions` (backend/config/llm_rbac.yaml's `rbac_permissions`)
    instead of role membership directly. 401 if unauthenticated, 403 if the
    caller's role wasn't granted this permission (or the "*" wildcard, same
    convention as the existing permissions_allow field)."""

    perm = Permission(permission) if not isinstance(permission, Permission) else permission

    def _check(user: UserModel = Depends(get_current_user)) -> UserModel:
        granted = policy_loader.role_config(user.role).granted_permissions
        if perm.value not in granted and "*" not in granted:
            raise AppError(403, "insufficient_permission", f"This action requires the {perm.value} permission")
        return user

    return _check
