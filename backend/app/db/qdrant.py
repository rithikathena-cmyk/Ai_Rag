from qdrant_client import QdrantClient

from app.core.config import settings

_client: QdrantClient | None = None


def get_qdrant_client() -> QdrantClient:
    global _client
    if _client is None:
        if settings.qdrant_url:
            # Qdrant Cloud (or any URL+API-key-authenticated instance) — see
            # core/config.py's qdrant_url/qdrant_api_key docstring.
            _client = QdrantClient(
                url=settings.qdrant_url, api_key=settings.qdrant_api_key or None, timeout=settings.qdrant_timeout_seconds,
            )
        else:
            _client = QdrantClient(
                host=settings.qdrant_host, port=settings.qdrant_port, timeout=settings.qdrant_timeout_seconds,
            )
    return _client
