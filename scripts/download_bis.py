import sys

from fin_assistant.config import settings
from fin_assistant.ingest.bis import PUBLICATIONS, BisError, download, find_pdf


def main() -> int:
    raw = settings.raw_dir / "basel_standards"
    for code in PUBLICATIONS:
        try:
            path = download(find_pdf(code), raw)
            print(f"  {code:<8} {path.stat().st_size // 1024:>6} KB  {PUBLICATIONS[code]}", flush=True)
        except BisError as e:
            print(f"  skip {e}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
