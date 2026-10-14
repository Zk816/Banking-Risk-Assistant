from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from fin_assistant.agent.prompts import ASSISTANT_SYSTEM
from fin_assistant.agent.toolloop import ToolCall, answer_with_tools
from fin_assistant.config import settings
from fin_assistant.monitoring.tracing import observe
from fin_assistant.tools.bank_metrics import TOOL_REGISTRY as FX_REGISTRY
from fin_assistant.tools.bank_metrics import TOOL_SCHEMAS as FX_SCHEMAS
from fin_assistant.tools.kz_banks import TOOL_REGISTRY, TOOL_SCHEMAS
from fin_assistant.tools.web import TOOL_REGISTRY as WEB_REGISTRY
from fin_assistant.tools.web import TOOL_SCHEMAS as WEB_SCHEMAS

MAX_ROUNDS = 12
MAX_TOKENS = 8000
REASONING = {"reasoning_effort": "medium"}

SCHEMAS = TOOL_SCHEMAS + [s for s in FX_SCHEMAS if s["function"]["name"] == "get_exchange_rate"]
REGISTRY = {**TOOL_REGISTRY, "get_exchange_rate": FX_REGISTRY["get_exchange_rate"]}
if settings.searxng_url:
    SCHEMAS = SCHEMAS + WEB_SCHEMAS
    REGISTRY = {**REGISTRY, **WEB_REGISTRY}


@dataclass
class AssistantAnswer:
    text: str
    calls: list[ToolCall] = field(default_factory=list)
    rejected_numbers: list[str] = field(default_factory=list)

    @property
    def sources(self) -> list[str]:
        seen: list[str] = []
        for c in self.calls:
            for s in _sources_of(c.result):
                if s not in seen:
                    seen.append(s)
        return seen


def _sources_of(result: dict[str, Any]) -> list[str]:
    out = [result["source"].split(" ")[0]] if isinstance(result.get("source"), str) else []
    out += [p["dataset"] for p in result.get("passages", []) if p.get("dataset")]
    return out


def ask(
    question: str,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    history: list[dict[str, str]] | None = None,
) -> AssistantAnswer:
    with observe("assistant", as_type="agent", input=question) as span:
        result = _run(question, on_event, history or [])
        answer = AssistantAnswer(text=result.text, calls=result.calls, rejected_numbers=result.fabricated)
        span.update(
            output=answer.text,
            metadata={
                "sources": answer.sources,
                "tools": [c.name for c in answer.calls],
                "rejected_numbers": answer.rejected_numbers,
            },
            level="WARNING" if answer.rejected_numbers else "DEFAULT",
        )
    return answer


def _run(question: str, on_event: Callable[[dict[str, Any]], None] | None, history: list[dict[str, str]]):
    return answer_with_tools(
        question,
        ASSISTANT_SYSTEM.format(today=date.today().isoformat()),
        tools=SCHEMAS,
        registry=REGISTRY,
        max_rounds=MAX_ROUNDS,
        max_tokens=MAX_TOKENS,
        extra_body=REASONING,
        retries=1,
        on_event=on_event,
        history=history,
    )
