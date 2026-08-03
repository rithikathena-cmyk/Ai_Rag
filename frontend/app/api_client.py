import os
import secrets
from pathlib import Path

import requests
from dotenv import load_dotenv

# Same rationale as backend/app/core/config.py: load the repo-root .env by
# absolute path (no-op in Docker, where only app/ is present and OS env vars
# from docker-compose already take precedence) so this works natively too.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:8000")


def get_health() -> dict:
    response = requests.get(f"{BACKEND_URL}/health", timeout=5)
    response.raise_for_status()
    return response.json()


def send_chat_message(message: str, conversation_id: str | None = None, user_id: str | None = None) -> dict:
    payload = {"message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    if user_id:
        payload["user_id"] = user_id
    response = requests.post(f"{BACKEND_URL}/chat", json=payload, timeout=60)
    if not response.ok:
        detail = response.json().get("error", {}).get("message", response.text)
        raise RuntimeError(detail)
    return response.json()


def upload_document(
    filename: str, content: bytes, content_type: str | None, previous_version_of: str | None = None
) -> dict:
    data = {"previous_version_of": previous_version_of} if previous_version_of else None
    response = requests.post(
        f"{BACKEND_URL}/documents/upload",
        files={"file": (filename, content, content_type or "application/octet-stream")},
        data=data,
        timeout=300,
    )
    if not response.ok:
        detail = response.json().get("error", {}).get("message", response.text)
        raise RuntimeError(detail)
    return response.json()


def list_documents(limit: int = 50, offset: int = 0) -> dict:
    response = requests.get(f"{BACKEND_URL}/documents", params={"limit": limit, "offset": offset}, timeout=30)
    response.raise_for_status()
    return response.json()


def delete_document(document_id: str) -> None:
    response = requests.delete(f"{BACKEND_URL}/documents/{document_id}", timeout=30)
    response.raise_for_status()


def get_document(document_id: str) -> dict:
    response = requests.get(f"{BACKEND_URL}/documents/{document_id}", timeout=30)
    response.raise_for_status()
    return response.json()


def get_document_chunks(document_id: str) -> list[dict]:
    response = requests.get(f"{BACKEND_URL}/documents/{document_id}/chunks", timeout=30)
    response.raise_for_status()
    return response.json()


def get_document_entities(document_id: str) -> list[dict]:
    response = requests.get(f"{BACKEND_URL}/documents/{document_id}/entities", timeout=30)
    response.raise_for_status()
    return response.json()


def get_document_versions(document_id: str) -> dict:
    response = requests.get(f"{BACKEND_URL}/documents/{document_id}/versions", timeout=30)
    response.raise_for_status()
    return response.json()


def list_document_permissions(document_id: str) -> list[dict]:
    response = requests.get(f"{BACKEND_URL}/documents/{document_id}/permissions", timeout=30)
    response.raise_for_status()
    return response.json()


def grant_permission(document_id: str, user_id: str, permission_level: str) -> dict:
    response = requests.post(
        f"{BACKEND_URL}/documents/{document_id}/permissions",
        json={"user_id": user_id, "permission_level": permission_level},
        timeout=30,
    )
    if not response.ok:
        detail = response.json().get("error", {}).get("message", response.text)
        raise RuntimeError(detail)
    return response.json()


def revoke_permission(document_id: str, user_id: str) -> None:
    response = requests.delete(f"{BACKEND_URL}/documents/{document_id}/permissions/{user_id}", timeout=30)
    response.raise_for_status()


def search_documents(
    query: str,
    mode: str = "hybrid",
    top_k: int = 10,
    rerank: bool = True,
    document_type: str | None = None,
    classification: str | None = None,
) -> dict:
    filters: dict = {}
    if document_type:
        filters["document_type"] = document_type
    if classification:
        filters["classification"] = classification
    payload = {"query": query, "mode": mode, "top_k": top_k, "rerank": rerank, "filters": filters}
    response = requests.post(f"{BACKEND_URL}/search", json=payload, timeout=30)
    if not response.ok:
        detail = response.json().get("error", {}).get("message", response.text)
        raise RuntimeError(detail)
    return response.json()


def list_reports(limit: int = 50, offset: int = 0) -> dict:
    response = requests.get(f"{BACKEND_URL}/reports", params={"limit": limit, "offset": offset}, timeout=30)
    response.raise_for_status()
    return response.json()


def report_download_url(report_id: str) -> str:
    return f"{BACKEND_URL}/reports/{report_id}/download"


def list_users(limit: int = 100, offset: int = 0) -> list[dict]:
    response = requests.get(f"{BACKEND_URL}/users", params={"limit": limit, "offset": offset}, timeout=30)
    response.raise_for_status()
    return response.json()


def create_user(email: str, display_name: str | None = None) -> dict:
    # This app has no login flow — password_hash is never checked anywhere,
    # so a random throwaway value satisfies the backend's required field.
    response = requests.post(
        f"{BACKEND_URL}/users",
        json={"email": email, "display_name": display_name, "password": secrets.token_urlsafe(24)},
        timeout=30,
    )
    if not response.ok:
        detail = response.json().get("error", {}).get("message", response.text)
        raise RuntimeError(detail)
    return response.json()


def update_user(user_id: str, role: str | None = None, is_active: bool | None = None) -> dict:
    payload = {}
    if role is not None:
        payload["role"] = role
    if is_active is not None:
        payload["is_active"] = is_active
    response = requests.patch(f"{BACKEND_URL}/users/{user_id}", json=payload, timeout=30)
    if not response.ok:
        detail = response.json().get("error", {}).get("message", response.text)
        raise RuntimeError(detail)
    return response.json()


def get_user_preferences(user_id: str) -> dict:
    response = requests.get(f"{BACKEND_URL}/users/{user_id}/preferences", timeout=30)
    response.raise_for_status()
    return response.json()


def put_user_preferences(user_id: str, preferences: dict) -> dict:
    response = requests.put(f"{BACKEND_URL}/users/{user_id}/preferences", json=preferences, timeout=30)
    response.raise_for_status()
    return response.json()


def list_conversations(user_id: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    params = {"limit": limit, "offset": offset}
    if user_id:
        params["user_id"] = user_id
    response = requests.get(f"{BACKEND_URL}/conversations", params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def reindex_document(document_id: str) -> dict:
    response = requests.post(f"{BACKEND_URL}/documents/{document_id}/reindex", timeout=300)
    if not response.ok:
        detail = response.json().get("error", {}).get("message", response.text)
        raise RuntimeError(detail)
    return response.json()


def list_upload_logs(outcome: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    params = {"limit": limit, "offset": offset}
    if outcome:
        params["outcome"] = outcome
    response = requests.get(f"{BACKEND_URL}/upload-logs", params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def list_collections() -> list[dict]:
    response = requests.get(f"{BACKEND_URL}/admin/collections", timeout=30)
    response.raise_for_status()
    return response.json()


def create_collection(name: str, vector_size: int, distance: str) -> dict:
    response = requests.post(
        f"{BACKEND_URL}/admin/collections",
        json={"name": name, "vector_size": vector_size, "distance": distance},
        timeout=30,
    )
    if not response.ok:
        detail = response.json().get("error", {}).get("message", response.text)
        raise RuntimeError(detail)
    return response.json()


def delete_collection(name: str) -> None:
    response = requests.delete(f"{BACKEND_URL}/admin/collections/{name}", timeout=30)
    if not response.ok:
        detail = response.json().get("error", {}).get("message", response.text)
        raise RuntimeError(detail)


def get_metrics() -> dict:
    response = requests.get(f"{BACKEND_URL}/admin/metrics", timeout=30)
    response.raise_for_status()
    return response.json()


def create_eval_query(query: str, description: str | None = None, expected_chunk_ids: list[str] | None = None) -> dict:
    response = requests.post(
        f"{BACKEND_URL}/eval/queries",
        json={"query": query, "description": description, "expected_chunk_ids": expected_chunk_ids or []},
        timeout=30,
    )
    if not response.ok:
        detail = response.json().get("error", {}).get("message", response.text)
        raise RuntimeError(detail)
    return response.json()


def list_eval_queries(limit: int = 100, offset: int = 0) -> list[dict]:
    response = requests.get(f"{BACKEND_URL}/eval/queries", params={"limit": limit, "offset": offset}, timeout=30)
    response.raise_for_status()
    return response.json()


def delete_eval_query(query_id: str) -> None:
    response = requests.delete(f"{BACKEND_URL}/eval/queries/{query_id}", timeout=30)
    response.raise_for_status()


def run_eval_query(query_id: str, k: int = 10) -> dict:
    response = requests.post(f"{BACKEND_URL}/eval/queries/{query_id}/run", params={"k": k}, timeout=120)
    if not response.ok:
        detail = response.json().get("error", {}).get("message", response.text)
        raise RuntimeError(detail)
    return response.json()


def list_eval_runs(eval_query_id: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
    params = {"limit": limit, "offset": offset}
    if eval_query_id:
        params["eval_query_id"] = eval_query_id
    response = requests.get(f"{BACKEND_URL}/eval/runs", params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def get_eval_summary() -> dict:
    response = requests.get(f"{BACKEND_URL}/eval/summary", timeout=30)
    response.raise_for_status()
    return response.json()


def get_conversation(conversation_id: str) -> dict:
    response = requests.get(f"{BACKEND_URL}/conversations/{conversation_id}", timeout=30)
    response.raise_for_status()
    return response.json()


def delete_conversation(conversation_id: str) -> None:
    response = requests.delete(f"{BACKEND_URL}/conversations/{conversation_id}", timeout=30)
    response.raise_for_status()
