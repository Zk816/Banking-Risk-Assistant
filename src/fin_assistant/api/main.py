import json
import queue
import threading
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from fin_assistant.agent.assistant import ask as assistant_ask
from fin_assistant.agent.router import answer as route_answer
from fin_assistant.api import errors, uploads
from fin_assistant.api.errors import request_id_of
from fin_assistant.api.schemas import (
    AskRequest,
    AskResponse,
    Citation,
    DependencyStatus,
    HealthResponse,
    ToolCallInfo,
)
from fin_assistant.api.security import require_key
from fin_assistant.config import settings
from fin_assistant.logging_setup import get_logger, setup_logging
from fin_assistant.monitoring.tracing import flush as flush_traces
from fin_assistant.monitoring.tracing import observe
from fin_assistant.retrieval.embed import get_model
from fin_assistant.retrieval.rerank import get_reranker
from fin_assistant.storage.db import table_counts
from fin_assistant.storage.freshness import check_sources

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    started = time.perf_counter()
    get_model()
    get_reranker()
    log.info("models loaded in %.1fs", time.perf_counter() - started)
    yield
    flush_traces()


app = FastAPI(
    title="fin-assistant",
    description="Цитируемые ответы по банковскому законодательству и курсам валют.",
    version="0.1.0",
    lifespan=lifespan,
)


errors.register(app)
app.include_router(uploads.router)

STREAM_TIMEOUT_S = 600


@app.post("/v1/ask", response_model=AskResponse, dependencies=[Depends(require_key)])
def ask(body: AskRequest, request: Request) -> AskResponse:
    """Answer a question with the risk assistant (Qwen3.8 + tools over every dataset).

    Raises:
        RuntimeError: if the model server is unreachable (handled as 503).
    """
    started = time.perf_counter()
    with observe("POST /v1/ask", metadata={"request_id": request_id_of(request)}):
        return _to_response(assistant_ask(body.question, history=_history(body)), request, started)


@app.post("/v1/ask/stream", dependencies=[Depends(require_key)])
def ask_stream(body: AskRequest, request: Request) -> StreamingResponse:
    """Same answer as /v1/ask, plus progress while it is being worked out (Server-Sent Events).

    Events: tool_start / tool_done for every lookup, retry if the number guard sent the model
    back, then one final "answer" event carrying the full AskResponse. The answer text is only
    sent after the guard has checked it, so a streamed answer is as safe as a plain one.
    """
    started = time.perf_counter()
    events: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=256)

    def work() -> None:
        try:
            with observe("POST /v1/ask/stream", metadata={"request_id": request_id_of(request)}):
                result = assistant_ask(body.question, on_event=events.put, history=_history(body))
            events.put({"type": "answer", "body": _to_response(result, request, started).model_dump()})
        except Exception as e:
            log.exception("stream request failed")
            events.put({"type": "error", "message": str(e)[:300], "request_id": request_id_of(request)})
        finally:
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def sse():
        while (event := events.get(timeout=STREAM_TIMEOUT_S)) is not None:
            yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Request-Id": request_id_of(request)},
    )


def _history(body: AskRequest) -> list[dict[str, str]]:
    return [t.model_dump() for t in body.history]


def _to_response(result, request: Request, started: float) -> AskResponse:
    citations = [
        Citation(
            citation=p["citation"], dataset=p.get("dataset"), date=p.get("date"), relevance=p.get("relevance")
        )
        for c in result.calls
        for p in c.result.get("passages", [])
    ]
    return AskResponse(
        answer=result.text,
        sources=result.sources,
        citations=citations,
        grounded=any(c.ok and c.result.get("found", True) for c in result.calls),
        refused=bool(result.rejected_numbers),
        tools_used=[
            ToolCallInfo(name=c.name, arguments=c.arguments, ok=c.ok, found=c.result.get("found"))
            for c in result.calls
        ],
        request_id=request_id_of(request),
        duration_ms=int((time.perf_counter() - started) * 1000),
    )


@app.post("/v1/ask/legacy", response_model=AskResponse, deprecated=True, dependencies=[Depends(require_key)])
def ask_legacy(body: AskRequest, request: Request) -> AskResponse:
    """The earlier keyword router (exchange rates, US filings, legal text). Kept for comparison.

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
            ToolCallInfo(name=c.name, arguments=c.arguments, ok=c.ok, found=c.result.get("found"))
            for c in result.calls
        ],
        request_id=request_id_of(request),
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
        checks.append(
            DependencyStatus(
                name="postgres", ok=True, detail=f"chunks={counts['chunks']} metrics={counts['metrics']}"
            )
        )
    except Exception as e:
        checks.append(DependencyStatus(name="postgres", ok=False, detail=str(e)[:120]))

    try:
        for source in check_sources():
            checks.append(
                DependencyStatus(name=f"data:{source.source}", ok=not source.stale, detail=source.detail)
            )
    except Exception as e:
        checks.append(DependencyStatus(name="data", ok=False, detail=str(e)[:120]))

    try:
        import httpx

        models = httpx.get(f"{settings.llm_base_url}/models", timeout=5.0)
        models.raise_for_status()
        checks.append(DependencyStatus(name="llm", ok=True, detail=settings.llm_model))
    except Exception as e:
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
