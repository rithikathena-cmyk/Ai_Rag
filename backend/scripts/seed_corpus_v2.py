"""Seeds the 19-document synthetic enterprise test corpus
(scripts/_seed_corpus_v2/) through the REAL /documents/upload pipeline —
parse -> summarize -> entity extraction -> chunk -> embed -> sparse index ->
Postgres + Qdrant. No direct DB/Qdrant writes.

Uploads as a throwaway admin-role user (bypasses the upload_documents RBAC
check regardless of department; the endpoint's `department` and
`security_classification` Form fields let one uploader tag each document
correctly). The uploader user is deleted again at the end.

Usage: python scripts/seed_corpus_v2.py   (requires the backend already
running — reads its port from this repo's own Settings, same as the app
itself, rather than a hardcoded port that silently drifts from the real dev
setup)
"""

import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, ".")

from app.core.config import settings
from app.db.postgres import new_session
from app.models.user import UserModel
from app.services.auth.password import hash_password

BASE_URL = f"http://127.0.0.1:{settings.backend_port}"
DATASET_DIR = Path(__file__).parent / "_seed_corpus_v2"

# (filename, department, security_classification)
DOCUMENTS = [
    ("mfg_sop_production_line7.md", "manufacturing", "internal"),
    ("mfg_sop_machine_shutdown.md", "manufacturing", "internal"),
    ("mfg_sop_quality_inspection.md", "manufacturing", "internal"),
    ("mfg_incident_report_line7_stoppage.md", "manufacturing", "confidential"),
    ("mfg_procedure_shift_attendance.md", "manufacturing", "internal"),
    ("hr_policy_attendance.md", "hr", "internal"),
    ("hr_benefits_guide.md", "hr", "internal"),
    ("hr_sop_recruitment.md", "hr", "confidential"),
    ("hr_policy_leave_management.md", "hr", "internal"),
    ("hr_incident_report_grievance.md", "hr", "restricted"),
    ("eng_manual_equipment_maintenance.md", "engineering", "internal"),
    ("eng_procedure_engineering_change.md", "engineering", "internal"),
    ("eng_spec_fx2200.md", "engineering", "internal"),
    ("eng_schedule_preventive_maintenance.md", "engineering", "internal"),
    ("eng_incident_report_hydraulic_leak.md", "engineering", "confidential"),
    ("exec_report_plant_performance.md", "executive", "internal"),
    ("exec_summary_quarterly_operations.md", "executive", "internal"),
    ("exec_kpi_attendance.md", "executive", "internal"),
    ("exec_strategic_manufacturing_plan.md", "executive", "restricted"),
]


def main() -> None:
    email = f"seed-corpus-v2-{uuid.uuid4().hex[:8]}@example.com"
    password = "Seed-Corpus-V2-Temp-Pass-1!"
    db = new_session()
    user = UserModel(
        email=email, display_name="Corpus V2 Seed Uploader", password_hash=hash_password(password),
        is_active=True, role="admin", department="executive",
    )
    db.add(user)
    db.commit()
    user_id = user.id
    db.close()
    print(f"Created throwaway uploader {email} (role=admin)")

    client = httpx.Client(base_url=BASE_URL, timeout=180.0)
    results = []
    try:
        login = client.post("/auth/login", json={"email": email, "password": password})
        login.raise_for_status()
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        for filename, department, classification in DOCUMENTS:
            path = DATASET_DIR / filename
            content = path.read_bytes()
            resp = client.post(
                "/documents/upload",
                files={"file": (filename, content, "text/markdown")},
                data={"department": department, "security_classification": classification},
                headers=headers,
            )
            if resp.status_code != 201:
                print(f"  FAILED  {filename:45s} ({department:14s}): {resp.status_code} {resp.text[:300]}")
                results.append((filename, department, None, 0))
                continue
            body = resp.json()
            print(
                f"  OK      {filename:45s} ({department:14s}, {classification:12s}) "
                f"-> id={body['id']} status={body['status']} chunks={body['chunk_count']}"
            )
            results.append((filename, department, body["id"], body["chunk_count"]))
            time.sleep(0.3)

        ok = sum(1 for _, _, doc_id, _ in results if doc_id is not None)
        total_chunks = sum(c for _, _, doc_id, c in results if doc_id is not None)
        print(f"\n{ok}/{len(DOCUMENTS)} documents ingested successfully, {total_chunks} total chunks.")
    finally:
        db = new_session()
        db.query(UserModel).filter(UserModel.id == user_id).delete()
        db.commit()
        db.close()
        print(f"Cleaned up throwaway uploader {email}")


if __name__ == "__main__":
    main()
