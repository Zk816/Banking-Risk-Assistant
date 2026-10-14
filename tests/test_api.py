import pytest
from fastapi.testclient import TestClient

from fin_assistant.agent.assistant import AssistantAnswer
from fin_assistant.agent.toolloop import ToolAnswer, ToolCall
from fin_assistant.api import main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "get_model", lambda: None)
    monkeypatch.setattr(main, "get_reranker", lambda: None)
    from fin_assistant.config import settings

    monkeypatch.setattr(settings, "api_keys", "")
    with TestClient(main.app, raise_server_exceptions=False) as c:
        yield c


def stub_answer(monkeypatch, result):
    answer = AssistantAnswer(text=result.text, calls=result.calls, rejected_numbers=result.fabricated)
    monkeypatch.setattr(main, "assistant_ask", lambda q, **kw: answer)


def good() -> ToolAnswer:
    call = ToolCall(
        name="get_exchange_rate",
        arguments={"currency": "EUR"},
        result={"found": True, "value": 513.46},
        ok=True,
    )
    return ToolAnswer(text="513,46 тенге за 1 EUR", calls=[call], rounds=2)


def test_ask_returns_the_answer(client, monkeypatch) -> None:
    stub_answer(monkeypatch, good())
    body = client.post("/v1/ask", json={"question": "Какой курс евро?"}).json()
    assert body["answer"] == "513,46 тенге за 1 EUR"
    assert body["grounded"] is True
    assert body["refused"] is False


def test_response_shows_which_tools_ran(client, monkeypatch) -> None:
    stub_answer(monkeypatch, good())
    tools = client.post("/v1/ask", json={"question": "q"}).json()["tools_used"]
    assert tools == [
        {"name": "get_exchange_rate", "arguments": {"currency": "EUR"}, "ok": True, "found": True}
    ]


def test_rejected_answer_is_marked_refused(client, monkeypatch) -> None:
    stub_answer(
        monkeypatch,
        ToolAnswer(
            text="Ответ отклонён: числа ['413.46'] не возвращал ни один инструмент.",
            calls=[],
            rounds=2,
            fabricated=["413.46"],
            raw_text="413.46 тенге",
        ),
    )
    body = client.post("/v1/ask", json={"question": "q"}).json()
    assert body["refused"] is True


def test_model_raw_text_never_leaves_the_process(client, monkeypatch) -> None:
    stub_answer(
        monkeypatch,
        ToolAnswer(
            text="Ответ отклонён.",
            calls=[],
            rounds=2,
            fabricated=["413.46"],
            raw_text="413.46 тенге за 1 EUR",
        ),
    )
    assert "413.46" not in client.post("/v1/ask", json={"question": "q"}).text


@pytest.mark.parametrize(
    "payload",
    [{"question": ""}, {"question": "а" * 20001}, {}, {"question": None}],
    ids=["empty", "too-long", "missing", "null"],
)
def test_bad_request_is_422_with_a_code(client, payload) -> None:
    response = client.post("/v1/ask", json=payload)
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_unreachable_upstream_is_503_not_500(client, monkeypatch) -> None:
    def boom(q, **kw):
        raise RuntimeError("Cannot reach the model server")

    monkeypatch.setattr(main, "assistant_ask", boom)

    response = client.post("/v1/ask", json={"question": "q"})
    assert response.status_code == 503
    assert response.json()["code"] == "upstream_unavailable"


def test_unexpected_error_is_500_without_internals(client, monkeypatch) -> None:
    def boom(q, **kw):
        raise KeyError("secret_table.column")

    monkeypatch.setattr(main, "assistant_ask", boom)

    response = client.post("/v1/ask", json={"question": "q"})
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "secret_table" not in response.text


def test_every_error_carries_a_request_id(client, monkeypatch) -> None:
    def boom(q, **kw):
        raise RuntimeError("down")

    monkeypatch.setattr(main, "assistant_ask", boom)
    assert client.post("/v1/ask", json={"question": "q"}).json()["request_id"]


def test_request_id_is_returned(client, monkeypatch) -> None:
    stub_answer(monkeypatch, good())
    response = client.post("/v1/ask", json={"question": "q"})
    assert response.headers["x-request-id"] == response.json()["request_id"]


def test_client_request_id_is_honoured(client, monkeypatch) -> None:
    stub_answer(monkeypatch, good())
    response = client.post("/v1/ask", json={"question": "q"}, headers={"X-Request-Id": "trace-42"})
    assert response.headers["x-request-id"] == "trace-42"


def test_healthz_is_200_when_everything_is_up(client, monkeypatch) -> None:
    monkeypatch.setattr(main, "table_counts", lambda: {"chunks": 10, "metrics": 48})
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_healthz_is_503_when_a_dependency_is_down(client, monkeypatch) -> None:
    def boom():
        raise OSError("connection refused")

    monkeypatch.setattr(main, "table_counts", boom)

    response = client.get("/healthz")
    assert response.status_code == 503
    postgres = next(d for d in response.json()["dependencies"] if d["name"] == "postgres")
    assert postgres["ok"] is False


def test_healthz_covers_storage_data_and_model(client, monkeypatch) -> None:
    monkeypatch.setattr(main, "table_counts", lambda: {"chunks": 1, "metrics": 1})
    names = {d["name"] for d in client.get("/healthz").json()["dependencies"]}
    assert "postgres" in names
    assert "llm" in names
    assert any(n.startswith("data:") for n in names)


def test_stale_data_makes_the_replica_unhealthy(client, monkeypatch) -> None:
    from datetime import date, datetime, timedelta, timezone

    from fin_assistant.storage.freshness import SourceFreshness

    monkeypatch.setattr(main, "table_counts", lambda: {"chunks": 1, "metrics": 1})
    monkeypatch.setattr(
        main,
        "check_sources",
        lambda: [
            SourceFreshness(
                source="nbk",
                latest_published=date.today() - timedelta(days=90),
                latest_retrieved=datetime.now(timezone.utc),
                documents=1,
                kind="series",
                max_age_days=2,
            )
        ],
    )

    response = client.get("/healthz")
    assert response.status_code == 503
    nbk = next(d for d in response.json()["dependencies"] if d["name"] == "data:nbk")
    assert nbk["ok"] is False


def test_version_reports_the_models(client) -> None:
    body = client.get("/version").json()
    assert body["llm_model"] and body["embed_model"] and body["rerank_model"]


def test_stream_sends_progress_then_the_checked_answer(client, monkeypatch) -> None:
    def fake_ask(question, on_event=None, **kw):
        on_event({"type": "tool_start", "name": "get_exchange_rate", "arguments": {"currency": "EUR"}})
        on_event({"type": "tool_done", "name": "get_exchange_rate", "ok": True, "found": True})
        return AssistantAnswer(text=good().text, calls=good().calls)

    monkeypatch.setattr(main, "assistant_ask", fake_ask)
    import json

    events = [
        json.loads(line[6:])
        for line in client.post("/v1/ask/stream", json={"question": "q"}).text.splitlines()
        if line.startswith("data: ")
    ]
    assert [e["type"] for e in events] == ["tool_start", "tool_done", "answer"]
    assert events[-1]["body"]["answer"] == "513,46 тенге за 1 EUR"


def test_key_required_when_keys_are_configured(client, monkeypatch) -> None:
    from fin_assistant.config import settings

    monkeypatch.setattr(settings, "api_keys", "k-good")
    stub_answer(monkeypatch, good())
    assert client.post("/v1/ask", json={"question": "q"}).json()["code"] == "unauthorized"
    assert client.post("/v1/ask", json={"question": "q"}, headers={"X-API-Key": "k-bad"}).status_code == 401
    assert client.post("/v1/ask", json={"question": "q"}, headers={"X-API-Key": "k-good"}).status_code == 200
    assert client.get("/version").status_code == 200


def test_rate_limit_is_429_with_retry_after(client, monkeypatch) -> None:
    from fin_assistant.api import security
    from fin_assistant.config import settings

    monkeypatch.setattr(settings, "api_keys", "k-limited")
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
    security._calls.clear()
    stub_answer(monkeypatch, good())
    h = {"X-API-Key": "k-limited"}
    codes = [client.post("/v1/ask", json={"question": "q"}, headers=h).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    r = client.post("/v1/ask", json={"question": "q"}, headers=h)
    assert r.json()["code"] == "rate_limited" and int(r.headers["Retry-After"]) > 0


def test_numbers_from_the_question_are_not_treated_as_invented() -> None:
    from fin_assistant.agent.toolloop import _verify

    pasted = "Выручка 4 200 000, прибыль 160 000. Какая маржа?"
    assert not _verify("Выручка 4 200 000, прибыль 160 000.", [], 1, pasted).fabricated
    assert _verify("Маржа 3,81%", [], 1, pasted).fabricated == ["3,81"]


def test_history_reaches_the_assistant(client, monkeypatch) -> None:
    seen = {}

    def fake_ask(question, on_event=None, history=None):
        seen["history"] = history
        return AssistantAnswer(text=good().text, calls=good().calls)

    monkeypatch.setattr(main, "assistant_ask", fake_ask)
    turns = [{"role": "user", "content": "k2 у Kaspi?"}, {"role": "assistant", "content": "14,30%"}]
    assert client.post("/v1/ask", json={"question": "а у Halyk?", "history": turns}).status_code == 200
    assert seen["history"] == turns
