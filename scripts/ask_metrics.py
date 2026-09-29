"""Ask about numbers. The model queries the metrics tables itself.

Run: uv run python scripts/ask_metrics.py "Какой курс евро?" --debug
"""

import argparse
import sys

from kzbank.agent.router import answer as route_answer
from kzbank.logging_setup import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask about stored financial metrics.")
    parser.add_argument("question")
    parser.add_argument("--debug", action="store_true",
                        help="Show every tool call and the model's raw answer.")
    args = parser.parse_args()
    setup_logging()

    # Через роутер: иначе видны только инструменты, которые он знал
    # при написании, а новые (EDGAR, поиск по закону) остаются недоступны.
    result = route_answer(args.question)

    if args.debug:
        print(f"\n--- {len(result.calls)} вызов(ов) инструментов, {result.rounds} раунд(ов) ---")
        for call in result.calls:
            status = "ok" if call.ok else "ОШИБКА"
            print(f"\n  {call.name}({call.arguments})  [{status}]")
            for key, value in call.result.items():
                print(f"      {key}: {value}")
        if result.raw_text and result.raw_text != result.text:
            print(f"\n  сырой ответ модели: {result.raw_text}")
        print("\n" + "-" * 52)

    print(f"\n{result.text}\n")

    if result.fabricated:
        print(f"⚠ модель назвала числа, которых не возвращал ни один инструмент: "
              f"{result.fabricated}\n")
    elif not result.grounded and not result.hit_limit and not result.calls:
        # A tool that ran and returned found=False *is* grounding — for a
        # refusal. Warning there told the user a correct answer was suspect.
        print("⚠ ответ без единого вызова инструмента\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
