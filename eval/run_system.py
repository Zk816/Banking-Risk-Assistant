import json
import re
import sys
from collections import Counter
from pathlib import Path

from fin_assistant.agent.router import answer
from fin_assistant.logging_setup import setup_logging

GOLDEN = Path(__file__).resolve().parent / "system.jsonl"

_SEPARATORS = re.compile(r"[   ]")


def states(text: str, expected: str) -> bool:
    flat = _SEPARATORS.sub("", text).replace(",", ".")
    return _SEPARATORS.sub("", expected).replace(",", ".") in flat


def main() -> int:
    setup_logging()
    questions = [json.loads(line) for line in GOLDEN.open(encoding="utf-8") if line.strip()]

    outcomes: Counter[str] = Counter()
    guard_fired: list[str] = []
    wrong: list[str] = []

    print(f"\nEND-TO-END — {len(questions)} вопросов через роутер\n")

    for q in questions:
        result = answer(q["q"])
        must_refuse = q["path"] == "refuse"
        expected = q["expect"]

        if result.fabricated:
            guard_fired.append(q["id"])
            outcomes["guard"] += 1
            mark = "ЗАЩИТА"
        elif must_refuse:
            ok = "нет" in result.text.lower() or "не найден" in result.text.lower()
            outcomes["refuse_ok" if ok else "refuse_fail"] += 1
            mark = "ok    " if ok else "ПЛОХО "
        elif expected is None:
            answered = bool(result.calls) and len(result.text) > 15
            outcomes["answered" if answered else "no_answer"] += 1
            mark = "ok    " if answered else "ПУСТО "
        elif states(result.text, expected):
            outcomes["exact"] += 1
            mark = "ok    "
        else:
            wrong.append(q["id"])
            outcomes["wrong"] += 1
            mark = "НЕВЕРНО"

        shown = " ".join(result.text.split())[:58]
        print(f"  {mark:<7} {q['id']:<14} {shown}")

    total = len(questions)
    numeric = outcomes["exact"] + outcomes["wrong"]
    print(f"\n  точные числа       {outcomes['exact']}/{numeric}")
    print(f"  отказы верные      {outcomes['refuse_ok']}/{outcomes['refuse_ok'] + outcomes['refuse_fail']}")
    print(f"  защита сработала   {outcomes['guard']}/{total}   <- модель выдумала число и была остановлена")
    if wrong:
        print(f"  неверный ответ     {', '.join(wrong)}")
    if guard_fired:
        print(f"  остановлено на     {', '.join(guard_fired)}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
