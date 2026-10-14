import argparse
import json
import sys
from pathlib import Path

from fin_assistant.agent.answer import answer_question
from fin_assistant.config import settings
from fin_assistant.logging_setup import setup_logging
from fin_assistant.retrieval.hybrid import search_hybrid
from fin_assistant.retrieval.rerank import rerank
from fin_assistant.retrieval.vector_search import search as search_vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from metrics import AnswerScore, rank_of, score_retrieval

GOLDEN = Path(__file__).resolve().parent / "questions.jsonl"
DEPTH = 5


def load_questions() -> list[dict]:
    with GOLDEN.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def refs_vector(question: str) -> list[str | None]:
    return [h.article_ref for h in search_vector(question, top_k=DEPTH)]


def refs_hybrid(question: str) -> list[str | None]:
    return [f.hit.article_ref for f in search_hybrid(question, top_k=DEPTH)]


def refs_reranked(question: str) -> list[str | None]:
    candidates = search_hybrid(question, top_k=settings.rerank_candidates)
    return [f.hit.article_ref for f in rerank(question, candidates, top_k=DEPTH)]


CONFIGS = {
    "vector only": refs_vector,
    "hybrid (RRF)": refs_hybrid,
    "hybrid + rerank": refs_reranked,
}


def evaluate_retrieval(questions: list[dict]) -> None:
    answerable = [q for q in questions if q["article"]]

    print(f"\nRETRIEVAL — {len(answerable)} answerable questions\n")
    print(f"{'configuration':<22} {'R@1':>7} {'R@3':>7} {'R@5':>7} {'MRR':>7}")
    print("-" * 62)

    for name, get_refs in CONFIGS.items():
        ranks = [rank_of(q["article"], get_refs(q["q"])) for q in answerable]
        score = score_retrieval(name, ranks, [q["id"] for q in answerable])
        print(score.row())
        if score.misses:
            print(f"{'':22} промахи: {', '.join(score.misses)}")

    print("\nпо типу вопроса (hybrid + rerank, R@1):")
    for kind in sorted({q["kind"] for q in answerable}):
        subset = [q for q in answerable if q["kind"] == kind]
        ranks = [rank_of(q["article"], refs_reranked(q["q"])) for q in subset]
        hit = sum(1 for r in ranks if r == 1)
        print(f"  {kind:<16} {hit}/{len(subset)}")


def evaluate_answers(questions: list[dict]) -> None:
    print(f"\nANSWERS — {len(questions)} questions (one LLM call each)\n")

    numeric_total = numeric_correct = 0
    refusal_total = refusal_correct = 0
    over_refusals: list[str] = []
    wrong_numbers: list[str] = []

    for q in questions:
        result = answer_question(q["q"])
        should_refuse = q["article"] is None

        if should_refuse:
            refusal_total += 1
            if result.refused:
                refusal_correct += 1
                mark = "OK  "
            else:
                mark = "FAIL"
            print(f"  {mark} [refuse] {q['id']}")
            continue

        if result.refused:
            over_refusals.append(q["id"])
            print(f"  FAIL [refused answerable] {q['id']}")
            continue

        if q["value"]:
            numeric_total += 1
            flat = result.text.replace(" ", "").replace(" ", "")
            if q["value"] in flat:
                numeric_correct += 1
                print(f"  OK   [{q['value']}] {q['id']}")
            else:
                wrong_numbers.append(q["id"])
                print(f"  FAIL [expected {q['value']}] {q['id']}: {result.text[:60]}")
        else:
            print(f"  OK   [answered] {q['id']}")

    score = AnswerScore(
        n=len(questions),
        numeric_total=numeric_total,
        numeric_correct=numeric_correct,
        refusal_total=refusal_total,
        refusal_correct=refusal_correct,
        over_refusals=over_refusals,
        wrong_numbers=wrong_numbers,
    )
    print(
        f"\n  числовая точность   {score.numeric_correct}/{score.numeric_total}"
        f"  ({score.numeric_accuracy:.0%})"
    )
    print(
        f"  точность отказов    {score.refusal_correct}/{score.refusal_total}  ({score.refusal_accuracy:.0%})"
    )
    if score.over_refusals:
        print(f"  лишние отказы       {', '.join(score.over_refusals)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate against the golden set.")
    parser.add_argument("--answers", action="store_true", help="Also run the full answer pipeline (slow).")
    args = parser.parse_args()
    setup_logging()

    questions = load_questions()
    evaluate_retrieval(questions)
    if args.answers:
        evaluate_answers(questions)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
