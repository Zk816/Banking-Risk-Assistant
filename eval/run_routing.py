import json
import sys
from pathlib import Path

from fin_assistant.agent.router import answer
from fin_assistant.logging_setup import setup_logging

GOLDEN = Path(__file__).resolve().parent / "routing.jsonl"


def main() -> int:
    setup_logging()
    questions = [json.loads(line) for line in GOLDEN.open(encoding="utf-8") if line.strip()]

    correct = 0
    no_call = 0
    print(f"\nROUTING — {len(questions)} вопросов\n")

    for q in questions:
        result = answer(q["q"])
        used = [c.name for c in result.calls]
        expected = q["tool"]

        if not used:
            no_call += 1
            mark = "НЕТ ВЫЗОВА"
        elif used[0] == expected:
            correct += 1
            mark = "ok        "
        else:
            mark = "НЕ ТОТ    "

        print(f"  {mark} {q['id']:<12} ждали {expected:<19} получили {', '.join(used) or '—'}")

    print(f"\n  правильный инструмент: {correct}/{len(questions)}  ({correct / len(questions):.0%})")
    if no_call:
        print(f"  ответов без вызова:    {no_call}  <- отвечает по памяти")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
