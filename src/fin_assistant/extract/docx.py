import html
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from fin_assistant.logging_setup import get_logger

log = get_logger(__name__)

DOCUMENT_PART = "word/document.xml"

_DROP_ELEMENTS = re.compile(
    r"<(w:drawing|w:pict|mc:AlternateContent|w:object|v:shape)\b.*?</\1>",
    re.DOTALL,
)
_PARAGRAPH_END = re.compile(r"</w:p>")
_LINE_BREAK = re.compile(r"<w:br\b[^>]*/?>")
_TAB = re.compile(r"<w:tab\b[^>]*/?>")
_TAGS = re.compile(r"<[^>]+>")


class DocxError(Exception):
    pass


@dataclass(frozen=True)
class DocxText:
    text: str
    paragraphs: int
    source: Path

    @property
    def chars(self) -> int:
        return len(self.text)


def extract_text(path: Path) -> DocxText:
    try:
        with zipfile.ZipFile(path) as archive:
            if DOCUMENT_PART not in archive.namelist():
                raise DocxError(f"{path.name}: zip without {DOCUMENT_PART}")
            xml = archive.read(DOCUMENT_PART).decode("utf-8", errors="replace")
    except zipfile.BadZipFile as e:
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
    log.info("docx %s -> %d chars, %d paragraphs", path.name, result.chars, result.paragraphs)
    return result


def _xml_to_text(xml: str) -> str:
    xml = _DROP_ELEMENTS.sub(" ", xml)
    xml = _LINE_BREAK.sub("\n", xml)
    xml = _TAB.sub(" ", xml)
    xml = _PARAGRAPH_END.sub("\n", xml)

    text = html.unescape(_TAGS.sub("", xml))

    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
