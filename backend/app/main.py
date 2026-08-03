import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.routers import (
    health, chat, documents, terms, users, upload_logs, search, reports, conversations, admin, evaluation,
)
from app.services.monitoring.metrics import record_latency

app = FastAPI(title="rag-chat backend")

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(documents.router)
app.include_router(terms.router)
app.include_router(users.router)
app.include_router(upload_logs.router)
app.include_router(search.router)
app.include_router(reports.router)
app.include_router(conversations.router)
app.include_router(admin.router)
app.include_router(evaluation.router)


@app.middleware("http")
async def track_latency(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    record_latency(request.url.path, (time.perf_counter() - start) * 1000)
    return response


@app.on_event("startup")
async def warm_models():
    # Loads BGE-M3, the zero-shot classifier, the spaCy NER pipeline, and the
    # BGE reranker once at boot instead of on the first upload/search request,
    # so the cold load doesn't hit a user.
    from app.services.classification.zero_shot import get_pipeline
    from app.services.embedding.model_loader import get_model
    from app.services.entities.spacy_ner import get_nlp
    from app.services.reranking.model_loader import get_reranker

    await run_in_threadpool(get_model)
    await run_in_threadpool(get_pipeline)
    await run_in_threadpool(get_nlp)
    await run_in_threadpool(get_reranker)


@app.exception_handler(HTTPException)
def http_exception_handler(request: Request, exc: HTTPException):
    code = getattr(exc, "code", "http_error")
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": code, "message": exc.detail}},
    )


@app.exception_handler(RequestValidationError)
def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "validation_error", "message": str(exc)}},
    )


@app.exception_handler(Exception)
def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": str(exc)}},
    )
