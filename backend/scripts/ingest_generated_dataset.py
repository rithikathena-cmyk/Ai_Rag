"""Ingests scripts/_generated_dataset/ (produced by generate_static_dataset.py)
through the REAL /documents/upload pipeline — parse -> summarize -> entity
extraction -> chunk -> embed -> sparse index -> Postgres + Qdrant. No direct
DB/Qdrant writes.

Mirrors seed_corpus_v2.py / seed_department_dataset.py's pattern exactly
(throwaway admin uploader, deleted at the end) but discovers files by walking
the department subdirectories instead of a hardcoded (filename, department)
list, and reads `security_classification` from each file's own YAML
frontmatter rather than a separate table — so it stays correct automatically
if generate_static_dataset.py's DOCUMENTS list is ever extended.

Usage: python scripts/ingest_generated_dataset.py   (requires the backend
already running — reads its port from this repo's own Settings, same as the
app itself, rather than a hardcoded port that silently drifts from the real
dev setup)
"""

import re
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
DATASET_DIR = Path(__file__).parent / "_generated_dataset"

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _read_classification(path: Path) -> str | None:
    match = FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
    if not match:
        return None
    for line in match.group(1).splitlines():
        if line.startswith("security_classification:"):
            return line.split(":", 1)[1].strip()
    return None


def _discover_documents() -> list[tuple[Path, str, str]]:
    """Returns (path, department, classification) for every *.md file found
    directly under a department subdirectory of DATASET_DIR."""
    docs = []
    for dept_dir in sorted(p for p in DATASET_DIR.iterdir() if p.is_dir()):
        for path in sorted(dept_dir.glob("*.md")):
            classification = _read_classification(path) or "internal"
            docs.append((path, dept_dir.name, classification))
    return docs


def main() -> None:
    documents = _discover_documents()
    if not documents:
        print(f"No documents found under {DATASET_DIR} — run generate_static_dataset.py first.")
        return

    email = f"seed-generated-{uuid.uuid4().hex[:8]}@example.com"
    password = "Seed-Generated-Temp-Pass-1!"
    db = new_session()
    user = UserModel(
        email=email, display_name="Generated Dataset Seed Uploader", password_hash=hash_password(password),
        is_active=True, role="admin", department="executive",
    )
    db.add(user)
    db.commit()
    user_id = user.id
    db.close()
    print(f"Created throwaway uploader {email} (role=admin, so it can tag any department)")

    client = httpx.Client(base_url=BASE_URL, timeout=180.0)
    results = []
    try:
        login = client.post("/auth/login", json={"email": email, "password": password})
        login.raise_for_status()
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        for path, department, classification in documents:
            content = path.read_bytes()
            resp = client.post(
                "/documents/upload",
                files={"file": (path.name, content, "text/markdown")},
                data={"department": department, "security_classification": classification},
                headers=headers,
            )
            if resp.status_code != 201:
                print(f"  FAILED  {path.name:45s} ({department:14s}): {resp.status_code} {resp.text[:300]}")
                results.append((path.name, department, None, 0))
                continue
            body = resp.json()
            print(
                f"  OK      {path.name:45s} ({department:14s}, {classification:12s}) "
                f"-> id={body['id']} status={body['status']} chunks={body['chunk_count']}"
            )
            results.append((path.name, department, body["id"], body["chunk_count"]))
            time.sleep(0.3)

        ok = sum(1 for _, _, doc_id, _ in results if doc_id is not None)
        total_chunks = sum(c for _, _, doc_id, c in results if doc_id is not None)
        print(f"\n{ok}/{len(documents)} documents ingested successfully, {total_chunks} total chunks.")
    finally:
        db = new_session()
        db.query(UserModel).filter(UserModel.id == user_id).delete()
        db.commit()
        db.close()
        print(f"Cleaned up throwaway uploader {email}")


if __name__ == "__main__":
    main()
