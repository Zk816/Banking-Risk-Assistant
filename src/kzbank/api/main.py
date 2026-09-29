"""HTTP service.

Two decisions worth knowing about.

**Models load at startup, not on the first request.** bge-m3 takes ~3s and the
reranker ~2.7s. Loading them lazily means the first user waits six seconds and
every replica behind a load balancer has its own cold start.

**Endpoints are `def`, not `async def`.** Everything underneath — torch, psycopg,
the blocking HTTP call to ollama — is synchronous. A sync endpoint runs in
FastAPI's threadpool; declaring it `async` would block the event loop for the
whole request and serialise every other client behind it.
"""

import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from kzbank.agent.router import answer as route_answer
from kzbank.api.schemas import (
    AskRequest,
    AskResponse,
    DependencyStatus,
    ErrorResponse,
    HealthResponse,
    ToolCallInfo,
)
from kzbank.config import settings
from kzbank.logging_setup import get_logger, setup_logging
from kzbank.retrieval.embed import get_model
from kzbank.retrieval.rerank import get_reranker
from kzbank.storage.db import table_counts
from kzbank.storage.freshness import check_sources

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201, ARG001
    """Load the models before the first request, release nothing after.

    A failure here should stop the process rather than leave a replica serving
    requests it cannot answer.
    """
    setup_logging()
    started = time.perf_counter()
    get_model()
    get_reranker()
    log.info("models loaded in %.1fs", time.perf_counter() - started)
    yield


app = FastAPI(
    title="Kazakhstan Banking Intelligence",
    description="Цитируемые ответы по банковскому законодательству и курсам валют.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def add_request_id(request: Request, call_next):  # noqa: ANN001, ANN201
    """Tag every request so a log line can be tied to a response."""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    started = time.perf_counter()

    response = await call_next(request)

    duration_ms = int((time.perf_counter() - started) * 1000)
    response.headers["x-request-id"] = request_id
    log.info(
        "%s %s -> %d %dms rid=%s",
        request.method, request.url.path, response.status_code, duration_ms, request_id,
    )
    return response


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


@app.exception_handler(RequestValidationError)
async def on_validation_error(request: Request, exc: RequestValidationError):  # noqa: ANN201
    """A bad request is the client's problem; say so without a stack trace."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=ErrorResponse(
            code="validation_error",
            message="; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                              for e in exc.errors()),
            request_id=_request_id(request),
        ).model_dump(),
    )


@app.exception_handler(RuntimeError)
async def on_upstream_error(request: Request, exc: RuntimeError):  # noqa: ANN201
    """The pipeline raises RuntimeError when ollama or Postgres is unreachable.

    503, not 500: nothing is wrong with the request, and a client may retry.
    """
    request_id = _request_id(request)
    log.error("upstream unavailable rid=%s: %s", request_id, exc)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=ErrorResponse(
            code="upstream_unavailable", message=str(exc), request_id=request_id,
        ).model_dump(),
    )


@app.exception_handler(Exception)
async def on_unexpected_error(request: Request, exc: Exception):  # noqa: ANN201
    """Anything else is our bug. Log the traceback once, here, and return an id.

    The message is deliberately generic: an internal error text can carry table
    names, paths and fragments of a user's question.
    """
    request_id = _request_id(request)
    log.exception("unhandled error rid=%s", request_id)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            code="internal_error",
            message="Внутренняя ошибка. Сообщите request_id.",
            request_id=request_id,
        ).model_dump(),
    )


@app.post("/v1/ask", response_model=AskResponse)
def ask(body: AskRequest, request: Request) -> AskResponse:
    """Answer a question, using whichever tools it needs.

    Raises:
        RuntimeError: if the model server is unreachable (handled as 503).
    """
    started = time.perf_counter()
    result = route_answer(body.question)

    return AskResponse(
        answer=result.text,
        grounded=result.grounded,
        refused=bool(result.fabricated) or not result.grounded,
        tools_used=[
            ToolCallInfo(name=c.name, arguments=c.arguments, ok=c.ok,
                         found=c.result.get("found"))
            for c in result.calls
        ],
        request_id=_request_id(request),
        duration_ms=int((time.perf_counter() - started) * 1000),
    )


@app.get("/healthz", response_model=HealthResponse)
def healthz() -> JSONResponse:
    """Per-dependency health. 503 when anything required is down.

    Deliberately not a bare 200: a replica whose database is gone is not healthy,
    and a load balancer needs to know.
    """
    checks: list[DependencyStatus] = []

    try:
        counts = table_counts()
        checks.append(DependencyStatus(
            name="postgres", ok=True,
            detail=f"chunks={counts['chunks']} metrics={counts['metrics']}"))
    except Exception as e:  # noqa: BLE001 — a health check reports, never raises
        checks.append(DependencyStatus(name="postgres", ok=False, detail=str(e)[:120]))

    try:
        for source in check_sources():
            checks.append(DependencyStatus(
                name=f"data:{source.source}", ok=not source.stale, detail=source.detail))
    except Exception as e:  # noqa: BLE001
        checks.append(DependencyStatus(name="data", ok=False, detail=str(e)[:120]))

    try:
        import httpx
        models = httpx.get(f"{settings.llm_base_url}/models", timeout=5.0)
        models.raise_for_status()
        checks.append(DependencyStatus(name="llm", ok=True, detail=settings.llm_model))
    except Exception as e:  # noqa: BLE001
        checks.append(DependencyStatus(name="llm", ok=False, detail=str(e)[:120]))

    body = HealthResponse(ok=all(c.ok for c in checks), dependencies=checks)
    return JSONResponse(
        status_code=status.HTTP_200_OK if body.ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=body.model_dump(),
    )


@app.get("/version")
def version() -> dict[str, Any]:
    """What this replica is running, for debugging a bad answer after the fact."""
    return {
        "version": app.version,
        "llm_model": settings.llm_model,
        "embed_model": settings.embed_model,
        "rerank_model": settings.rerank_model,
        "top_k": settings.top_k,
    }
