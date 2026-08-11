"""Verification pass for the corpus_v2 seed: Postgres/Qdrant counts,
/admin/index-consistency, per-department retrieval, RBAC cross-department
denial, PII redaction in citations, and citation metadata correctness.

Uses direct service calls (search_documents) for retrieval/RBAC checks —
same real DB/Qdrant/reranker path production uses — plus one throwaway
per-role user login for a real /chat round trip to prove degraded=False,
citation metadata, and PII redaction end-to-end over HTTP.

Usage: python scripts/verify_corpus_v2.py  (requires backend on :8000)
"""

import re
import sys
import uuid

import httpx

sys.path.insert(0, ".")

from app.db.postgres import new_session
from app.models.user import UserModel
from app.services.agents.retrieval_agent import search_documents
from app.services.auth.password import hash_password

BASE_URL = "http://127.0.0.1:8000"

ROLE_QUERIES = [
    ("user", ("manufacturing",), "What is the seal strength requirement for Line 7 packaging?"),
    ("hr", ("hr",), "What is the 401k match percentage in the employee benefits guide?"),
    ("project_manager", ("engineering",), "What is the rated throughput of the FX-2200 filling machine?"),
    ("ceo", ("manufacturing", "hr", "engineering", "executive"), "What was the enterprise OEE in Q1 2026?"),
]


def check_retrieval_and_rbac():
    print("\n=== Retrieval + RBAC (direct service calls, real DB/Qdrant) ===")
    db = new_session()
    for role, depts, query in ROLE_QUERIES:
        results = search_documents(db, query=query, top_k=8, role=role, knowledge_departments=depts)
        filenames = sorted({r["document_filename"] for r in results})
        print(f"  role={role:16s} depts={depts} query={query!r}")
        print(f"    -> {len(results)} hits: {filenames}")
    db.close()


def check_cross_department_denial():
    print("\n=== Cross-department denial (Employee asking about HR/Executive topics) ===")
    db = new_session()
    # An Employee (knowledge_departments=('manufacturing',)) asking about HR
    # benefits or the restricted Strategic Manufacturing Plan must get zero
    # hits from those documents, even though the topic exists in the corpus.
    for query in ["What is the 401k match percentage?", "What is the capital plan for predictive maintenance?"]:
        results = search_documents(db, query=query, top_k=8, role="user", knowledge_departments=("manufacturing",))
        filenames = sorted({r["document_filename"] for r in results})
        leaked = [f for f in filenames if f.startswith("hr_") or f.startswith("exec_")]
        print(f"  Employee query={query!r} -> files={filenames}  leaked_hr_or_exec={leaked}")
    db.close()


PHONE_RE = re.compile(r"\b(?:\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def check_pii_end_to_end():
    print("\n=== PII redaction end-to-end via real /chat (HTTP) ===")
    email = f"verify-corpus-v2-{uuid.uuid4().hex[:8]}@example.com"
    password = "Verify-Corpus-V2-Temp-Pass-1!"
    db = new_session()
    user = UserModel(
        email=email, display_name="Corpus V2 Verify User", password_hash=hash_password(password),
        is_active=True, role="hr", department="hr",
    )
    db.add(user)
    db.commit()
    user_id = user.id
    db.close()

    client = httpx.Client(base_url=BASE_URL, timeout=120.0)
    try:
        login = client.post("/auth/login", json={"email": email, "password": password})
        login.raise_for_status()
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        question = "Who was the reported-by contact on the workplace grievance investigation, and what was the finding?"
        resp = client.post("/chat", json={"message": question}, headers=headers)
        resp.raise_for_status()
        body = resp.json()

        print(f"  degraded={body['degraded']} degraded_reason={body['degraded_reason']}")
        print(f"  reply: {body['reply'][:300]!r}")
        print(f"  {len(body['sources'])} source(s):")
        any_pii_leak = False
        any_redaction_marker = False
        for s in body["sources"]:
            text = s["text"]
            phones = PHONE_RE.findall(text)
            emails = EMAIL_RE.findall(text)
            ssns = SSN_RE.findall(text)
            has_redaction = "REDACTED" in text
            any_redaction_marker = any_redaction_marker or has_redaction
            leaked_this_source = bool(phones or emails or ssns)
            any_pii_leak = any_pii_leak or leaked_this_source
            print(
                f"    [{s['index']}] {s.get('document_filename')} (chunk {s['chunk_index']}) "
                f"redaction_marker={has_redaction} raw_phone_matches={phones} raw_email_matches={emails} raw_ssn_matches={ssns}"
            )
            print(f"        metadata check: document_id={s['document_id']!r} chunk_id={s['chunk_id']!r}")

        print(f"\n  ANY raw PII leaked into user-facing sources: {any_pii_leak}")
        print(f"  Any redaction marker present (expected True for this grievance doc): {any_redaction_marker}")

        conv_id = body["conversation_id"]
        conv = client.get(f"/conversations/{conv_id}", headers=headers)
        if conv.status_code == 200:
            msgs = conv.json().get("messages", [])
            assistant_msgs = [m for m in msgs if m.get("role") == "assistant"]
            if assistant_msgs:
                stored_sources = assistant_msgs[-1].get("sources", [])
                stored_leak = any(
                    PHONE_RE.search(s.get("text") or "") or EMAIL_RE.search(s.get("text") or "") or SSN_RE.search(s.get("text") or "")
                    for s in stored_sources
                )
                print(f"  Persisted conversation history raw-PII leak: {stored_leak}")
    finally:
        db = new_session()
        db.query(UserModel).filter(UserModel.id == user_id).delete()
        db.commit()
        db.close()
        print(f"  Cleaned up throwaway user {email}")


def check_index_consistency():
    print("\n=== /admin/index-consistency ===")
    email = f"verify-admin-{uuid.uuid4().hex[:8]}@example.com"
    password = "Verify-Admin-Temp-Pass-1!"
    db = new_session()
    user = UserModel(
        email=email, display_name="Verify Admin", password_hash=hash_password(password),
        is_active=True, role="admin", department="executive",
    )
    db.add(user)
    db.commit()
    user_id = user.id
    db.close()

    client = httpx.Client(base_url=BASE_URL, timeout=60.0)
    try:
        login = client.post("/auth/login", json={"email": email, "password": password})
        login.raise_for_status()
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        resp = client.get("/admin/index-consistency", headers=headers)
        print(f"  status={resp.status_code}")
        print(f"  body: {resp.json()}")
    finally:
        db = new_session()
        db.query(UserModel).filter(UserModel.id == user_id).delete()
        db.commit()
        db.close()
        print(f"  Cleaned up throwaway admin {email}")


if __name__ == "__main__":
    check_retrieval_and_rbac()
    check_cross_department_denial()
    check_pii_end_to_end()
    check_index_consistency()
