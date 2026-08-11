"""One-off seeding script: uploads a small synthetic multi-department
document set through the REAL /documents/upload pipeline (parse -> summarize
-> entity extraction -> chunk -> embed -> sparse index -> Postgres + Qdrant),
so the resulting documents are indistinguishable from anything uploaded
through the UI — no direct DB/Qdrant writes.

Uploads as a throwaway admin-role user (admin's permissions.allow: ["*"]
bypasses the upload_documents RBAC check regardless of department, and the
endpoint's `department` Form field lets a single uploader tag each document
to the department it should be filtered under) so each document lands in
the right knowledge_departments bucket without needing four separate
per-department logins. The uploader user is deleted again at the end.

Run against a live backend (this script talks HTTP, not the app object
directly, since /documents/upload is `async def` and does real background
work FastAPI's TestClient handles fine, but a live server is simpler to
reason about for a one-off seed run). Usage:

    python scripts/seed_department_dataset.py
"""

import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, ".")

from app.db.postgres import new_session
from app.models.user import UserModel
from app.services.auth.password import hash_password

BASE_URL = "http://127.0.0.1:8000"
DATASET_DIR = Path(__file__).parent / "_seed_dataset"

# (filename, department)
DOCUMENTS = [
    ("manufacturing_sop_injection_molding.md", "manufacturing"),
    ("manufacturing_wi_quality_inspection.md", "manufacturing"),
    ("hr_policy_leave_and_attendance.md", "hr"),
    ("hr_policy_safety_and_harassment.md", "hr"),
    ("engineering_risk_assessment_conveyor.md", "engineering"),
    ("engineering_report_cnc_predictive_maintenance.md", "engineering"),
    ("executive_q3_performance_summary.md", "executive"),
    ("executive_enterprise_risk_overview.md", "executive"),
]


def main() -> None:
    email = f"seed-uploader-{uuid.uuid4().hex[:8]}@example.com"
    password = "Seed-Uploader-Temp-Pass-1!"
    db = new_session()
    user = UserModel(
        email=email, display_name="Dataset Seed Uploader", password_hash=hash_password(password),
        is_active=True, role="admin", department="executive",
    )
    db.add(user)
    db.commit()
    user_id = user.id
    db.close()
    print(f"Created throwaway uploader {email} (role=admin, so it can tag any department)")

    client = httpx.Client(base_url=BASE_URL, timeout=120.0)
    try:
        login = client.post("/auth/login", json={"email": email, "password": password})
        login.raise_for_status()
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        results = []
        for filename, department in DOCUMENTS:
            path = DATASET_DIR / filename
            content = path.read_bytes()
            resp = client.post(
                "/documents/upload",
                files={"file": (filename, content, "text/markdown")},
                data={"department": department},
                headers=headers,
            )
            if resp.status_code != 201:
                print(f"  FAILED  {filename} ({department}): {resp.status_code} {resp.text[:300]}")
                results.append((filename, department, None))
                continue
            body = resp.json()
            print(
                f"  OK      {filename} ({department}) -> id={body['id']} "
                f"status={body['status']} chunks={body['chunk_count']}"
            )
            results.append((filename, department, body["id"]))
            time.sleep(0.5)  # be gentle on the local embed/reranker warm state

        ok = sum(1 for _, _, doc_id in results if doc_id is not None)
        print(f"\n{ok}/{len(DOCUMENTS)} documents ingested successfully.")
    finally:
        db = new_session()
        db.query(UserModel).filter(UserModel.id == user_id).delete()
        db.commit()
        db.close()
        print(f"Cleaned up throwaway uploader {email}")


if __name__ == "__main__":
    main()
