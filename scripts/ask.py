"""Ask a question against the ingested corpus.

Run: uv run python scripts/ask.py "Какие требования к капиталу банков?" --debug
"""

import argparse
import sys

from kzbank.agent.answer import answer_question
from kzbank.logging_setup import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask the corpus a question.")
    parser.add_argument("question", help="Question in Russian, Kazakh or English.")
    parser.add_argument("--top-k", type=int, default=None, help="How many chunks to retrieve.")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show the retrieved chunks and their similarity scores.",
    )
    args = parser.parse_args()
    setup_logging(verbose=getattr(args, "debug", False))

    result = answer_question(args.question, top_k=args.top_k)

    if args.debug:
        print(f"\n--- retrieved {len(result.hits)} chunks " + "-" * 44)
        by_id = {f.hit.chunk_id: f for f in result.fused}
        for i, hit in enumerate(result.hits, start=1):
            preview = " ".join(hit.text.split())[:150]
            f = by_id.get(hit.chunk_id)
            if f:
                found = f"{f.found_by:<7} v={f.vector_rank} k={f.keyword_rank}"
                ce = f"ce={f.rerank_score:+.3f}" if f.rerank_score is not None else ""
            else:
                found = ce = ""
            print(f"\n[{i}] {ce}  sim={hit.similarity:.3f}  {found}  {hit.citation}")
            print(f"    {preview}...")
        print("\n" + "-" * 62)

    print(f"\n{result.text}\n")

    cited = result.cited
    if cited:
        print("источники:")
        for marker, hit in cited:
            print(f"  [{marker}] {hit.citation}  (sim={hit.similarity:.3f})")
        unused = len(result.hits) - len(cited)
        if unused:
            print(f"  ({unused} retrieved chunk(s) not cited)")
        print()
    elif result.hits and not result.refused:
        print("⚠ answer cites no source — it is not grounded\n")

    if result.raw_text and result.raw_text != result.text:
        print("отклонённый ответ модели:")
        print(f"  {result.raw_text}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
