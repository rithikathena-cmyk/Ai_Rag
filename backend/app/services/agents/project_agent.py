import uuid

from sqlalchemy.orm import Session

from app.models.project import ProjectModel
from app.models.project_member import ProjectMemberModel
from app.models.user import UserModel


def list_my_projects(
    db: Session, *, user_id: uuid.UUID, scope: str = "own", limit: int = 50,
) -> list[dict]:
    """Read-only, ORM-filtered project lookup for the planner's
    `list_my_projects` tool (services/agents/planner.py). Deliberately never
    goes through query_analytics/Claude-authored SQL — see
    docs/PROJECT_GOVERNANCE.md's row-level-scoping rationale: a plain Python
    `.filter(manager_id == user_id)` can't be bypassed by a cleverly-worded
    SQL query the way teaching sql_guard to inject a WHERE clause could be.

    `scope="own"` (Project Manager): projects the caller manages or is a
    member of. `scope="all"` (CEO/Admin): every project, unfiltered — the
    caller already established at authorization time (see
    services/llm_rbac/report_policy.py::_resolve_row_filter()) that this
    role may see everything.
    """
    query = db.query(ProjectModel)
    if scope != "all":
        member_of = {
            r[0] for r in db.query(ProjectMemberModel.project_id)
            .filter(ProjectMemberModel.user_id == user_id).all()
        }
        query = query.filter(
            (ProjectModel.manager_id == user_id) | (ProjectModel.id.in_(member_of))
        )
    rows = query.order_by(ProjectModel.created_at.desc()).limit(limit).all()

    manager_ids = {r.manager_id for r in rows if r.manager_id is not None}
    manager_names = {
        r[0]: (r[1] or r[2])
        for r in db.query(UserModel.id, UserModel.display_name, UserModel.email)
        .filter(UserModel.id.in_(manager_ids)).all()
    } if manager_ids else {}

    return [
        {
            "id": str(p.id),
            "name": p.name,
            "description": p.description,
            "department": p.department,
            "manager": manager_names.get(p.manager_id),
            "priority": p.priority,
            "status": p.status,
            "created_at": p.created_at.isoformat(),
        }
        for p in rows
    ]
