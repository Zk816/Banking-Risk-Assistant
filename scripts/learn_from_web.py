import argparse
import hashlib
import sys
from datetime import date
from urllib.parse import urlparse

from fin_assistant.ingest.adilet import ParsedDocument
from fin_assistant.logging_setup import setup_logging
from fin_assistant.storage.db import connect, fetch_all
from fin_assistant.storage.documents import store_document
from fin_assistant.tools.web import fetch_page

SOURCE = "web_official"
FULL_TEXT_CHARS = 2_000_000


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    setup_logging()
    pages = fetch_all(
        "SELECT url, title, times_used FROM web_pages WHERE official AND ingested_at IS NULL "
        "ORDER BY times_used DESC, first_seen"
    )
    print(f"\n  {len(pages)} official page(s) to learn\n")
    learned = 0
    for page in pages:
        print(f"  {page['times_used']:>3}x  {page['url'][:100]}")
        if args.dry_run:
            continue
        result = fetch_page(page["url"], max_chars=FULL_TEXT_CHARS, remember=False)
        if not result.get("found"):
            print(f"        skip: {result.get('reason')}")
            continue
        text = result["text"]
        document = ParsedDocument(
            source=SOURCE,
            title=f"{result['title']} ({urlparse(page['url']).hostname})",
            text=text,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            raw_path=page["url"],
            url=page["url"],
            language="ru",
            published_at=date.today(),
        )
        document_id, chunks = store_document(document)
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE web_pages SET ingested_at = now(), document_id = %s WHERE url = %s",
                (document_id if document_id > 0 else None, page["url"]),
            )
        print(f"        +{chunks} chunks")
        learned += 1
    print(f"\n  learned {learned} page(s)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
