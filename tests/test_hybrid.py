import pytest

from fin_assistant.retrieval.hybrid import RRF_K, FusedHit
from fin_assistant.retrieval.keyword_search import build_tsquery
from fin_assistant.retrieval.vector_search import Hit


def hit(chunk_id: int) -> Hit:
    return Hit(
        chunk_id=chunk_id,
        document_id=1,
        document_title="doc",
        document_url=None,
        document_edition=None,
        ordinal=chunk_id,
        article_ref=f"Статья {chunk_id}",
        text="x",
        similarity=0.5,
    )


def rrf(rank: int) -> float:
    return 1.0 / (RRF_K + rank)


def test_terms_are_or_joined_not_and() -> None:
    assert build_tsquery("норматив k2") == "норматив | k2"


def test_hyphenated_token_survives() -> None:
    assert build_tsquery("коэффициент k1-2") == "коэффициент | k1-2"


def test_operator_characters_cannot_reach_tsquery() -> None:
    assert build_tsquery("k2 & (bad | !syntax) :*") == "k2 | bad | syntax"


def test_query_with_no_terms_is_empty() -> None:
    assert build_tsquery("!!! ???") == ""
    assert build_tsquery("") == ""


def test_appearing_in_both_lists_beats_winning_one() -> None:
    in_both = rrf(2) + rrf(2)
    vector_only_first = rrf(1)
    assert in_both > vector_only_first


def test_rrf_damps_the_gap_between_top_ranks() -> None:
    assert rrf(1) / rrf(2) == pytest.approx(62 / 61, rel=1e-6)


def test_found_by_reports_which_retriever() -> None:
    assert FusedHit(hit(1), 0.03, 1, 1).found_by == "both"
    assert FusedHit(hit(2), 0.01, 3, None).found_by == "vector"
    assert FusedHit(hit(3), 0.01, None, 2).found_by == "keyword"


def test_keyword_only_chunk_can_outrank_a_weak_vector_hit() -> None:
    rescued = rrf(1)
    weak_vector = rrf(9)
    assert rescued > weak_vector
