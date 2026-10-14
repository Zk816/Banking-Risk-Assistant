import sys
from collections import Counter

from fin_assistant.config import settings
from fin_assistant.ingest.kdif import DOC_SECTIONS, KdifError, download_doc, list_docs


def main() -> int:
    raw = settings.raw_dir / "deposit_market"
    for section in DOC_SECTIONS:
        try:
            docs = list_docs(section)
        except KdifError as e:
            print(f"  {section}: {e}")
            continue
        kinds = Counter()
        for d in docs:
            try:
                kinds[download_doc(d, raw).suffix] += 1
            except KdifError as e:
                print(f"    skip {e}")
        print(f"  {section:<48} {len(docs):>3} docs {dict(kinds)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
