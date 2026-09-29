"""Request and response shapes for the HTTP API.

Separate from the internal dataclasses on purpose: `ToolAnswer` carries things a
client has no business seeing (the model's rejected raw text), and the wire
format should be free to change without dragging the pipeline with it.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    """A question for the system."""

    question: str = Field(min_length=1, max_length=1000,
                          description="Вопрос на русском, казахском или английском")


class ToolCallInfo(BaseModel):
    """One tool the model used, so a client can show its working."""

    name: str
    arguments: dict[str, Any]
    ok: bool
    found: bool | None = None


class AskResponse(BaseModel):
    """An answer plus everything needed to judge whether to trust it."""

    answer: str
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
    """Per-dependency health. `ok` is false if any required one is down."""

    ok: bool
    dependencies: list[DependencyStatus]


class ErrorResponse(BaseModel):
    """One shape for every failure, so clients can branch on `code`."""

    code: Literal[
        "validation_error",
        "upstream_unavailable",
        "internal_error",
    ]
    message: str
    request_id: str
