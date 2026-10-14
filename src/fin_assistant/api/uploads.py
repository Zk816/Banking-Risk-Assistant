import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from fin_assistant.api.errors import request_id_of
from fin_assistant.api.schemas import AnalyzeResponse, ErrorResponse
from fin_assistant.api.security import require_key
from fin_assistant.config import settings
from fin_assistant.monitoring.tracing import observe
from fin_assistant.tools.statement_analysis import AnalysisError, NotAStatementError, analyze_file, summarize
from fin_assistant.tools.table_profile import TableError, describe, profile

router = APIRouter()

ALLOWED_UPLOADS = (".xlsx", ".xls")


@router.post("/v1/analyze", response_model=AnalyzeResponse, dependencies=[Depends(require_key)])
def analyze(
    request: Request,
    file: UploadFile = File(...),
    question: str | None = Form(default=None, max_length=settings.max_question_chars),
) -> JSONResponse | AnalyzeResponse:
    """Analyse an uploaded financial statement (Excel): ratios, risk flags, written analysis.

    The file is streamed to a temporary folder with a size cap and deleted when the request ends.

    Raises:
        RuntimeError: if the model server is unreachable (handled as 503).
    """
    started = time.perf_counter()
    name = Path(file.filename or "upload").name
    if not name.lower().endswith(ALLOWED_UPLOADS):
        return _bad_file(request, "Поддерживаются только файлы Excel (.xlsx, .xls)")
    limit = settings.max_upload_mb * 1024 * 1024
    with tempfile.TemporaryDirectory(prefix="fin_assistant_upload_") as tmp:
        path = Path(tmp) / name
        size = 0
        with path.open("wb") as out:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    return _bad_file(request, f"Файл больше {settings.max_upload_mb} МБ")
                out.write(chunk)
        try:
            with observe(
                "POST /v1/analyze",
                input={"file": name, "question": question},
                metadata={"request_id": request_id_of(request)},
            ) as span:
                try:
                    report = summarize(analyze_file(path), question)
                except NotAStatementError:
                    table = describe(profile(path), question)
                    span.update(output={"kind": "table", "refused": bool(table.rejected_numbers)})
                    return AnalyzeResponse(
                        kind="table",
                        analysis=table.text,
                        periods=[],
                        mapped_rows={},
                        ratios=[],
                        risk_flags=[],
                        refused=bool(table.rejected_numbers),
                        request_id=request_id_of(request),
                        duration_ms=int((time.perf_counter() - started) * 1000),
                    )
                span.update(output={"flags": report.analysis.flags, "refused": bool(report.rejected_numbers)})
        except (AnalysisError, TableError) as e:
            return _bad_file(request, str(e))
    a = report.analysis
    return AnalyzeResponse(
        analysis=report.text,
        periods=a.periods,
        mapped_rows=a.mapped_labels,
        ratios=a.ratios,
        risk_flags=a.flags,
        refused=bool(report.rejected_numbers),
        request_id=request_id_of(request),
        duration_ms=int((time.perf_counter() - started) * 1000),
    )


def _bad_file(request: Request, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(
            code="bad_file", message=message, request_id=request_id_of(request)
        ).model_dump(),
    )
