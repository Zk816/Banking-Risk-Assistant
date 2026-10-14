import time
import uuid

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from fin_assistant.api.schemas import ErrorResponse
from fin_assistant.logging_setup import get_logger

log = get_logger(__name__)


async def add_request_id(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    started = time.perf_counter()

    response = await call_next(request)

    duration_ms = int((time.perf_counter() - started) * 1000)
    response.headers["x-request-id"] = request_id
    log.info(
        "%s %s -> %d %dms rid=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        request_id,
    )
    return response


def request_id_of(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


async def on_validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=ErrorResponse(
            code="validation_error",
            message="; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()),
            request_id=request_id_of(request),
        ).model_dump(),
    )


async def on_http_error(request: Request, exc: HTTPException):
    code = {401: "unauthorized", 429: "rate_limited", 422: "validation_error"}.get(
        exc.status_code, "internal_error"
    )
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content=ErrorResponse(
            code=code, message=str(exc.detail), request_id=request_id_of(request)
        ).model_dump(),
    )


async def on_upstream_error(request: Request, exc: RuntimeError):
    request_id = request_id_of(request)
    log.error("upstream unavailable rid=%s: %s", request_id, exc)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=ErrorResponse(
            code="upstream_unavailable",
            message=str(exc),
            request_id=request_id,
        ).model_dump(),
    )


async def on_unexpected_error(request: Request, exc: Exception):
    request_id = request_id_of(request)
    log.exception("unhandled error rid=%s", request_id)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            code="internal_error",
            message="Внутренняя ошибка. Сообщите request_id.",
            request_id=request_id,
        ).model_dump(),
    )


def register(app: FastAPI) -> None:
    app.middleware("http")(add_request_id)
    app.add_exception_handler(RequestValidationError, on_validation_error)
    app.add_exception_handler(HTTPException, on_http_error)
    app.add_exception_handler(RuntimeError, on_upstream_error)
    app.add_exception_handler(Exception, on_unexpected_error)
