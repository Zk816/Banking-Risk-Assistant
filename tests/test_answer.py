import pytest

from fin_assistant.agent.answer import Answer
from fin_assistant.retrieval.vector_search import Hit


def make_hit(n: int, title: str = "Закон о банках", article: str | None = None) -> Hit:
    return Hit(
        chunk_id=n,
        document_id=1,
        document_title=title,
        document_url=None,
        document_edition=None,
        ordinal=n,
        article_ref=article,
        text=f"текст фрагмента {n}",
        similarity=0.9 - n / 100,
    )


def make_answer(text: str, n_hits: int = 3) -> Answer:
    return Answer(
        question="вопрос",
        text=text,
        hits=[make_hit(i) for i in range(n_hits)],
        refused=False,
    )


def test_only_cited_sources_are_reported() -> None:
    answer = make_answer("Ответ взят из источника [2].", n_hits=3)
    assert [marker for marker, _ in answer.cited] == [2]


def test_repeated_marker_is_reported_once() -> None:
    answer = make_answer("Сначала [1], потом снова [1].", n_hits=3)
    assert [marker for marker, _ in answer.cited] == [1]


def test_multiple_markers_are_sorted() -> None:
    answer = make_answer("Сначала [3], затем [1].", n_hits=3)
    assert [marker for marker, _ in answer.cited] == [1, 3]


def test_markers_map_to_the_right_hits() -> None:
    answer = make_answer("Видим [2].", n_hits=3)
    ((_, hit),) = answer.cited
    assert hit.text == "текст фрагмента 1"


def test_fabricated_marker_is_flagged_not_silently_dropped() -> None:
    answer = make_answer("Согласно [7], ответ такой.", n_hits=3)
    assert answer.cited == []
    assert answer.invented_markers == [7]


def test_marker_zero_is_invalid() -> None:
    answer = make_answer("Ссылка [0].", n_hits=3)
    assert answer.invented_markers == [0]


def test_uncited_answer_has_no_sources() -> None:
    answer = make_answer("Просто утверждение без ссылок.", n_hits=3)
    assert answer.cited == []
    assert answer.invented_markers == []


def test_empty_question_is_rejected() -> None:
    from fin_assistant.agent.answer import answer_question

    with pytest.raises(ValueError, match="must not be empty"):
        answer_question("   ")
