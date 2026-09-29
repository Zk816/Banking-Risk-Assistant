"""Plain text out of a .docx, without a library.

A .docx is a zip of XML. `word/document.xml` holds the body, and the text lives
in `<w:t>` elements inside `<w:p>` paragraphs. That is enough for legal acts, and
it avoids a dependency whose only job would be unzipping a file.

Two things have to be removed before the text is readable:

  * `<w:drawing>` and `<w:pict>` carry positioning numbers that otherwise land in
    the text as "-135255-381000" — a coordinate pair, extracted as if it were a
    figure in the act.
  * `<mc:AlternateContent>` stores the same content twice, once per renderer, so
    keeping it duplicates whole paragraphs.
"""

import html
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from kzbank.logging_setup import get_logger

log = get_logger(__name__)

DOCUMENT_PART = "word/document.xml"

# Removed whole, including their contents.
_DROP_ELEMENTS = re.compile(
    r"<(w:drawing|w:pict|mc:AlternateContent|w:object|v:shape)\b.*?</\1>",
    re.DOTALL,
)
_PARAGRAPH_END = re.compile(r"</w:p>")
_LINE_BREAK = re.compile(r"<w:br\b[^>]*/?>")
_TAB = re.compile(r"<w:tab\b[^>]*/?>")
_TAGS = re.compile(r"<[^>]+>")


class DocxError(Exception):
    """The file is not a readable .docx."""


@dataclass(frozen=True)
class DocxText:
    """Extracted text plus what it cost to get it."""

    text: str
    paragraphs: int
    source: Path

    @property
    def chars(self) -> int:
        return len(self.text)


def extract_text(path: Path) -> DocxText:
    """Read a .docx and return its body text, one paragraph per line.

    Raises:
        DocxError: if the file is not a zip, or holds no document part.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            if DOCUMENT_PART not in archive.namelist():
                raise DocxError(f"{path.name}: zip without {DOCUMENT_PART}")
            xml = archive.read(DOCUMENT_PART).decode("utf-8", errors="replace")
    except zipfile.BadZipFile as e:
        # .doc is an OLE compound file, not a zip. Reading it needs antiword or
        # libreoffice, neither of which is installed, and neither is worth a
        # dependency for the ~6% of NBK acts still published in the 2003 format.
        raise DocxError(
            f"{path.name}: Word 97-2003 (.doc), not .docx — "
            f"конвертировать через libreoffice --convert-to docx"
        ) from e
    except OSError as e:
        raise DocxError(f"{path.name}: cannot read: {e}") from e

    text = _xml_to_text(xml)
    if not text.strip():
        raise DocxError(f"{path.name}: document part is empty")

    result = DocxText(text=text, paragraphs=text.count("\n") + 1, source=path)
    log.info("docx %s -> %d chars, %d paragraphs",
             path.name, result.chars, result.paragraphs)
    return result


def _xml_to_text(xml: str) -> str:
    """Turn WordprocessingML into text, preserving paragraph boundaries."""
    xml = _DROP_ELEMENTS.sub(" ", xml)
    xml = _LINE_BREAK.sub("\n", xml)
    xml = _TAB.sub(" ", xml)
    xml = _PARAGRAPH_END.sub("\n", xml)

    text = html.unescape(_TAGS.sub("", xml))

    # Word splits a sentence across runs, so a paragraph arrives as fragments on
    # one line; collapse the spaces but keep the newlines.
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
