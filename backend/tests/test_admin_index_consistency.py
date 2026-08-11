"""routers/admin.py::GET /admin/index-consistency — the operator-facing
surface for services/ingestion/consistency.py, added after tracing a real
bug (WM_1.pdf: Postgres said "completed", Qdrant had zero points, and
nothing anywhere would have surfaced that mismatch without this endpoint).
Monkeypatches check_all_documents() (the only I/O boundary this route
touches) rather than requiring live Postgres/Qdrant.
"""

import uuid
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db.postgres import get_db
from app.routers import admin
from app.services.auth.dependencies import get_current_user
from app.services.ingestion.consistency import ConsistencyReport


def _make_app(reports):
    app = FastAPI()
    app.include_router(admin.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid.uuid4(), role="admin", is_active=True)

    def _fake_get_db():
        yield None

    app.dependency_overrides[get_db] = _fake_get_db
    admin.check_all_documents = lambda db: reports
    return TestClient(app)


def test_reports_only_inconsistent_documents():
    consistent_id, inconsistent_id = uuid.uuid4(), uuid.uuid4()
    reports = [
        ConsistencyReport(document_id=consistent_id, filename="a.txt", postgres_chunk_count=2, qdrant_point_count=2),
        ConsistencyReport(document_id=inconsistent_id, filename="WM_1.pdf", postgres_chunk_count=8, qdrant_point_count=0),
    ]
    client = _make_app(reports)

    response = client.get("/admin/index-consistency")

    assert response.status_code == 200
    body = response.json()
    assert body["checked"] == 2
    assert len(body["inconsistent"]) == 1
    assert body["inconsistent"][0]["filename"] == "WM_1.pdf"
    assert body["inconsistent"][0]["postgres_chunk_count"] == 8
    assert body["inconsistent"][0]["qdrant_point_count"] == 0


def test_all_consistent_reports_empty_list():
    doc_id = uuid.uuid4()
    reports = [ConsistencyReport(document_id=doc_id, filename="a.txt", postgres_chunk_count=1, qdrant_point_count=1)]
    client = _make_app(reports)

    response = client.get("/admin/index-consistency")

    assert response.json() == {"checked": 1, "inconsistent": []}


def test_non_admin_role_is_forbidden():
    app = FastAPI()
    app.include_router(admin.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid.uuid4(), role="user", is_active=True)

    def _fake_get_db():
        yield None

    app.dependency_overrides[get_db] = _fake_get_db
    admin.check_all_documents = lambda db: []
    client = TestClient(app)

    response = client.get("/admin/index-consistency")

    assert response.status_code == 403
