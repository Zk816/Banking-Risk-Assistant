import argparse
import sys
from collections import Counter
from datetime import date

from fin_assistant.config import settings
from fin_assistant.ingest.kase import BANKS, KaseError
from fin_assistant.ingest.kase_docs import download, list_documents, list_issuers
from fin_assistant.logging_setup import setup_logging


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--since", type=date.fromisoformat, default=date(2018, 1, 1))
    p.add_argument("--bank", action="append", help="KASE issuer code; repeatable")
    p.add_argument(
        "--all-issuers",
        action="store_true",
        help="Every Kazakh issuer on KASE, not only banks (audit + rating only)",
    )
    args = p.parse_args()
    setup_logging()
    raw = settings.raw_dir / "company_reports"
    codes = args.bank or list(BANKS)
    kinds = None
    if args.all_issuers:
        codes = [i.code for i in list_issuers() if i.code not in BANKS]
        kinds = {"audit", "rating"}
        print(f"  {len(codes)} non-bank issuers")
    for code in codes:
        try:
            docs = list_documents(code, args.since)
        except KaseError as e:
            print(f"  {code}: {e}")
            continue
        got, failed = Counter(), 0
        for d in docs:
            if kinds and d.kind not in kinds:
                continue
            try:
                download(d, raw)
                got[d.kind] += 1
            except KaseError as e:
                failed += 1
                print(f"    skip {e}")
        print(f"  {code}: {dict(got)}  failed {failed}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
