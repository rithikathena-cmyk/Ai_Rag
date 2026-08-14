"""Thin HTTP wrapper around the FastAPI backend.

Every call goes through `_request`, which normalizes both transport failures
(timeouts, connection errors) and backend error responses (the
`{"error": {"code", "message"}}` shape produced by the exception handlers in
backend/app/main.py) into a single `APIError`. UI code (app/views/*.py) never
touches `requests` directly — it only calls the functions below and catches
`APIError`.
"""

import os
from pathlib import Path
from typing import Any

import requests
import streamlit as st
from dotenv import load_dotenv

# Repo-root .env for native runs. In Docker only app/ is mounted, this path
# doesn't exist, and the OS env vars docker-compose injects take over instead.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000").rstrip("/")
DEFAULT_TIMEOUT = 30  # seconds; overridden per-call below for slow endpoints

_session = requests.Session()


class APIError(Exception):
    """Raised for both non-2xx backend responses and network-level failures."""

    def __init__(self, message: str, status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def _auth_headers() -> dict:
    # Read from st.session_state (per-browser-session), not a module global —
    # this module's _session/token-free functions are shared across every
    # user connected to this Streamlit server process, so a bare module-level
    # token would leak between users. session_state is the one thing here
    # that's actually per-session.
    token = st.session_state.get("access_token")
    return {"Authorization": f"Bearer {token}"} if token else {}


class _RefreshUnreachable(Exception):
    """Raised when the /auth/refresh call itself couldn't be completed
    (network error, backend down/restarting) — distinct from the refresh
    token being genuinely rejected. Not evidence the refresh token is bad,
    so callers must NOT clear the session over it (that would silently log
    the user out over a transient connectivity blip, e.g. the backend
    process restarting)."""


def _try_refresh() -> bool:
    """Attempts one access-token refresh using the stored refresh_token.
    Returns True on success (session_state's access_token is updated in
    place); False if there's no refresh_token or the backend genuinely
    rejected it — callers should treat False as "give up, the user needs to
    log in again." Raises _RefreshUnreachable if the backend couldn't be
    reached at all; see that class's docstring for why callers must handle
    it separately from a plain False."""
    refresh_token = st.session_state.get("refresh_token")
    if not refresh_token:
        return False
    try:
        response = _session.post(f"{BACKEND_URL}/auth/refresh", json={"refresh_token": refresh_token}, timeout=10)
    except requests.exceptions.RequestException as exc:
        raise _RefreshUnreachable(str(exc)) from exc
    if not response.ok:
        return False
    st.session_state["access_token"] = response.json()["access_token"]
    return True


def clear_session_auth() -> None:
    st.session_state.pop("access_token", None)
    st.session_state.pop("refresh_token", None)
    st.session_state.pop("current_user", None)
    # Also drop the previous user's chat transcript/conversation_id — Streamlit
    # session_state is scoped to the browser tab, not to auth, so without this
    # the next person to log in on this tab would see the prior user's
    # messages rendered, and their first turn would carry a conversation_id
    # that (per routers/chat.py's get_conversation()) belongs to someone else.
    st.session_state.pop("chat_messages", None)
    st.session_state.pop("conversation_id", None)


def _request(method: str, path: str, *, timeout: float = DEFAULT_TIMEOUT, _retried: bool = False, **kwargs) -> Any:
    url = f"{BACKEND_URL}{path}"
    headers = {**_auth_headers(), **(kwargs.pop("headers", None) or {})}
    try:
        response = _session.request(method, url, timeout=timeout, headers=headers, **kwargs)
    except requests.exceptions.ConnectTimeout:
        raise APIError(f"Timed out connecting to {url}. Is the backend running?")
    except requests.exceptions.ReadTimeout:
        raise APIError(f"{method} {path} took longer than {timeout}s to respond.")
    except requests.exceptions.ConnectionError:
        raise APIError(f"Could not reach the backend at {BACKEND_URL}. Is it running?")
    except requests.exceptions.RequestException as exc:
        raise APIError(f"Request to {path} failed: {exc}")

    # An expired access token is worth one silent refresh-and-retry; a
    # missing/invalid one (never logged in, or the refresh token itself is
    # dead) isn't recoverable here — surface it as a normal APIError instead.
    if response.status_code == 401 and not _retried and st.session_state.get("refresh_token"):
        try:
            refreshed = _try_refresh()
        except _RefreshUnreachable:
            # Couldn't even reach the backend to attempt the refresh — leave
            # the session intact (the refresh_token itself was never actually
            # checked) and surface a normal, retryable error instead of
            # silently logging the user out.
            raise APIError(f"Could not reach the backend at {BACKEND_URL} to refresh your session. Is it running?")
        if refreshed:
            return _request(method, path, timeout=timeout, _retried=True, **kwargs)
        clear_session_auth()

    if not response.ok:
        code, message = None, response.text or f"HTTP {response.status_code}"
        try:
            body = response.json()
            error = body.get("error", {})
            code = error.get("code")
            message = error.get("message", message)
        except ValueError:
            pass  # non-JSON error body (e.g. a raw 502 from a proxy) — keep response.text
        raise APIError(message, status_code=response.status_code, code=code)

    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def _download(path: str, *, timeout: float = 60) -> tuple[bytes, str, str]:
    """Returns (content, content_type, filename) for a file-download endpoint."""
    url = f"{BACKEND_URL}{path}"
    try:
        response = _session.get(url, timeout=timeout, headers=_auth_headers())
    except requests.exceptions.RequestException as exc:
        raise APIError(f"Download from {path} failed: {exc}")
    if not response.ok:
        raise APIError(f"Download failed with HTTP {response.status_code}", status_code=response.status_code)

    content_type = response.headers.get("content-type", "application/octet-stream")
    filename = "download"
    disposition = response.headers.get("content-disposition", "")
    if "filename=" in disposition:
        filename = disposition.split("filename=", 1)[1].strip('"; ')
    return response.content, content_type, filename


# ---------------------------------------------------------------- health ---

def get_health() -> dict:
    return _request("GET", "/health", timeout=5)


# ------------------------------------------------------------------ auth ---

def login(email: str, password: str) -> dict:
    """Returns {"access_token", "refresh_token", "token_type"}. Doesn't touch
    session_state itself — the caller (views/login.py) decides when/how to
    store it, so this function stays a plain API call like everything else here."""
    return _request("POST", "/auth/login", json={"email": email, "password": password}, timeout=15)


def get_current_user_info() -> dict:
    return _request("GET", "/auth/me", timeout=10)


def logout() -> None:
    clear_session_auth()


# ------------------------------------------------------------------ chat ---

def send_chat_message(
    message: str,
    conversation_id: str | None = None,
    top_k: int | None = None,
    action: str | None = None,
    model_tier: str | None = None,
) -> dict:
    # /chat is LLM-RBAC-governed and requires a bearer token (_auth_headers(),
    # via _request) — there is no anonymous/client-supplied-user_id mode
    # anymore; the caller's identity always comes from the token.
    payload: dict[str, Any] = {"message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    if top_k:
        payload["top_k"] = top_k
    if action:
        payload["action"] = action
    if model_tier:
        # Manual "try a different model" retry — still gated by the caller's
        # role via llm_rbac.yaml's tiers_allowed on the backend, see
        # views/chat.py's retry button.
        payload["model_tier"] = model_tier
    # Agent loop can call up to 4 tools (retrieval / SQL / report) per turn.
    return _request("POST", "/chat", json=payload, timeout=120)


# --------------------------------------------------------- conversations ---

def list_conversations(user_id: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    params = {"limit": limit, "offset": offset}
    if user_id:
        params["user_id"] = user_id
    return _request("GET", "/conversations", params=params)


def get_conversation(conversation_id: str) -> dict:
    return _request("GET", f"/conversations/{conversation_id}")


def delete_conversation(conversation_id: str) -> None:
    _request("DELETE", f"/conversations/{conversation_id}")


# ------------------------------------------------------------- documents ---

def upload_document(
    filename: str, content: bytes, content_type: str | None,
    previous_version_of: str | None = None, client_document_id: str | None = None,
) -> dict:
    files = {"file": (filename, content, content_type or "application/octet-stream")}
    data = {}
    if previous_version_of:
        data["previous_version_of"] = previous_version_of
    if client_document_id:
        # Lets the caller start polling get_ingestion_progress(client_document_id)
        # immediately, before this (synchronous, potentially minutes-long) call returns.
        data["client_document_id"] = client_document_id
    # Parsing + embedding + reranking happens synchronously in this request.
    # Docling's models are warmed at backend startup (see main.py:warm_models),
    # but a large PDF can still legitimately take a few minutes on CPU.
    return _request("POST", "/documents/upload", files=files, data=data or None, timeout=2000)


def list_documents(limit: int = 50, offset: int = 0) -> dict:
    return _request("GET", "/documents", params={"limit": limit, "offset": offset})


def get_document(document_id: str) -> dict:
    return _request("GET", f"/documents/{document_id}")


def get_ingestion_progress(document_id: str) -> dict:
    return _request("GET", f"/documents/{document_id}/progress", timeout=5)


def delete_document(document_id: str) -> None:
    _request("DELETE", f"/documents/{document_id}")


def reindex_document(document_id: str) -> dict:
    return _request("POST", f"/documents/{document_id}/reindex", timeout=120)


def get_document_chunks(document_id: str, limit: int = 200, offset: int = 0) -> list[dict]:
    return _request("GET", f"/documents/{document_id}/chunks", params={"limit": limit, "offset": offset})


def get_document_text(document_id: str) -> dict:
    return _request("GET", f"/documents/{document_id}/text")


def get_document_versions(document_id: str) -> dict:
    return _request("GET", f"/documents/{document_id}/versions")


def get_document_entities(document_id: str) -> list[dict]:
    return _request("GET", f"/documents/{document_id}/entities")


def grant_permission(document_id: str, user_id: str, permission_level: str) -> dict:
    return _request(
        "POST", f"/documents/{document_id}/permissions",
        json={"user_id": user_id, "permission_level": permission_level},
    )


def list_permissions(document_id: str) -> list[dict]:
    return _request("GET", f"/documents/{document_id}/permissions")


def revoke_permission(document_id: str, user_id: str) -> None:
    _request("DELETE", f"/documents/{document_id}/permissions/{user_id}")


# ----------------------------------------------------------------- terms ---

def get_term_chunks(term: str, limit: int = 50, offset: int = 0) -> dict:
    return _request("GET", f"/terms/{term}/chunks", params={"limit": limit, "offset": offset})


def get_chunk_terms(chunk_id: str) -> list[dict]:
    return _request("GET", f"/chunks/{chunk_id}/terms")


# ----------------------------------------------------------------- users ---

def create_user(
    email: str, password: str, display_name: str | None = None,
    role: str | None = None, department: str | None = None,
) -> dict:
    # Admin/CEO-only on the backend (require_role) — the caller's own bearer
    # token goes on this request automatically via _request()'s
    # _auth_headers(), same as every other authenticated call here.
    return _request(
        "POST", "/users",
        json={"email": email, "password": password, "display_name": display_name, "role": role, "department": department},
    )


def get_user(user_id: str) -> dict:
    return _request("GET", f"/users/{user_id}")


def get_my_usage() -> dict:
    return _request("GET", "/users/me/usage")


def get_my_capabilities() -> dict:
    return _request("GET", "/users/me/capabilities")


def list_users(limit: int = 50, offset: int = 0) -> list[dict]:
    return _request("GET", "/users", params={"limit": limit, "offset": offset})


def update_user(user_id: str, role: str | None = None, is_active: bool | None = None) -> dict:
    body = {k: v for k, v in {"role": role, "is_active": is_active}.items() if v is not None}
    return _request("PATCH", f"/users/{user_id}", json=body)


def set_user_token_limit(user_id: str, daily_tokens: int | None, monthly_tokens: int | None) -> dict:
    # Admin/CEO-only on the backend (require_role) — PUT semantics: the body
    # is the full desired override state, so passing None for a field clears
    # that override back to the role default rather than leaving it untouched.
    return _request(
        "PUT", f"/users/{user_id}/token-limit",
        json={"daily_tokens": daily_tokens, "monthly_tokens": monthly_tokens},
    )


def get_user_usage(user_id: str) -> dict:
    # Admin/CEO-only — lets the token-limit editor show a target user's
    # current usage before deciding whether to change their limit or reset it.
    return _request("GET", f"/users/{user_id}/usage")


def reset_user_usage(user_id: str) -> dict:
    # Admin/CEO-only, same as set_user_token_limit above — but a deliberately
    # separate call/action, never bundled into the token-limit save. See
    # reset_usage()'s docstring in services/llm_rbac/quotas.py for why.
    return _request("POST", f"/users/{user_id}/usage/reset")


def get_user_preferences(user_id: str) -> dict:
    return _request("GET", f"/users/{user_id}/preferences")


def put_user_preferences(user_id: str, preferences: dict) -> dict:
    return _request("PUT", f"/users/{user_id}/preferences", json=preferences)


# ---------------------------------------------------------- upload logs ---

def list_upload_logs(outcome: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if outcome:
        params["outcome"] = outcome
    return _request("GET", "/upload-logs", params=params)


# ---------------------------------------------------------------- search ---

def search(query: str, mode: str = "hybrid", top_k: int = 10, rerank: bool = True, filters: dict | None = None) -> dict:
    # /search is LLM-RBAC-governed the same way /chat is — requires a bearer
    # token, results are narrowed to the caller's role/department automatically.
    payload = {"query": query, "mode": mode, "top_k": top_k, "rerank": rerank, "filters": filters or {}}
    return _request("POST", "/search", json=payload, timeout=60)


# --------------------------------------------------------------- reports ---

def list_reports(limit: int = 50, offset: int = 0) -> dict:
    return _request("GET", "/reports", params={"limit": limit, "offset": offset})


def download_report(report_id: str) -> tuple[bytes, str, str]:
    return _download(f"/reports/{report_id}/download")


# ----------------------------------------------------------------- admin ---

def list_collections() -> list[dict]:
    return _request("GET", "/admin/collections")


def create_collection(name: str, vector_size: int, distance: str = "Cosine") -> dict:
    return _request("POST", "/admin/collections", json={"name": name, "vector_size": vector_size, "distance": distance})


def delete_collection(name: str) -> None:
    _request("DELETE", f"/admin/collections/{name}")


def get_metrics() -> dict:
    return _request("GET", "/admin/metrics")


def get_query_metrics() -> dict:
    return _request("GET", "/admin/query-metrics")


def get_gateway_usage(limit: int = 200) -> dict:
    return _request("GET", "/admin/gateway-usage", params={"limit": limit})


def get_guardrail_analytics() -> dict:
    return _request("GET", "/admin/guardrail-analytics")


def get_model_availability() -> dict:
    return _request("GET", "/admin/model-availability")


def set_model_availability(disabled: bool) -> dict:
    return _request("PUT", "/admin/model-availability", json={"disabled": disabled})


def get_roles() -> dict:
    """Read-only per-role permission/tool/quota summary — VIEW_ROLES-gated
    (CEO/Admin). See views/roles.py."""
    return _request("GET", "/admin/roles")


# -------------------------------------------------------------- approvals ---

def list_approvals(status: str = "pending", limit: int = 50, offset: int = 0) -> dict:
    return _request("GET", "/approvals", params={"status": status, "limit": limit, "offset": offset})


def get_approval(approval_id: str) -> dict:
    return _request("GET", f"/approvals/{approval_id}")


def decide_approval(approval_id: str, decision: str, reason: str | None = None, values: dict | None = None) -> dict:
    body: dict[str, Any] = {"decision": decision}
    if reason:
        body["reason"] = reason
    if values:
        body["values"] = values
    return _request("POST", f"/approvals/{approval_id}/decide", json=body)


# ------------------------------------------------------------ evaluation ---

def create_eval_query(query: str, description: str | None = None, expected_chunk_ids: list[str] | None = None) -> dict:
    return _request(
        "POST", "/eval/queries",
        json={"query": query, "description": description, "expected_chunk_ids": expected_chunk_ids or []},
    )


def list_eval_queries(limit: int = 100, offset: int = 0) -> list[dict]:
    return _request("GET", "/eval/queries", params={"limit": limit, "offset": offset})


def delete_eval_query(query_id: str) -> None:
    _request("DELETE", f"/eval/queries/{query_id}")


def run_eval_query(query_id: str, k: int = 10) -> dict:
    # Runs retrieval + LLM judging for this query — slower than a typical call.
    return _request("POST", f"/eval/queries/{query_id}/run", params={"k": k}, timeout=120)


def list_eval_runs(eval_query_id: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if eval_query_id:
        params["eval_query_id"] = eval_query_id
    return _request("GET", "/eval/runs", params=params)


def eval_summary() -> dict:
    return _request("GET", "/eval/summary")


def run_experiment_gate(
    k: int = 10,
    eval_query_ids: list[str] | None = None,
    include_parent_child: bool = True,
    include_query_rewrite: bool = True,
    include_combined: bool = False,
) -> dict:
    # Runs the full baseline/parent-child/query-rewrite (± combined) sweep —
    # several eval runs back-to-back, slower than a single query run.
    body = {
        "k": k, "eval_query_ids": eval_query_ids,
        "include_parent_child": include_parent_child, "include_query_rewrite": include_query_rewrite,
        "include_combined": include_combined,
    }
    return _request("POST", "/eval/experiments/run", json=body, timeout=300)
