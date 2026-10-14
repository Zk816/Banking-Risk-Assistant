import argparse
import json
import re
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
QUESTIONS = HERE / "pro_questions.jsonl"
RESULTS = HERE / "results"

_SPACES = re.compile(r"[\s  ]")
REFUSAL = re.compile(
    r"нет данных|за рамки|вне компетенц|не найден|нет информации|не могу|не располага|отсутству|вне мо(их|ей|его)|"
    r"не нашл|нет в источниках|no data|not found|cannot|can't|can not|unable|not able|"
    r"outside (my|the) scope|don't have|do not have|not (provide|give) (investment|financial) advice",
    re.IGNORECASE,
)


def _norm(text: str) -> str:
    return _SPACES.sub("", text).lower()


def contains(text: str, needle: str) -> bool:
    t, n = _norm(text), _norm(needle)
    return n in t or n.replace(",", ".") in t.replace(",", ".")


def score(q: dict, text: str) -> bool:
    if q.get("refuse"):
        return bool(REFUSAL.search(text))
    if REFUSAL.search(text[:120]):
        return False
    if "all" in q:
        return all(any(contains(text, n) for n in group) for group in q["all"])
    return any(contains(text, n) for n in q["expect"])


def make_agent(name: str):
    if name == "cognitive":
        from fin_assistant.agent.cognitive import CognitiveAgent

        agent = CognitiveAgent("eval@local", consent=False)

        def ask(question: str) -> str:
            trace = agent.handle(question)
            return str(trace["output"] if isinstance(trace, dict) else getattr(trace, "output", trace))

        return ask
    if name == "assistant":
        from fin_assistant.agent.assistant import ask as assistant_ask

        return lambda question: assistant_ask(question).text
    raise SystemExit(f"unknown agent {name}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--agent", default="cognitive")
    p.add_argument("--only", help="run one category")
    p.add_argument(
        "--set",
        default="pro",
        choices=["pro", "holdout", "feedback"],
        help="pro: 50 questions used while building; holdout: 32 never tuned on; feedback: from 👍/👎",
    )
    args = p.parse_args()
    source = {
        "pro": QUESTIONS,
        "holdout": HERE / "holdout_questions.jsonl",
        "feedback": HERE / "feedback_questions.jsonl",
    }[args.set]
    questions = [json.loads(l) for l in source.open(encoding="utf-8") if l.strip()] if source.exists() else []
    if not questions:
        print(
            f"  no questions in {source.relative_to(HERE.parent)} yet."
            + (
                "\n  Rate answers in the chat, then run: uv run python scripts/feedback_review.py"
                if args.set == "feedback"
                else ""
            )
        )
        return 0
    if args.only:
        questions = [q for q in questions if q["cat"] == args.only]
    ask = make_agent(args.agent)

    rows, by_cat = [], defaultdict(lambda: [0, 0])
    for q in questions:
        start = time.time()
        try:
            text = ask(q["q"])
        except Exception as e:
            text = f"ERROR {type(e).__name__}: {e}"
        ok = score(q, text)
        by_cat[q["cat"]][0] += ok
        by_cat[q["cat"]][1] += 1
        rows.append(
            {
                "id": q["id"],
                "cat": q["cat"],
                "ok": ok,
                "seconds": round(time.time() - start, 1),
                "q": q["q"],
                "answer": text[:1500],
            }
        )
        print(
            f"  {'ok ' if ok else 'BAD'}  {q['id']:<26} {time.time() - start:5.1f}s  {text[:90]!r}",
            flush=True,
        )

    total = sum(r["ok"] for r in rows)
    print(f"\n  {args.agent}: {total}/{len(rows)} correct ({100 * total / len(rows):.0f}%)\n")
    for cat, (ok, n) in by_cat.items():
        print(f"    {cat:<10} {ok}/{n}")
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"{args.set}_{args.agent}_{date.today()}.json"
    out.write_text(
        json.dumps(
            {
                "agent": args.agent,
                "correct": total,
                "total": len(rows),
                "by_category": {c: v for c, v in by_cat.items()},
                "rows": rows,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"\n  saved {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
