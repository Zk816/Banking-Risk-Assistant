import sys

from fin_assistant.config import settings
from fin_assistant.ingest.wb_docs import QUERIES, WbDocsError, download, search


def main() -> int:
    raw = settings.raw_dir / "worldbank_reports"
    seen: dict[str, object] = {}
    for q in QUERIES:
        try:
            for d in search(q):
                seen.setdefault(d.doc_id, d)
        except WbDocsError as e:
            print(f"  {e}")
    print(f"  {len(seen)} reports found")
    ok = failed = 0
    for d in seen.values():
        try:
            download(d, raw)
            ok += 1
        except WbDocsError as e:
            failed += 1
            print(f"    skip {e}")
    print(f"  downloaded {ok}, failed {failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
