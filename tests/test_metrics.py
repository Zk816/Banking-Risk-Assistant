import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
from metrics import mrr, rank_of, recall_at_k, score_retrieval

REFS = ["Глава 3, Статья 42", "Глава 3, Статья 43", None, "Преамбула"]


def test_rank_matches_article_inside_a_chapter_ref() -> None:
    assert rank_of("Статья 43", REFS) == 2


def test_rank_is_one_based() -> None:
    assert rank_of("Статья 42", REFS) == 1


def test_missing_article_gives_none() -> None:
    assert rank_of("Статья 99", REFS) is None


def test_none_refs_are_skipped_not_matched() -> None:
    assert rank_of("Статья 1", [None, None]) is None


def test_suffix_match_does_not_confuse_similar_numbers() -> None:
    assert rank_of("Статья 4", ["Глава 3, Статья 43"]) is None


def test_recall_counts_only_within_k() -> None:
    ranks = [1, 3, 7, None]
    assert recall_at_k(ranks, 1) == pytest.approx(0.25)
    assert recall_at_k(ranks, 3) == pytest.approx(0.50)
    assert recall_at_k(ranks, 5) == pytest.approx(0.50)


def test_mrr_rewards_rank_one() -> None:
    assert mrr([1, 1]) == pytest.approx(1.0)
    assert mrr([2, 2]) == pytest.approx(0.5)
    assert mrr([None]) == pytest.approx(0.0)


def test_mrr_separates_what_recall_cannot() -> None:
    always_first = [1, 1, 1]
    always_fifth = [5, 5, 5]
    assert recall_at_k(always_first, 5) == recall_at_k(always_fifth, 5)
    assert mrr(always_first) > mrr(always_fifth)


def test_empty_input_is_zero_not_a_crash() -> None:
    assert recall_at_k([], 5) == 0.0
    assert mrr([]) == 0.0


def test_misses_name_the_failing_questions() -> None:
    score = score_retrieval("cfg", [1, None, 9], ["a", "b", "c"])
    assert score.misses == ["b", "c"]
