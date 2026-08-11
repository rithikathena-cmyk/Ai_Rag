"""Pure helper for the permission-driven UI (no Streamlit dependency) — checks
a /users/me/capabilities response against the coarse REST-resource
permission catalog (backend/app/core/permissions.py), mirrored here as plain
strings since the frontend has no import path into the backend package."""

# Mirrors app/core/permissions.py::Permission — kept as plain string
# constants (not an Enum) since nothing here needs enum machinery, just
# name-checking against the backend's exact values.
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


def has_permission(capabilities: dict | None, permission: str) -> bool:
    """capabilities is a raw /users/me/capabilities response dict (or None,
    e.g. while it's still loading/failed to fetch — treated as no
    permissions, fail-closed for UI visibility). Hiding a control here is
    never the only gate — the backend's require_permission() dependency is
    the actual enforcement boundary; this just keeps the UI from showing
    controls a request would 403 on anyway."""
    if not capabilities:
        return False
    if capabilities.get("all_permissions"):
        return True
    return permission in (capabilities.get("granted_permissions") or [])
