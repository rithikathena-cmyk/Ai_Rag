from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.db.postgres import get_engine
from app.db.qdrant import get_qdrant_client

router = APIRouter()


@router.get("/health")
def health():
    try:
        get_qdrant_client().get_collections()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Qdrant unreachable: {exc}")
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Postgres unreachable: {exc}")
    return {"status": "ok", "qdrant": "connected", "postgres": "connected"}
