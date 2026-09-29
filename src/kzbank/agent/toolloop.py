"""The tool-calling loop, written out by hand.

This is what LangChain's agent executor and the SDK tool runners do for you.
It is about forty lines, and seeing them is the point:

    1. send the question plus the tool schemas
    2. the model either answers, or asks for a tool
    3. run the tool, append the result, send everything back
    4. repeat until it answers or the budget runs out

The model never touches the database. It emits a name and JSON arguments; this
module decides whether that name is real and calls the function itself.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from openai import APIConnectionError, APIStatusError

from kzbank.agent.citations import untraceable_numbers
from kzbank.agent.llm import get_client
from kzbank.config import settings
from kzbank.logging_setup import get_logger
from kzbank.tools.bank_metrics import TOOL_REGISTRY, TOOL_SCHEMAS

log = get_logger(__name__)

# A model that keeps calling tools without concluding has to be stopped. Two
# rounds is enough for "check coverage, then fetch"; more means it is stuck.
MAX_ROUNDS = 4


@dataclass
class ToolCall:
    """One tool the model asked for, and what it got back."""

    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    ok: bool


@dataclass
class ToolAnswer:
    """Final text plus every tool call made to produce it."""

    text: str
    calls: list[ToolCall] = field(default_factory=list)
    rounds: int = 0
    hit_limit: bool = False
    # Numbers the model stated that no tool actually returned. Non-empty means
    # the answer was rejected and `text` is a refusal.
    fabricated: list[str] = field(default_factory=list)
    raw_text: str | None = None

    @property
    def grounded(self) -> bool:
        """True when at least one tool returned real data.

        An answer produced with no successful tool call came out of the model's
        weights, which for a numeric question is exactly what must not happen.
        """
        return any(c.ok and c.result.get("found", True) for c in self.calls)


def _run_tool(
    name: str, raw_arguments: str, registry: dict[str, Any] | None = None
) -> tuple[dict[str, Any], bool]:
    """Execute one tool call. Returns (result, ok).

    Never raises: a failure is handed back to the model as a result so it can
    react, rather than killing the request.
    """
    function = (registry if registry is not None else TOOL_REGISTRY).get(name)
    if function is None:
        # Small models do invent tool names. Say so plainly.
        return {"error": f"Инструмента '{name}' не существует"}, False

    try:
        arguments = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError as e:
        return {"error": f"Аргументы не являются корректным JSON: {e}"}, False

    if not isinstance(arguments, dict):
        return {"error": "Аргументы должны быть объектом JSON"}, False

    try:
        return function(**arguments), True
    except TypeError as e:
        # Wrong or missing argument names — the model's mistake, not a crash.
        return {"error": f"Неверные аргументы для {name}: {e}"}, False
    except Exception as e:  # noqa: BLE001 — see below
        # A tool runs on arguments the model invented, so it can fail in ways no
        # signature check catches: observed in the wild, the model passed
        # currency={"type":"string"} and the tool died on .strip() with an
        # AttributeError. One bad call must not kill the request — it becomes a
        # result the model can react to.
        #
        # This is the one broad catch in the codebase. It is logged with the
        # traceback so a genuine bug in a tool is still diagnosable, rather than
        # disappearing into a polite message.
        log.exception("tool %s raised on arguments %r", name, arguments)
        return {"error": f"Инструмент {name} завершился ошибкой: {type(e).__name__}: {e}"}, False


def answer_with_tools(
    question: str,
    system: str,
    *,
    tools: list[dict[str, Any]] | None = None,
    registry: dict[str, Any] | None = None,
) -> ToolAnswer:
    """Let the model query the metrics tables, then answer.

    `tools` and `registry` default to the metrics tools. The router passes the
    full set; they are always passed together, since a schema with no function
    behind it is a tool the model can call and we cannot run.

    Raises:
        ValueError: if the question is empty, or tools and registry disagree.
        RuntimeError: if the model server is unreachable.
    """
    question = question.strip()
    if not question:
        raise ValueError("question must not be empty")

    schemas = tools if tools is not None else TOOL_SCHEMAS
    functions = registry if registry is not None else TOOL_REGISTRY
    advertised = {t["function"]["name"] for t in schemas}
    missing = advertised - set(functions)
    if missing:
        raise ValueError(f"schemas advertise tools with no function behind them: {sorted(missing)}")

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]
    calls: list[ToolCall] = []

    for round_number in range(1, MAX_ROUNDS + 1):
        try:
            response = get_client().chat.completions.create(
                model=settings.llm_model,
                temperature=0.0,
                max_tokens=1000,
                messages=messages,
                tools=schemas,
            )
        except APIConnectionError as e:
            raise RuntimeError(
                f"Cannot reach the model server at {settings.llm_base_url}"
            ) from e
        except APIStatusError as e:
            raise RuntimeError(f"Model server returned {e.status_code}") from e

        message = response.choices[0].message
        tool_calls = message.tool_calls or []

        if not tool_calls:
            text = (message.content or "").strip()
            log.info("toolloop finished round=%d calls=%d", round_number, len(calls))
            return _verify(text, calls, round_number)

        # Echo the assistant turn back verbatim; the protocol requires every
        # tool_call_id to be answered in the next message.
        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.function.name,
                                     "arguments": c.function.arguments},
                    }
                    for c in tool_calls
                ],
            }
        )

        for call in tool_calls:
            result, ok = _run_tool(call.function.name, call.function.arguments, functions)
            calls.append(
                ToolCall(
                    name=call.function.name,
                    arguments=_safe_args(call.function.arguments),
                    result=result,
                    ok=ok,
                )
            )
            log.info("toolloop call=%s ok=%s", call.function.name, ok)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                }
            )

    log.warning("toolloop hit MAX_ROUNDS=%d without an answer", MAX_ROUNDS)
    return ToolAnswer(
        text="Не удалось получить ответ: модель зациклилась на вызовах инструментов.",
        calls=calls,
        rounds=MAX_ROUNDS,
        hit_limit=True,
    )


def _verify(text: str, calls: list[ToolCall], rounds: int) -> ToolAnswer:
    """Refuse an answer stating a number no tool returned.

    The model is handed the exact figure as JSON and still rewrites it: asked for
    the euro rate it said 413,46 where the tool returned 513,46, three runs out
    of three at temperature 0. A wrong rate reads exactly like a right one, so
    the answer is withheld rather than shown with a warning.
    """
    sources = [json.dumps(c.result, ensure_ascii=False, default=str) for c in calls]
    bad = untraceable_numbers(text, sources)
    if not bad:
        return ToolAnswer(text=text, calls=calls, rounds=rounds, raw_text=text)

    log.warning("toolloop rejected answer, fabricated numbers: %s", bad)
    return ToolAnswer(
        text=f"Ответ отклонён: числа {bad} не возвращал ни один инструмент.",
        calls=calls,
        rounds=rounds,
        fabricated=bad,
        raw_text=text,
    )


def _safe_args(raw: str) -> dict[str, Any]:
    """Arguments for logging and display; never raises on malformed JSON."""
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"_raw": raw}
    return parsed if isinstance(parsed, dict) else {"_raw": raw}
