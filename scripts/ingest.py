import argparse
import hashlib
import re
import sys
from datetime import date
from pathlib import Path

from fin_assistant.config import settings
from fin_assistant.extract.ocr import ocr_pdf
from fin_assistant.extract.pdf import PdfTextError, chunk_pages, read_pdf
from fin_assistant.extract.tables import TableTextError, chunk_sheets, read_sheets
from fin_assistant.ingest import bis, kase, nbk_acts
from fin_assistant.ingest.adilet import ParsedDocument
from fin_assistant.ingest.adilet import parse_directory as parse_adilet
from fin_assistant.logging_setup import get_logger, setup_logging
from fin_assistant.storage.db import (
    fetch_all,
    table_counts,
)
from fin_assistant.storage.documents import store_document as store

log = get_logger(__name__)

PDF_SOURCES = {
    "stability_reports",
    "company_reports",
    "basel_standards",
    "nbk_publications",
    "deposit_market",
    "worldbank_reports",
}
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")


KASE_KIND_LABEL = {
    "audit": "аудированная финансовая отчетность (МСФО)",
    "review": "обзорная промежуточная финансовая отчетность (МСФО)",
    "fs": "квартальная финансовая отчетность",
    "rating": "рейтинговое действие",
}
_KASE_FILE = re.compile(r"^(audit|review|fs|rating)_(\d{4}-\d{2}-\d{2})_(.+)$")
_YEAR = re.compile(r"(19|20)\d{2}")


def _issuer_name(kase_dir: Path, code: str) -> str:
    if code in kase.BANKS:
        return kase.BANKS[code]
    listing = kase_dir / "issuers.tsv"
    if listing.exists():
        for line in listing.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if parts[0] == code and len(parts) > 1:
                return parts[1]
    return code


def _year_end(text: str) -> date | None:
    for m in _YEAR.finditer(text):
        year = int(m.group(0))
        if 1990 <= year <= date.today().year:
            return min(date(year, 12, 31), date.today())
    return None


def _listed_title(path: Path) -> str | None:
    listing = path.parent / "titles.tsv"
    if not listing.exists():
        return None
    for line in listing.read_text(encoding="utf-8").splitlines():
        name, *rest = line.split("\t")
        if name == path.name and rest:
            return rest[-1].strip()
    return None


def pdf_document(path: Path, source: str, pdf) -> ParsedDocument:
    first = pdf.pages[0][1]
    title = _listed_title(path) or first.split("\n", 1)[0][:150]
    published, url = None, None
    m = _KASE_FILE.match(path.name)
    if source == "company_reports" and m:
        code = path.parent.name
        published = date.fromisoformat(m.group(2))
        url = f"{kase.BASE}/files/emitters/{code}/{m.group(3)}"
        fiscal = re.search(r"(?<!\d)(20\d{2})(?!\d)", m.group(3))
        year = f" за {fiscal.group(1)} год" if fiscal and m.group(1) != "rating" else ""
        title = f"{_issuer_name(path.parent.parent, code)}: {KASE_KIND_LABEL[m.group(1)]}{year}, {m.group(3)}"
    else:
        published = _year_end(title) or _year_end(first[:400])
        if source == "basel_standards":
            title = f"BCBS {path.stem}: {bis.PUBLICATIONS.get(path.stem, path.stem)}"
            url = f"{bis.BASE}/bcbs/publ/{path.stem}.htm"
        if source == "stability_reports":
            url = f"https://nationalbank.kz/file/download/{path.stem.split('_')[-1]}"
            y = _YEAR.search(" ".join(t for _, t in pdf.pages[:3]))
            title = f"Отчет о финансовой стабильности Казахстана ({y.group(0) if y else path.stem})"
    return ParsedDocument(
        source=source,
        title=title,
        text="\n".join(t for _, t in pdf.pages),
        content_hash=pdf.content_hash,
        raw_path=str(path),
        url=url,
        language="ru" if _CYRILLIC.search(first) else "en",
        published_at=published,
    )


def ingest_pdfs(directory: Path, source: str, ocr: bool = False) -> int:
    files = sorted(directory.rglob("*.pdf"))
    print(f"\n{len(files)} PDF(s) under {directory}\n")
    total = skipped = 0
    stored = {r["raw_path"] for r in fetch_all("SELECT raw_path FROM documents WHERE source = %s", (source,))}
    for i, path in enumerate(files, 1):
        if str(path) in stored:
            continue
        try:
            pdf = read_pdf(path)
        except PdfTextError as e:
            if not ocr:
                skipped += 1
                log.warning("skipped %s", e)
                continue
            try:
                pdf = ocr_pdf(path)
            except PdfTextError as e2:
                skipped += 1
                log.warning("skipped after OCR %s", e2)
                continue
        document = pdf_document(path, source, pdf)
        chunks = chunk_pages(pdf, size=settings.chunk_size, overlap=settings.chunk_overlap)
        document_id, n = store(document, chunks)
        total += n
        state = "unchanged" if document_id < 0 else f"+{n:>4} chunks"
        print(
            f"  [{i}/{len(files)}] {state}  {len(pdf.pages)}/{pdf.total_pages} p  {document.title[:70]}",
            flush=True,
        )
    print(f"\n  {total} chunks written, {skipped} PDFs without readable text\n")
    return 0


def ingest_tables(directory: Path, source: str) -> int:
    files = sorted(p for p in directory.rglob("*") if p.suffix.lower() in (".xls", ".xlsx"))
    stored = {r["raw_path"] for r in fetch_all("SELECT raw_path FROM documents WHERE source = %s", (source,))}
    print(f"\n{len(files)} spreadsheet(s) under {directory}\n")
    total = skipped = 0
    for i, path in enumerate(files, 1):
        if str(path) in stored:
            continue
        try:
            sheets = read_sheets(path)
        except TableTextError as e:
            skipped += 1
            log.warning("skipped %s", e)
            continue
        title = _listed_title(path) or path.stem
        document = ParsedDocument(
            source=source,
            title=title,
            text="\n".join(l for s in sheets for l in s.header + s.rows),
            content_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
            raw_path=str(path),
            url=None,
            language="ru",
            published_at=_year_end(title),
        )
        chunks = chunk_sheets(title, sheets, size=settings.chunk_size)
        document_id, n = store(document, chunks)
        total += n
        print(f"  [{i}/{len(files)}] +{n:>4} chunks  {len(sheets)} sheets  {title[:70]}", flush=True)
    print(f"\n  {total} chunks written, {skipped} spreadsheets unreadable\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest saved documents.")
    parser.add_argument(
        "--source",
        default="laws",
        choices=["laws", "nbk_regulations", *sorted(PDF_SOURCES)],
        help="adilet: saved .html/.txt. nbk: .docx resolutions. Others: PDF folders.",
    )
    parser.add_argument(
        "--tables",
        action="store_true",
        help="Ingest the Excel files of a PDF source folder as text tables instead of its PDFs.",
    )
    parser.add_argument(
        "--ocr", action="store_true", help="Read scanned PDFs with the local vision model (slow, cached)."
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Override the input directory (default: RAW_DATA_DIR/<source>).",
    )
    args = parser.parse_args()
    setup_logging(verbose=getattr(args, "debug", False))

    directory = args.dir or (settings.raw_dir / args.source)
    if not directory.is_dir():
        raise SystemExit(
            f"No such directory: {directory}\nSave acts from the Adilet web UI into it, then re-run."
        )

    if args.source in PDF_SOURCES and args.tables:
        return ingest_tables(directory, args.source)
    if args.source in PDF_SOURCES:
        return ingest_pdfs(directory, args.source, ocr=args.ocr)

    if args.source == "nbk_regulations":
        acts, problems = nbk_acts.parse_directory(directory)
        documents = [
            ParsedDocument(
                source=a.source,
                title=a.title,
                text=a.text,
                content_hash=a.content_hash,
                raw_path=a.raw_path,
                url=None,
                language=a.language,
                edition=a.number,
                published_at=a.published_at,
            )
            for a in acts
        ]
    else:
        documents, problems = parse_adilet(directory)

    for problem in problems:
        log.warning("skipped %s", problem)
        print(f"  skipped  {problem}", file=sys.stderr)

    if not documents:
        raise SystemExit(f"No usable documents in {directory}")

    print(f"\ningesting {len(documents)} document(s) from {directory}\n")
    total_chunks = 0
    for document in documents:
        document_id, n = store(document)
        if document_id < 0:
            print(f"  unchanged  {document.title[:60]}")
            continue
        total_chunks += n
        log.info(
            "ingested doc_id=%s lang=%s chunks=%d hash=%s",
            document_id,
            document.language,
            n,
            document.content_hash[:12],
        )
        meta = document.published_at or "no date"
        print(f"  +{n:>4} chunks  [{document.language}] {meta}  {document.title[:60]}")

    print(f"\n  {total_chunks} chunks written")
    for table, count in table_counts().items():
        if count:
            print(f"  {table:<12} {count:>6} rows")
    print()
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
