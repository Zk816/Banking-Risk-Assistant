import pytest

from fin_assistant.agent import answer as answer_module
from fin_assistant.retrieval.hybrid import FusedHit
from fin_assistant.retrieval.vector_search import Hit

SOURCE_TEXT = (
    "Статья 43. Коэффициент достаточности собственного капитала k2 устанавливается в размере не менее 0,080."
)


def _fused(text: str = SOURCE_TEXT) -> FusedHit:
    hit = Hit(
        chunk_id=1,
        document_id=1,
        document_title="Закон о банках",
        document_url=None,
        document_edition="01.01.2026",
        ordinal=6,
        article_ref="Глава 3, Статья 43",
        text=text,
        similarity=0.55,
    )
    return FusedHit(hit=hit, rrf_score=0.03, vector_rank=1, keyword_rank=1, rerank_score=0.63)


@pytest.fixture
def pipeline(monkeypatch):
    def install(reply: str, *, hits: list[FusedHit] | None = None) -> None:
        candidates = _fused() if hits is None else None
        monkeypatch.setattr(
            answer_module,
            "gather_candidates",
            lambda q: hits if hits is not None else [candidates],
        )
        monkeypatch.setattr(
            answer_module,
            "rerank",
            lambda q, c, top_k: c[:top_k],
        )
        monkeypatch.setattr(answer_module, "chat", lambda system, user: reply)

    return install


def test_good_answer_is_returned(pipeline) -> None:
    pipeline("0,080 [1]")
    result = answer_module.answer_question("сколько k2?")
    assert not result.refused
    assert result.text == "0,080 [1]"
    assert result.report is not None and result.report.ok


def test_rounded_number_is_refused(pipeline) -> None:
    pipeline("0,08 [1]")
    result = answer_module.answer_question("сколько k2?")
    assert result.refused
    assert "0,08" in result.text
    assert result.raw_text == "0,08 [1]"


def test_fabricated_source_is_refused(pipeline) -> None:
    pipeline("0,080 согласно [7].")
    result = answer_module.answer_question("сколько k2?")
    assert result.refused
    assert "несуществующие источники" in result.text


def test_uncited_answer_is_refused(pipeline) -> None:
    pipeline("Значение равно 0,080.")
    result = answer_module.answer_question("сколько k2?")
    assert result.refused


def test_invented_number_is_refused(pipeline) -> None:
    pipeline("Значение составляет 0,065 [1].")
    result = answer_module.answer_question("сколько k2?")
    assert result.refused


def test_model_refusal_is_passed_through_unchecked(pipeline) -> None:
    pipeline("НЕТ ДАННЫХ В ИСТОЧНИКАХ")
    result = answer_module.answer_question("ставка НДС?")
    assert result.refused
    assert "не прошёл проверку" not in result.text


def test_empty_retrieval_never_calls_the_model(monkeypatch) -> None:
    called = []
    monkeypatch.setattr(answer_module, "gather_candidates", lambda q: [])
    monkeypatch.setattr(answer_module, "rerank", lambda q, c, top_k: [])
    monkeypatch.setattr(answer_module, "chat", lambda system, user: called.append(1) or "whatever")

    result = answer_module.answer_question("что угодно")
    assert result.refused
    assert called == []


def test_rejected_answer_keeps_the_original_for_inspection(pipeline) -> None:
    pipeline("0,08 [1]")
    result = answer_module.answer_question("сколько k2?")
    assert result.raw_text != result.text
    assert result.report is not None
    assert result.report.untraceable_numbers == ["0,08"]
