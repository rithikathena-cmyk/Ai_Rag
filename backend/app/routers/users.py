import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.permissions import Permission
from app.core.roles import ROLE_VALUES, Role
from app.db.postgres import get_db
from app.models.user import UserModel
from app.services.auth.dependencies import get_current_user
from app.services.auth.password import hash_password
from app.services.auth.rbac import require_permission, require_role
from app.services.llm_rbac.policy_loader import departments as llm_rbac_departments, role_config
from app.services.llm_rbac.quotas import effective_quotas, get_usage, reset_usage
from app.services.memory.preferences import get_preferences, update_preferences

router = APIRouter()


class UserCreateRequest(BaseModel):
    email: str
    display_name: str | None = None
    password: str = Field(min_length=8)
    role: str | None = None
    department: str | None = None

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str | None) -> str | None:
        if v is not None and v not in ROLE_VALUES:
            raise ValueError(f"role must be one of: {', '.join(ROLE_VALUES)}")
        return v

    @field_validator("department")
    @classmethod
    def _validate_department_field(cls, v: str | None) -> str | None:
        return _validate_department(v)


def _validate_department(v: str | None) -> str | None:
    known = llm_rbac_departments()
    if v is not None and known and v not in known:
        raise ValueError(f"department must be one of: {', '.join(known)}")
    return v


class UserUpdateRequest(BaseModel):
    role: str | None = None
    department: str | None = None
    is_active: bool | None = None

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str | None) -> str | None:
        if v is not None and v not in ROLE_VALUES:
            raise ValueError(f"role must be one of: {', '.join(ROLE_VALUES)}")
        return v

    @field_validator("department")
    @classmethod
    def _validate_department_field(cls, v: str | None) -> str | None:
        return _validate_department(v)


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    is_active: bool
    role: str
    department: str | None
    created_at: datetime
    # None means "use the role default" from llm_rbac.yaml — see
    # PUT /users/{user_id}/token-limit below.
    daily_token_limit_override: int | None = None
    monthly_token_limit_override: int | None = None


def _to_response(row: UserModel) -> UserResponse:
    return UserResponse(
        id=row.id, email=row.email, display_name=row.display_name,
        is_active=row.is_active, role=row.role, department=row.department, created_at=row.created_at,
        daily_token_limit_override=row.daily_token_limit_override,
        monthly_token_limit_override=row.monthly_token_limit_override,
    )


@router.post("/users", response_model=UserResponse, status_code=201)
def create_user(
    body: UserCreateRequest, db: Session = Depends(get_db),
    _creator: UserModel = Depends(require_role(Role.ADMIN, Role.CEO)),
):
    # Account creation is Admin/CEO-only — this used to have no auth
    # dependency at all (anyone could self-register via the login page's
    # "Create account" tab). require_role, not require_permission(
    # MANAGE_USERS): MANAGE_USERS is Admin-only in llm_rbac.yaml (role/
    # active-status *editing* of existing accounts, see views/users.py),
    # and account creation is deliberately a separate, slightly broader
    # grant that also includes CEO.
    email = body.email.strip().lower()
    if db.query(UserModel).filter(UserModel.email == email).one_or_none():
        raise AppError(409, "email_already_registered", f"A user with email {email} already exists")
    row = UserModel(
        email=email, display_name=body.display_name, password_hash=hash_password(body.password),
        role=body.role or "user", department=body.department,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_response(row)


@router.get("/users/{user_id}", response_model=UserResponse)
def get_user(
    user_id: uuid.UUID, db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user)
):
    # Previously had no auth dependency at all — any unauthenticated request
    # could fetch any user's profile (email, role, department) by ID.
    # Self-lookup is always fine (matches /users/me/*); anyone else's
    # profile requires VIEW_USERS.
    if user_id != current_user.id:
        granted = role_config(current_user.role).granted_permissions
        if Permission.VIEW_USERS.value not in granted and "*" not in granted:
            raise AppError(403, "insufficient_permission", "This action requires the VIEW_USERS permission")
    row = db.get(UserModel, user_id)
    if row is None:
        raise AppError(404, "user_not_found", f"User {user_id} not found")
    return _to_response(row)


class TokenLimitUpdateRequest(BaseModel):
    # PUT semantics: the body is the complete desired override state, not a
    # partial patch — omitting a field (or sending null) means "no override,
    # use the role default" for that field, not "leave it unchanged". This
    # lets an Admin/CEO clear a previously-set override by re-PUTting
    # without it, same as removing a cap they added earlier.
    daily_tokens: int | None = Field(default=None, ge=1)
    monthly_tokens: int | None = Field(default=None, ge=1)


@router.put("/users/{user_id}/token-limit", response_model=UserResponse)
def set_user_token_limit(
    user_id: uuid.UUID, body: TokenLimitUpdateRequest, db: Session = Depends(get_db),
    _actor: UserModel = Depends(require_role(Role.ADMIN, Role.CEO)),
):
    # require_role(ADMIN, CEO), not require_permission(MANAGE_USERS): same
    # reasoning as create_user above — MANAGE_USERS is Admin-only in
    # llm_rbac.yaml, but capping an individual user's token budget is
    # deliberately a broader Admin+CEO grant, not role/active-status editing.
    row = db.get(UserModel, user_id)
    if row is None:
        raise AppError(404, "user_not_found", f"User {user_id} not found")
    row.daily_token_limit_override = body.daily_tokens
    row.monthly_token_limit_override = body.monthly_tokens
    db.commit()
    db.refresh(row)
    return _to_response(row)


class UsageResponse(BaseModel):
    role: str
    # requests_per_minute/max_concurrent_requests are config-only limit
    # values (no enforcement wired up) — neither is a day/month rollup this
    # endpoint can read back a "used so far" figure for. None means unlimited
    # (CEO/Admin).
    daily_requests_limit: int | None
    daily_requests_used: int
    daily_tokens_limit: int | None
    daily_tokens_used: int
    monthly_tokens_limit: int | None
    monthly_tokens_used: int
    monthly_cost_usd_limit: float | None
    monthly_cost_usd_used: float
    requests_per_minute_limit: int | None
    max_concurrent_requests_limit: int | None


def _usage_response(db: Session, user: UserModel) -> UsageResponse:
    quotas = effective_quotas(
        role_config(user.role).quotas,
        daily_token_limit_override=user.daily_token_limit_override,
        monthly_token_limit_override=user.monthly_token_limit_override,
    )
    usage = get_usage(db, user.id)
    return UsageResponse(
        role=user.role,
        daily_requests_limit=quotas.get("daily_requests"),
        daily_requests_used=usage["daily_requests_used"],
        daily_tokens_limit=quotas.get("daily_tokens"),
        daily_tokens_used=usage["daily_tokens_used"],
        monthly_tokens_limit=quotas.get("monthly_tokens"),
        monthly_tokens_used=usage["monthly_tokens_used"],
        monthly_cost_usd_limit=quotas.get("monthly_cost_usd"),
        monthly_cost_usd_used=usage["monthly_cost_usd_used"],
        requests_per_minute_limit=quotas.get("requests_per_minute"),
        max_concurrent_requests_limit=quotas.get("max_concurrent_requests"),
    )


@router.get("/users/me/usage", response_model=UsageResponse)
def get_my_usage(db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user)):
    return _usage_response(db, current_user)


@router.get("/users/{user_id}/usage", response_model=UsageResponse)
def get_user_usage(
    user_id: uuid.UUID, db: Session = Depends(get_db),
    _actor: UserModel = Depends(require_role(Role.ADMIN, Role.CEO)),
):
    # Same Admin+CEO grant as the token-limit/usage-reset endpoints below —
    # lets the token-limit editor UI show a target user's current usage
    # before an admin decides whether a limit change or a reset is what's
    # actually needed.
    row = db.get(UserModel, user_id)
    if row is None:
        raise AppError(404, "user_not_found", f"User {user_id} not found")
    return _usage_response(db, row)


@router.post("/users/{user_id}/usage/reset", response_model=UsageResponse)
def reset_user_usage(
    user_id: uuid.UUID, db: Session = Depends(get_db),
    _actor: UserModel = Depends(require_role(Role.ADMIN, Role.CEO)),
):
    # Same Admin+CEO grant as PUT /users/{id}/token-limit, but a deliberately
    # separate action from it — see reset_usage()'s docstring for why a
    # limit change never resets usage on its own. This is the explicit "give
    # this user a clean slate" button for when an admin actually wants that.
    row = db.get(UserModel, user_id)
    if row is None:
        raise AppError(404, "user_not_found", f"User {user_id} not found")
    reset_usage(db, user_id)
    db.commit()
    db.refresh(row)
    return _usage_response(db, row)


class CapabilitiesResponse(BaseModel):
    role: str
    display_name: str
    model_tiers_allowed: list[str]
    default_model_tier: str
    # Actions that trigger an Opus escalation for this role — empty for a
    # role structurally capped at Sonnet (see policy_loader.RoleConfig).
    escalate_to_opus_for: list[str]
    tools: list[str]
    knowledge_departments: list[str]
    # permissions.allow from llm_rbac.yaml, named-action granularity (e.g.
    # "search_manuals") — not the same list as `tools`, which is the coarser
    # Claude Gateway tool set. Empty + all_capabilities=True for CEO/Admin's
    # "*" wildcard rather than spelling out the whole catalog.
    capabilities: list[str]
    all_capabilities: bool
    # Coarse REST-resource permissions (app/core/permissions.py) — drives the
    # frontend's permission-driven nav/composer (has_permission() checks),
    # distinct from `capabilities` above (fine-grained named actions).
    granted_permissions: list[str]
    all_permissions: bool


@router.get("/users/me/capabilities", response_model=CapabilitiesResponse)
def get_my_capabilities(current_user: UserModel = Depends(get_current_user)):
    cfg = role_config(current_user.role)
    all_capabilities = "*" in cfg.permissions_allow
    all_permissions = "*" in cfg.granted_permissions
    return CapabilitiesResponse(
        role=current_user.role,
        display_name=cfg.display_name,
        model_tiers_allowed=sorted(cfg.tiers_allowed),
        default_model_tier=cfg.default_tier,
        escalate_to_opus_for=sorted(cfg.escalate_to_opus_for),
        tools=sorted(cfg.tools),
        knowledge_departments=list(cfg.knowledge_departments),
        capabilities=[] if all_capabilities else sorted(cfg.permissions_allow),
        all_capabilities=all_capabilities,
        granted_permissions=[] if all_permissions else sorted(cfg.granted_permissions),
        all_permissions=all_permissions,
    )


@router.get(
    "/users", response_model=list[UserResponse], dependencies=[Depends(require_permission(Permission.VIEW_USERS))]
)
def list_users(limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    # Was require_role(ADMIN) — now VIEW_USERS, which per the enterprise
    # permission matrix also covers HR/Project Manager/CEO, not just Admin.
    rows = db.query(UserModel).order_by(UserModel.created_at.desc()).offset(offset).limit(limit).all()
    return [_to_response(r) for r in rows]


@router.patch(
    "/users/{user_id}", response_model=UserResponse,
    dependencies=[Depends(require_permission(Permission.MANAGE_USERS))],
)
def update_user(user_id: uuid.UUID, body: UserUpdateRequest, db: Session = Depends(get_db)):
    # Was require_role(ADMIN) — now MANAGE_USERS, granted to Admin only
    # (same effective set as before, CEO does NOT get this per the matrix).
    row = db.get(UserModel, user_id)
    if row is None:
        raise AppError(404, "user_not_found", f"User {user_id} not found")
    if body.role is not None:
        row.role = body.role
    if body.department is not None:
        row.department = body.department
    if body.is_active is not None:
        row.is_active = body.is_active
    db.commit()
    db.refresh(row)
    return _to_response(row)


def _require_self(user_id: uuid.UUID, current_user: UserModel) -> None:
    # Preferences are personal settings, not an admin-manageable resource —
    # self-only, no permission escape hatch (unlike get_user above).
    # Previously these two routes had no auth dependency at all.
    if user_id != current_user.id:
        raise AppError(403, "not_self", "You may only view or edit your own preferences")


@router.get("/users/{user_id}/preferences", response_model=dict)
def get_user_preferences(
    user_id: uuid.UUID, db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user)
):
    _require_self(user_id, current_user)
    if db.get(UserModel, user_id) is None:
        raise AppError(404, "user_not_found", f"User {user_id} not found")
    return get_preferences(db, user_id)


@router.put("/users/{user_id}/preferences", response_model=dict)
def put_user_preferences(
    user_id: uuid.UUID, body: dict, db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    _require_self(user_id, current_user)
    if db.get(UserModel, user_id) is None:
        raise AppError(404, "user_not_found", f"User {user_id} not found")
    return update_preferences(db, user_id, body)
