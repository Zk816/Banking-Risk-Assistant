from types import SimpleNamespace

import pytest

from fin_assistant.agent import toolloop
from fin_assistant.agent.toolloop import MAX_ROUNDS, ToolCall, _run_tool, _verify


def tool_call(name: str, arguments: str, call_id: str = "c1"):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


def reply(*, content: str = "", calls=None):
    message = SimpleNamespace(content=content, tool_calls=calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class StubClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.sent.append(kwargs)
                return outer.replies.pop(0)

        self.chat = SimpleNamespace(completions=_Completions())


def schemas_for(registry):
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": name,
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in registry
    ]


@pytest.fixture
def stub(monkeypatch):
    def install(replies, tools=None):
        client = StubClient(replies)
        monkeypatch.setattr(toolloop, "get_client", lambda: client)
        if tools is not None:
            monkeypatch.setattr(toolloop, "TOOL_REGISTRY", tools)
            monkeypatch.setattr(toolloop, "TOOL_SCHEMAS", schemas_for(tools))
        return client

    return install


def test_unknown_tool_name_is_reported_not_raised(monkeypatch) -> None:
    monkeypatch.setattr(toolloop, "TOOL_REGISTRY", {})
    result, ok = _run_tool("no_such_tool", "{}")
    assert not ok
    assert "не существует" in result["error"]


def test_malformed_arguments_are_reported(monkeypatch) -> None:
    monkeypatch.setattr(toolloop, "TOOL_REGISTRY", {"f": lambda **k: {}})
    result, ok = _run_tool("f", "{not json")
    assert not ok
    assert "JSON" in result["error"]


def test_wrong_argument_names_are_reported(monkeypatch) -> None:
    monkeypatch.setattr(toolloop, "TOOL_REGISTRY", {"f": lambda currency: {}})
    result, ok = _run_tool("f", '{"wrong": 1}')
    assert not ok
    assert "Неверные аргументы" in result["error"]


def test_successful_call_returns_the_payload(monkeypatch) -> None:
    monkeypatch.setattr(toolloop, "TOOL_REGISTRY", {"f": lambda currency: {"found": True, "value": 1.5}})
    result, ok = _run_tool("f", '{"currency": "USD"}')
    assert ok and result["value"] == 1.5


def test_answer_without_tools_returns_immediately(stub) -> None:
    stub([reply(content="Данных нет.")])
    answer = toolloop.answer_with_tools("вопрос", "system")
    assert answer.text == "Данных нет."
    assert answer.rounds == 1
    assert not answer.grounded


def test_tool_result_is_fed_back_to_the_model(stub) -> None:
    client = stub(
        [reply(calls=[tool_call("f", '{"currency":"USD"}')]), reply(content="447.85 тенге")],
        tools={"f": lambda currency: {"found": True, "value": 447.85}},
    )
    answer = toolloop.answer_with_tools("курс?", "system")
    assert answer.grounded
    assert answer.rounds == 2

    roles = [m["role"] for m in client.sent[1]["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]


def test_tool_call_id_is_echoed_back(stub) -> None:
    client = stub(
        [reply(calls=[tool_call("f", "{}", call_id="abc123")]), reply(content="ok")],
        tools={"f": lambda: {"found": True}},
    )
    toolloop.answer_with_tools("q", "system")
    tool_message = client.sent[1]["messages"][-1]
    assert tool_message["tool_call_id"] == "abc123"


def test_loop_stops_at_max_rounds(stub) -> None:
    stub(
        [reply(calls=[tool_call("f", "{}")]) for _ in range(MAX_ROUNDS)], tools={"f": lambda: {"found": True}}
    )
    answer = toolloop.answer_with_tools("q", "system")
    assert answer.hit_limit
    assert answer.rounds == MAX_ROUNDS


def test_failed_tool_does_not_count_as_grounded(stub) -> None:
    stub([reply(calls=[tool_call("missing", "{}")]), reply(content="Данных нет.")], tools={})
    assert not toolloop.answer_with_tools("q", "system").grounded


def test_not_found_result_is_not_grounding(stub) -> None:
    stub(
        [reply(calls=[tool_call("f", "{}")]), reply(content="Данных нет.")],
        tools={"f": lambda: {"found": False, "error": "нет"}},
    )
    assert not toolloop.answer_with_tools("q", "system").grounded


def test_empty_question_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        toolloop.answer_with_tools("   ", "system")


def call_with(value: float) -> ToolCall:
    return ToolCall(
        name="get_exchange_rate",
        arguments={"currency": "EUR"},
        result={"found": True, "value": value, "unit": "KZT per 1 EUR"},
        ok=True,
    )


def test_number_the_tool_returned_is_accepted() -> None:
    assert not _verify("513,46 тенге за 1 EUR", [call_with(513.46)], 2).fabricated


def test_comma_and_dot_are_the_same_number() -> None:
    assert not _verify("513.46 тенге", [call_with(513.46)], 2).fabricated


def test_a_digit_the_model_changed_is_caught() -> None:
    answer = _verify("413,46 тенге за 1 EUR", [call_with(513.46)], 2)
    assert answer.fabricated == ["413,46"]
    assert "отклонён" in answer.text


def test_rejected_answer_keeps_the_model_text() -> None:
    answer = _verify("413,46 тенге", [call_with(513.46)], 2)
    assert answer.raw_text == "413,46 тенге"
    assert answer.raw_text != answer.text


def test_error_quotes_the_spelling_the_model_used() -> None:
    assert _verify("413,46 тенге", [call_with(513.46)], 2).fabricated == ["413,46"]


def test_answer_with_no_numbers_passes() -> None:
    assert not _verify("Данных нет.", [], 1).fabricated


def test_schema_without_a_function_is_rejected() -> None:
    orphan = [
        {
            "type": "function",
            "function": {
                "name": "ghost",
                "description": "x",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    with pytest.raises(ValueError, match="no function behind them"):
        toolloop.answer_with_tools("q", "system", tools=orphan, registry={})


def test_extra_functions_without_schemas_are_allowed() -> None:
    from fin_assistant.agent import toolloop as tl

    result, ok = tl._run_tool("hidden", "{}", {"hidden": lambda: {"found": True}})
    assert ok and result["found"]
