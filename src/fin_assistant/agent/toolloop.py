import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from openai import APIConnectionError, APIStatusError

from fin_assistant.agent.citations import untraceable_numbers
from fin_assistant.agent.llm import get_client
from fin_assistant.agent.prompts import RETRY_PROMPT
from fin_assistant.config import settings
from fin_assistant.logging_setup import get_logger
from fin_assistant.monitoring.tracing import observe
from fin_assistant.tools.bank_metrics import TOOL_REGISTRY, TOOL_SCHEMAS

log = get_logger(__name__)

MAX_ROUNDS = 4


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    ok: bool


@dataclass
class ToolAnswer:
    text: str
    calls: list[ToolCall] = field(default_factory=list)
    rounds: int = 0
    hit_limit: bool = False
    fabricated: list[str] = field(default_factory=list)
    raw_text: str | None = None

    @property
    def grounded(self) -> bool:
        return any(c.ok and c.result.get("found", True) for c in self.calls)


def _run_tool(
    name: str, raw_arguments: str, registry: dict[str, Any] | None = None
) -> tuple[dict[str, Any], bool]:
    function = (registry if registry is not None else TOOL_REGISTRY).get(name)
    if function is None:
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
        return {"error": f"Неверные аргументы для {name}: {e}"}, False
    except Exception as e:
        log.exception("tool %s raised on arguments %r", name, arguments)
        return {"error": f"Инструмент {name} завершился ошибкой: {type(e).__name__}: {e}"}, False


def answer_with_tools(
    question: str,
    system: str,
    *,
    tools: list[dict[str, Any]] | None = None,
    registry: dict[str, Any] | None = None,
    max_rounds: int = MAX_ROUNDS,
    max_tokens: int = 1000,
    extra_body: dict[str, Any] | None = None,
    retries: int = 0,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    history: list[dict[str, str]] | None = None,
) -> ToolAnswer:
    question = question.strip()
    if not question:
        raise ValueError("question must not be empty")

    schemas = tools if tools is not None else TOOL_SCHEMAS
    functions = registry if registry is not None else TOOL_REGISTRY
    advertised = {t["function"]["name"] for t in schemas}
    missing = advertised - set(functions)
    if missing:
        raise ValueError(f"schemas advertise tools with no function behind them: {sorted(missing)}")

    history = history or []
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        *[{"role": t["role"], "content": t["content"]} for t in history],
        {"role": "user", "content": question},
    ]
    calls: list[ToolCall] = []
    retries_left = retries

    def emit(event: dict[str, Any]) -> None:
        if on_event:
            on_event(event)

    for round_number in range(1, max_rounds + 1):
        try:
            with observe(
                f"llm round {round_number}",
                as_type="generation",
                model=settings.llm_model,
                input=messages[-3:],
                model_parameters={"max_tokens": max_tokens, **(extra_body or {})},
            ) as gen:
                response = get_client().chat.completions.create(
                    model=settings.llm_model,
                    temperature=0.0,
                    max_tokens=max_tokens,
                    messages=messages,
                    tools=schemas,
                    extra_body=extra_body or {},
                )
                msg = response.choices[0].message
                usage = getattr(response, "usage", None)
                gen.update(
                    output={
                        "content": msg.content,
                        "tool_calls": [c.function.name for c in (msg.tool_calls or [])],
                    },
                    usage_details={"input": usage.prompt_tokens, "output": usage.completion_tokens}
                    if usage
                    else None,
                )
        except APIConnectionError as e:
            raise RuntimeError(f"Cannot reach the model server at {settings.llm_base_url}") from e
        except APIStatusError as e:
            raise RuntimeError(f"Model server returned {e.status_code}") from e

        message = response.choices[0].message
        tool_calls = message.tool_calls or []

        if not tool_calls:
            text = (message.content or "").strip()
            log.info("toolloop finished round=%d calls=%d", round_number, len(calls))
            verdict = _verify(
                text, calls, round_number, question + "\n" + "\n".join(t["content"] for t in history)
            )
            if verdict.fabricated and retries_left > 0 and round_number < max_rounds:
                retries_left -= 1
                log.info("toolloop retry: untraceable numbers %s", verdict.fabricated)
                emit({"type": "retry", "numbers": verdict.fabricated})
                messages.append({"role": "assistant", "content": text})
                messages.append(
                    {"role": "user", "content": RETRY_PROMPT.format(numbers=", ".join(verdict.fabricated))}
                )
                continue
            return verdict

        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.function.name, "arguments": c.function.arguments},
                    }
                    for c in tool_calls
                ],
            }
        )

        for call in tool_calls:
            emit(
                {
                    "type": "tool_start",
                    "name": call.function.name,
                    "arguments": _safe_args(call.function.arguments),
                }
            )
            with observe(
                call.function.name, as_type="tool", input=_safe_args(call.function.arguments)
            ) as tool_obs:
                result, ok = _run_tool(call.function.name, call.function.arguments, functions)
                tool_obs.update(output=result, level="DEFAULT" if ok else "ERROR")
            emit({"type": "tool_done", "name": call.function.name, "ok": ok, "found": result.get("found")})
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

    log.warning("toolloop hit max_rounds=%d without an answer", max_rounds)
    return ToolAnswer(
        text="Не удалось получить ответ: модель зациклилась на вызовах инструментов.",
        calls=calls,
        rounds=max_rounds,
        hit_limit=True,
    )


def _verify(text: str, calls: list[ToolCall], rounds: int, question: str = "") -> ToolAnswer:
    sources = [json.dumps(c.result, ensure_ascii=False, default=str) for c in calls] + [question]
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
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"_raw": raw}
    return parsed if isinstance(parsed, dict) else {"_raw": raw}
