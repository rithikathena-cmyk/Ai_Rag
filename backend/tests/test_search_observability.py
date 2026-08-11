"""app/main.py — observability_middleware (request-id contextvar) + the
catch-all exception handler's sanitized-500 fix. Uses the real app (no
startup event fires under TestClient without a `with` block, so no model
loading), with one extra test-only route that deliberately raises.
"""

import logging

from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app, raise_server_exceptions=False)


@main.app.get("/__test_boom__")
def _boom():
    raise RuntimeError("raw db connection string: postgresql://user:pw@host/db")


def test_500_response_is_sanitized_and_has_request_id():
    response = client.get("/__test_boom__")

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert "raw db connection string" not in body["error"]["message"]
    assert body["error"]["message"] == "An unexpected error occurred. Please try again or contact support."
    assert body["error"]["request_id"]
    assert response.headers["x-request-id"] == body["error"]["request_id"]


def test_unhandled_exception_is_logged_server_side(caplog):
    with caplog.at_level(logging.ERROR, logger="app.main"):
        client.get("/__test_boom__")

    assert any("Unhandled exception" in r.message for r in caplog.records)
    assert any(r.exc_info is not None for r in caplog.records)


def test_request_id_header_is_echoed_when_supplied():
    response = client.get("/__test_boom__", headers={"x-request-id": "client-supplied-id"})
    assert response.headers["x-request-id"] == "client-supplied-id"
    assert response.json()["error"]["request_id"] == "client-supplied-id"
