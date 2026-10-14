from typing import Any, Literal

from pydantic import BaseModel, Field

from fin_assistant.config import settings


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class AskRequest(BaseModel):
    history: list[Turn] = Field(
        default_factory=list,
        max_length=10,
        description="Предыдущие сообщения диалога, чтобы понимать уточняющие вопросы",
    )

    question: str = Field(
        min_length=1,
        max_length=settings.max_question_chars,
        description="Вопрос на русском, казахском или английском",
    )


class ToolCallInfo(BaseModel):
    name: str
    arguments: dict[str, Any]
    ok: bool
    found: bool | None = None


class Citation(BaseModel):
    citation: str
    dataset: str | None = None
    date: str | None = None
    relevance: float | None = None


class AskResponse(BaseModel):
    answer: str
    sources: list[str] = Field(default_factory=list, description="Датасеты, из которых взяты факты")
    citations: list[Citation] = Field(default_factory=list)
    grounded: bool = Field(description="Хотя бы один инструмент вернул данные")
    refused: bool = Field(description="Система отказалась отвечать")
    tools_used: list[ToolCallInfo]
    request_id: str
    duration_ms: int


class DependencyStatus(BaseModel):
    name: str
    ok: bool
    detail: str


class HealthResponse(BaseModel):
    ok: bool
    dependencies: list[DependencyStatus]


class ErrorResponse(BaseModel):
    code: Literal[
        "validation_error",
        "bad_file",
        "unauthorized",
        "rate_limited",
        "upstream_unavailable",
        "internal_error",
    ]
    message: str
    request_id: str


class AnalyzeResponse(BaseModel):
    kind: Literal["statement", "table"] = Field(
        default="statement",
        description="statement: финансовая отчетность с коэффициентами; table: любая другая таблица",
    )
    analysis: str
    periods: list[str]
    mapped_rows: dict[str, str] = Field(description="Стандартная статья -> строка из файла")
    ratios: list[dict[str, Any]]
    risk_flags: list[str]
    refused: bool
    request_id: str
    duration_ms: int
