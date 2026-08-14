"""Seeds a second, 8-document batch (scripts/_seed_corpus_v3/) through the
REAL /documents/upload pipeline — parse -> summarize -> entity extraction ->
chunk -> embed -> sparse index -> Postgres + Qdrant. No direct DB/Qdrant
writes. Same pattern as scripts/seed_corpus_v2.py; a separate script (not
appended to that one's DOCUMENTS list) so re-running v2 never re-uploads
these, and vice versa.

Fills two real gaps found live during this session's guardrail testing:
- EHS-SAFE-005 (Lockout/Tagout) was referenced by three v2 documents
  (SOP-MFG-104, SOP-MFG-101, hr_policy_code_of_conduct.md here) but never
  actually existed as its own document — "List the safety procedures"
  scored 0.487 against scope_semantic_check's configured topics, just below
  the 0.55 threshold, precisely because no document was specifically about
  general safety procedures.
- No financial/budget document existed anywhere in the corpus even though
  llm_rbac.yaml's `financial_information` permission (CEO-only) has existed
  since the RBAC pass — GEN-EXEC-FIN-2026 is the first document that
  permission actually has real content behind it.

Uploads as a throwaway admin-role user, same as seed_corpus_v2.py, for the
same reason (bypasses the upload_documents RBAC check regardless of
department; the uploader is deleted again at the end).

Usage: python scripts/seed_corpus_v3.py   (requires the backend already
running — reads its port from this repo's own Settings)
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
DATASET_DIR = Path(__file__).parent / "_seed_corpus_v3"

# (filename, department, security_classification)
DOCUMENTS = [
    ("mfg_sop_lockout_tagout.md", "manufacturing", "internal"),
    ("mfg_manual_ppe_requirements.md", "manufacturing", "internal"),
    ("hr_policy_onboarding.md", "hr", "internal"),
    ("hr_policy_code_of_conduct.md", "hr", "internal"),
    ("eng_spec_conveyor_system.md", "engineering", "internal"),
    ("eng_incident_report_conveyor_jam.md", "engineering", "confidential"),
    ("exec_report_annual_budget.md", "executive", "restricted"),
    ("exec_report_customer_satisfaction.md", "executive", "internal"),
]


def main() -> None:
    email = f"seed-corpus-v3-{uuid.uuid4().hex[:8]}@example.com"
    password = "Seed-Corpus-V3-Temp-Pass-1!"
    db = new_session()
    user = UserModel(
        email=email, display_name="Corpus V3 Seed Uploader", password_hash=hash_password(password),
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
