"""Download National Bank acts from a page you saved in your browser.

The split matters. nationalbank.kz's robots.txt says:

    Disallow: /ru/npa

so no crawler of ours goes near the listing. The file path itself —
`/file/download/{id}` — carries no such restriction. So:

    you    open the section in a browser and press Ctrl+S
    this   reads the saved HTML, pulls the file ids, downloads them

Nothing here fetches a disallowed page; it only fetches files whose ids you
already obtained by using the site normally.

Run: uv run python scripts/fetch_acts.py ~/Downloads/page.html
     uv run python scripts/fetch_acts.py ~/Downloads/page.html --list
"""

import argparse
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote

import httpx
from bs4 import BeautifulSoup

from kzbank.config import settings
from kzbank.logging_setup import get_logger, setup_logging

log = get_logger(__name__)

BASE_URL = "https://www.nationalbank.kz"
USER_AGENT = "kzbank-research/0.1 (+educational RAG project)"
TIMEOUT_S = 120.0
# One file every two seconds. These are 600 KB Word documents on a regulator's
# web server; there is no reason to be in a hurry.
DELAY_S = 2.0

# A browser's "Save page as" rewrites relative hrefs to absolute ones, so the
# same link arrives as either "/file/download/123" or
# "https://nationalbank.kz/file/download/123". Match the id either way.
_ID_RE = re.compile(r"/file/download/(\d+)")
_FILENAME_RE = re.compile(r"filename\*?=(?:utf-8'')?\"?([^\";]+)", re.IGNORECASE)
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def parse_saved_page(path: Path) -> list[tuple[str, str]]:
    """Pull (file_id, title) pairs out of a saved section page.

    Raises:
        SystemExit: if the file is missing or holds no download links.
    """
    if not path.is_file():
        raise SystemExit(f"\n  Нет файла: {path}\n")

    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "lxml")

    # Each file is linked twice from the same card: once as a bare document icon
    # and once around the act's title. Same id, and only the second has text, so
    # the title comes from the card rather than from either link.
    found: dict[str, str] = {}
    for link in soup.select('a[href*="/file/download/"]'):
        match = _ID_RE.search(link.get("href", ""))
        if not match:
            continue
        file_id = match.group(1)

        card = link.find_parent(class_=re.compile(r"posts-files__item")) or link.parent
        title = " ".join(card.get_text(" ", strip=True).split())
        if len(title) > len(found.get(file_id, "")):
            found[file_id] = title[:200]

    if not found:
        raise SystemExit(
            f"\n  В {path.name} нет ссылок /file/download/.\n"
            f"  Сохранена ли страница раздела НПА? Проверь в консоли браузера:\n"
            f"    document.querySelectorAll('a[href^=\"/file/download/\"]').length\n"
        )
    return sorted(found.items(), key=lambda pair: int(pair[0]))


def download(file_id: str, target_dir: Path, *, client: httpx.Client) -> Path | None:
    """Fetch one file. Returns its path, or None if it was already there.

    Raises:
        httpx.HTTPError: on a network failure; the caller decides whether to stop.
    """
    response = client.get(f"/file/download/{file_id}")
    response.raise_for_status()

    name = _filename_from(response.headers.get("content-disposition", ""), file_id)
    path = target_dir / f"{file_id}_{name}"
    if path.exists() and path.stat().st_size == len(response.content):
        return None

    path.write_bytes(response.content)
    return path


def _filename_from(disposition: str, file_id: str) -> str:
    """The server's filename, made safe for a local path."""
    match = _FILENAME_RE.search(disposition)
    if not match:
        return f"{file_id}.docx"
    name = unquote(match.group(1)).strip()
    # The id is already the prefix; keep the name short and path-safe.
    return _UNSAFE.sub("_", name)[:120] or f"{file_id}.docx"


def main() -> int:
    parser = argparse.ArgumentParser(description="Download NBK acts from a saved page.")
    parser.add_argument("page", type=Path, help="HTML saved from a /ru/npa/ section")
    parser.add_argument("--out", type=Path, default=None,
                        help="Where to put the files (default: data/raw/nbk).")
    parser.add_argument("--list", action="store_true",
                        help="Show what would be downloaded, fetch nothing.")
    parser.add_argument("--limit", type=int, default=None, help="Stop after N files.")
    args = parser.parse_args()
    setup_logging()

    links = parse_saved_page(args.page)
    if args.limit:
        links = links[: args.limit]

    print(f"\n  {len(links)} файл(ов) в {args.page.name}\n")
    for file_id, title in links[:5]:
        print(f"    {file_id}  {title[:78]}")
    if len(links) > 5:
        print(f"    … ещё {len(links) - 5}")

    if args.list:
        print("\n  --list: ничего не скачано\n")
        return 0

    target = args.out or (settings.raw_dir / "nbk")
    target.mkdir(parents=True, exist_ok=True)
    print(f"\n  качаю в {target}, по файлу каждые {DELAY_S:.0f} с\n")

    saved = skipped = failed = 0
    with httpx.Client(base_url=BASE_URL, headers={"User-Agent": USER_AGENT},
                      timeout=TIMEOUT_S, follow_redirects=True) as client:
        for i, (file_id, title) in enumerate(links):
            if i:
                time.sleep(DELAY_S)
            try:
                path = download(file_id, target, client=client)
            except httpx.HTTPError as e:
                failed += 1
                log.warning("file %s failed: %s", file_id, e)
                print(f"    ОШИБКА {file_id}: {e}")
                continue

            if path is None:
                skipped += 1
                print(f"    уже есть  {file_id}")
            else:
                saved += 1
                size_kb = path.stat().st_size // 1024
                print(f"    {saved:>3}/{len(links)}  {size_kb:>5} КБ  {path.name[:62]}")

    log.info("nbk acts saved=%d skipped=%d failed=%d", saved, skipped, failed)
    print(f"\n  скачано {saved}, пропущено {skipped}, ошибок {failed}")
    print(f"  дальше:  uv run python scripts/ingest.py --source nbk\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
