import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from metrics import mrr, rank_of, recall_at_k

from fin_assistant.logging_setup import setup_logging

BASELINE = Path(__file__).resolve().parent / "baseline.json"
QUESTIONS = Path(__file__).resolve().parent / "questions.jsonl"
DEPTH = 5


def measure() -> dict[str, dict[str, float]]:
    from run_eval import CONFIGS

    questions = [json.loads(line) for line in QUESTIONS.open(encoding="utf-8") if line.strip()]
    answerable = [q for q in questions if q["article"]]

    results: dict[str, dict[str, float]] = {}
    for name, get_refs in CONFIGS.items():
        ranks = [rank_of(q["article"], get_refs(q["q"])) for q in answerable]
        results[name] = {
            "recall_1": round(recall_at_k(ranks, 1), 4),
            "mrr": round(mrr(ranks), 4),
        }
    return results


def compare(current: dict, baseline: dict, tolerance: float) -> list[str]:
    failures = []
    for config, expected in baseline.items():
        if config not in current:
            failures.append(f"{config}: конфигурация исчезла из прогона")
            continue
        for metric, floor in expected.items():
            got = current[config][metric]
            if got < floor - tolerance:
                failures.append(f"{config}.{metric}: {got:.3f} < {floor:.3f} (допуск {tolerance})")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail on retrieval regression.")
    parser.add_argument(
        "--record",
        action="store_true",
        help="Overwrite the baseline with the current numbers. "
        "Deliberate act — say why in the commit message.",
    )
    args = parser.parse_args()
    setup_logging()

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    current = measure()

    print(f"\n{'конфигурация':<22} {'recall@1':>10} {'база':>8}   {'MRR':>7} {'база':>8}")
    print("-" * 64)
    for config, expected in baseline["retrieval"].items():
        got = current.get(config, {})
        print(
            f"{config:<22} {got.get('recall_1', 0):>10.3f} {expected['recall_1']:>8.3f}   "
            f"{got.get('mrr', 0):>7.3f} {expected['mrr']:>8.3f}"
        )

    if args.record:
        baseline["retrieval"] = current
        BASELINE.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n  baseline перезаписан: {BASELINE.name}\n")
        return 0

    failures = compare(current, baseline["retrieval"], baseline["tolerance"])
    if failures:
        print("\n  РЕГРЕССИЯ:")
        for failure in failures:
            print(f"    {failure}")
        print("\n  Либо откати изменение, либо перезапиши базу:")
        print("    uv run python eval/gate.py --record\n")
        return 1

    print(f"\n  ворота пройдены (допуск {baseline['tolerance']})\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
