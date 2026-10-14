import argparse
import sys

from fin_assistant.agent.router import answer as route_answer
from fin_assistant.logging_setup import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask about stored financial metrics.")
    parser.add_argument("question")
    parser.add_argument(
        "--debug", action="store_true", help="Show every tool call and the model's raw answer."
    )
    args = parser.parse_args()
    setup_logging()

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
        print(f"⚠ модель назвала числа, которых не возвращал ни один инструмент: {result.fabricated}\n")
    elif not result.grounded and not result.hit_limit and not result.calls:
        print("⚠ ответ без единого вызова инструмента\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
