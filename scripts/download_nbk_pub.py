import argparse
import sys
from collections import Counter

from fin_assistant.config import settings
from fin_assistant.ingest.nbk_pub import SECTIONS, download, list_section
from fin_assistant.ingest.nbk_stats import NbkStatsError


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--section", action="append", choices=list(SECTIONS))
    args = p.parse_args()
    raw = settings.raw_dir / "nbk_publications"
    for slug in args.section or list(SECTIONS):
        try:
            files = list_section(slug)
        except NbkStatsError as e:
            print(f"  {slug}: {e}", flush=True)
            continue
        kinds, failed = Counter(), 0
        for f in files:
            try:
                kinds[download(f, raw).suffix] += 1
            except NbkStatsError as e:
                failed += 1
                print(f"    skip {e}", flush=True)
        print(f"  {slug:<45} {len(files):>4} files {dict(kinds)} failed {failed}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
