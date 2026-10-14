import pytest
from pydantic import ValidationError

from fin_assistant.config import Settings
from fin_assistant.retrieval import rerank as rerank_module
from fin_assistant.retrieval.hybrid import FusedHit
from fin_assistant.retrieval.vector_search import Hit

BASE = {"database_url": "postgresql://u:p@localhost:5435/db"}


def fused(chunk_id: int, text: str = "x") -> FusedHit:
    hit = Hit(
        chunk_id=chunk_id,
        document_id=1,
        document_title="doc",
        document_url=None,
        document_edition=None,
        ordinal=chunk_id,
        article_ref=f"Статья {chunk_id}",
        text=text,
        similarity=0.5,
    )
    return FusedHit(hit=hit, rrf_score=0.01, vector_rank=chunk_id, keyword_rank=None)


class StubModel:
    def __init__(self, scores: list[float]) -> None:
        self.scores = scores
        self.calls = 0

    def predict(self, pairs, **kwargs):
        self.calls += 1
        assert len(pairs) == len(self.scores)
        return self.scores


@pytest.fixture
def stub(monkeypatch):
    def install(scores: list[float]) -> StubModel:
        model = StubModel(scores)
        monkeypatch.setattr(rerank_module, "get_reranker", lambda: model)
        return model

    return install


def test_reorders_by_cross_encoder_score(stub) -> None:
    stub([0.1, 0.9, 0.5])
    out = rerank_module.rerank("q", [fused(1), fused(2), fused(3)], top_k=3)
    assert [f.hit.chunk_id for f in out] == [2, 3, 1]


def test_score_is_attached_for_inspection(stub) -> None:
    stub([0.42])
    out = rerank_module.rerank("q", [fused(1)], top_k=1)
    assert out[0].rerank_score == pytest.approx(0.42)


def test_truncates_to_top_k(stub) -> None:
    stub([0.1, 0.9, 0.5, 0.7])
    out = rerank_module.rerank("q", [fused(i) for i in range(1, 5)], top_k=2)
    assert [f.hit.chunk_id for f in out] == [2, 4]


def test_rrf_provenance_survives_reranking(stub) -> None:
    stub([0.9])
    out = rerank_module.rerank("q", [fused(7)], top_k=1)
    assert out[0].vector_rank == 7
    assert out[0].rrf_score == pytest.approx(0.01)


def test_empty_candidates_never_calls_the_model(stub) -> None:
    model = stub([])
    assert rerank_module.rerank("q", [], top_k=5) == []
    assert model.calls == 0


def test_nonpositive_top_k_is_rejected(stub) -> None:
    stub([0.1])
    with pytest.raises(ValueError, match="top_k must be positive"):
        rerank_module.rerank("q", [fused(1)], top_k=0)


def test_candidates_below_top_k_is_rejected() -> None:
    with pytest.raises(ValueError, match="rerank_candidates"):
        Settings(**BASE, top_k=10, rerank_candidates=5)


def test_candidates_equal_to_top_k_is_allowed() -> None:
    s = Settings(**BASE, top_k=5, rerank_candidates=5)
    assert s.rerank_candidates == 5


def test_zero_batch_size_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(**BASE, rerank_batch_size=0)
