import logging
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.request_context import request_id_ctx
from app.routers import (
    audit, auth, health, chat, documents, terms, users, upload_logs, search, reports, conversations, admin,
    evaluation, projects, approvals, guardrail_policies, policy_copilot, traces, pii_access,
)
from app.services.monitoring.metrics import record_latency

# Request-id correlation for logs — a ContextVar (app/core/request_context.py,
# shared with service-layer code that can't import this module — see that
# module's docstring) so any logger.*() call anywhere in the request's call
# stack picks it up for free via the filter below, without threading a
# request_id param through every function that only ever logs (functions
# that need it for durable records — metrics, audit log — still take it as
# an explicit param since those outlive the request-scoped contextvar; see
# services/retrieval/search.py).
_request_id_ctx = request_id_ctx


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_ctx.get()
        return True


# Nothing configured the root logger before this — existing logger.warning/
# logger.exception calls (retry_handler.py, usage_tracker.py, engine.py, ...)
# only ever reached Python's last-resort stderr handler with no formatting.
# Filter is attached to the handler (not a logger) so it applies regardless
# of which module's logger the record originated from.
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"))
_handler.addFilter(_RequestIdFilter())
logging.basicConfig(level=logging.INFO, handlers=[_handler])

logger = logging.getLogger(__name__)

app = FastAPI(title="rag-chat backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_allowed_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
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
app.include_router(projects.router)
app.include_router(approvals.router)
app.include_router(audit.router)
app.include_router(guardrail_policies.router)
app.include_router(policy_copilot.router)
app.include_router(traces.router)
app.include_router(pii_access.router)


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = request_id
    token = _request_id_ctx.set(request_id)
    start = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        _request_id_ctx.reset(token)
    record_latency(request.url.path, (time.perf_counter() - start) * 1000)
    response.headers["x-request-id"] = request_id
    return response


def _current_request_id(request: Request) -> str:
    # request.state is backed by the ASGI scope dict, shared by reference
    # across the whole middleware chain — unlike _request_id_ctx, it stays
    # correct even for the bare-Exception handler below, which Starlette
    # runs from ServerErrorMiddleware *outside* our own middleware (so
    # outside this module's contextvar scope) by design, precisely so it can
    # catch errors raised by other middleware too.
    return getattr(request.state, "request_id", None) or _request_id_ctx.get()


@app.on_event("startup")
async def warm_models():
    # Loads BGE-M3, the spaCy NER pipeline, and the BGE reranker once at boot
    # instead of on the first upload/search request, so the cold load doesn't
    # hit a user.
    from app.services.embedding.model_loader import get_model
    from app.services.entities.spacy_ner import get_nlp
    from app.services.ingestion import docling_parser
    from app.services.reranking.model_loader import get_reranker

    await run_in_threadpool(get_model)
    await run_in_threadpool(get_nlp)
    await run_in_threadpool(get_reranker)
    await run_in_threadpool(docling_parser.warm)


@app.exception_handler(HTTPException)
def http_exception_handler(request: Request, exc: HTTPException):
    code = getattr(exc, "code", "http_error")
    request_id = _current_request_id(request)
    if exc.status_code >= 500:
        logger.error("HTTP %s %s on %s %s: %s", exc.status_code, code, request.method, request.url.path, exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": code, "message": exc.detail, "request_id": request_id}},
        headers={"x-request-id": request_id},
    )


@app.exception_handler(RequestValidationError)
def validation_exception_handler(request: Request, exc: RequestValidationError):
    request_id = _current_request_id(request)
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "validation_error", "message": str(exc), "request_id": request_id}},
        headers={"x-request-id": request_id},
    )


@app.exception_handler(Exception)
def unhandled_exception_handler(request: Request, exc: Exception):
    # The client only ever gets a generic message — str(exc) can contain raw
    # DB errors, connection strings, or other internals that must never leak
    # over the wire. Full detail (with traceback) goes server-side only,
    # tagged with the same request_id the client sees, so an incident can be
    # traced from a user-reported request_id straight to the matching log.
    #
    # Starlette routes bare-Exception handling through ServerErrorMiddleware,
    # which wraps *outside* our own observability_middleware — so this
    # handler runs outside this module's contextvar scope, and
    # observability_middleware never gets to add its own x-request-id
    # response header for this path (it already re-raised past that point).
    # Re-set the contextvar from request.state (scope-backed, unaffected by
    # that boundary) so this log line's own %(request_id)s still matches
    # what the client sees, and set the header directly here too.
    request_id = _current_request_id(request)
    _request_id_ctx.set(request_id)
    # logger.exception() (bare, relying on sys.exc_info()) silently loses the
    # traceback here: Starlette runs sync exception handlers like this one
    # via run_in_threadpool, in a worker thread with its own fresh call
    # stack — sys.exc_info() in that thread sees no active exception even
    # though `exc` was passed in explicitly. Pass exc_info=exc directly so
    # the traceback is captured regardless of which thread this runs in.
    logger.error("Unhandled exception on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "An unexpected error occurred. Please try again or contact support.",
                "request_id": request_id,
            }
        },
        headers={"x-request-id": request_id},
    )
