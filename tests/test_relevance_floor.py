from types import SimpleNamespace

import pytest

from fin_assistant.tools import legal_text
from fin_assistant.tools.legal_text import MIN_RELEVANCE, search_legal_text


def fused(score: float, text: str = "Статья 16. Уставный капитал банка"):
    hit = SimpleNamespace(
        article_ref="Глава 2, Статья 16",
        document_title="Закон о банках",
        text=text,
        ordinal=3,
    )
    return SimpleNamespace(hit=hit, rerank_score=score)


@pytest.fixture
def retrieval(monkeypatch):
    def install(hits):
        monkeypatch.setattr(legal_text, "gather_candidates", lambda q: hits)
        monkeypatch.setattr(legal_text, "rerank", lambda q, c, top_k: c[:top_k])

    return install


def test_relevant_result_is_returned(retrieval) -> None:
    retrieval([fused(0.99)])
    result = search_legal_text("уставный капитал банка")
    assert result["found"]
    assert result["snippets"][0]["relevance"] == pytest.approx(0.99)


def test_irrelevant_result_is_withheld(retrieval) -> None:
    retrieval([fused(0.0414)])
    result = search_legal_text("уставный капитал страховой компании")
    assert not result["found"]
    assert result["best_relevance"] == pytest.approx(0.0414)


def test_withheld_result_tells_the_model_not_to_use_it(retrieval) -> None:
    retrieval([fused(0.0005)])
    assert "не используй" in search_legal_text("ставка НДС")["error"].lower()


def test_empty_retrieval_is_distinct_from_below_floor(retrieval) -> None:
    retrieval([])
    result = search_legal_text("что угодно")
    assert not result["found"]
    assert "best_relevance" not in result


def test_exactly_at_the_floor_passes(retrieval) -> None:
    retrieval([fused(MIN_RELEVANCE)])
    assert search_legal_text("граничный случай")["found"]


def test_just_below_the_floor_is_withheld(retrieval) -> None:
    retrieval([fused(MIN_RELEVANCE - 0.001)])
    assert not search_legal_text("граничный случай")["found"]


def test_floor_sits_between_the_measured_groups() -> None:
    assert 0.099 < MIN_RELEVANCE < 0.96
