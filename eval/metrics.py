from dataclasses import dataclass


def rank_of(expected_article: str, retrieved_refs: list[str | None]) -> int | None:
    for position, ref in enumerate(retrieved_refs, start=1):
        if ref and ref.endswith(expected_article):
            return position
    return None


def recall_at_k(ranks: list[int | None], k: int) -> float:
    if not ranks:
        return 0.0
    return sum(1 for r in ranks if r is not None and r <= k) / len(ranks)


def mrr(ranks: list[int | None]) -> float:
    if not ranks:
        return 0.0
    return sum(1.0 / r if r else 0.0 for r in ranks) / len(ranks)


@dataclass(frozen=True)
class RetrievalScore:
    name: str
    n: int
    recall_1: float
    recall_3: float
    recall_5: float
    mrr: float
    misses: list[str]

    def row(self) -> str:
        return (
            f"{self.name:<22} {self.recall_1:>7.2f} {self.recall_3:>7.2f} "
            f"{self.recall_5:>7.2f} {self.mrr:>7.3f}   {len(self.misses)} miss"
        )


def score_retrieval(name: str, ranks: list[int | None], ids: list[str]) -> RetrievalScore:
    return RetrievalScore(
        name=name,
        n=len(ranks),
        recall_1=recall_at_k(ranks, 1),
        recall_3=recall_at_k(ranks, 3),
        recall_5=recall_at_k(ranks, 5),
        mrr=mrr(ranks),
        misses=[qid for qid, r in zip(ids, ranks, strict=True) if r is None or r > 5],
    )


@dataclass(frozen=True)
class AnswerScore:
    n: int
    numeric_total: int
    numeric_correct: int
    refusal_total: int
    refusal_correct: int
    over_refusals: list[str]
    wrong_numbers: list[str]

    @property
    def numeric_accuracy(self) -> float:
        return self.numeric_correct / self.numeric_total if self.numeric_total else 0.0

    @property
    def refusal_accuracy(self) -> float:
        return self.refusal_correct / self.refusal_total if self.refusal_total else 0.0
