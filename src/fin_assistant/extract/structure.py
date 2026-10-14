import re
from dataclasses import dataclass

_RU_ARTICLE = re.compile(r"^[ \t]*Статья\s+(\d+(?:-\d+)?)\s*\.?", re.MULTILINE)
_KZ_ARTICLE = re.compile(r"^[ \t]*(\d+(?:-\d+)?)-бап\s*\.?", re.MULTILINE)

_RU_CHAPTER = re.compile(r"^[ \t]*Глава\s+(\d+(?:-\d+)?)\s*\.?", re.MULTILINE)
_KZ_CHAPTER = re.compile(r"^[ \t]*(\d+(?:-\d+)?)-тарау\s*\.?", re.MULTILINE)

_RU_POINT = re.compile(r"^[ \t]*(\d{1,3})\.\s+(?=[А-ЯЁA-ZӘҒҚҢӨҰҮҺІ])", re.MULTILINE)
_KZ_POINT = re.compile(r"^[ \t]*(\d{1,3})-тармақ\s*\.?", re.MULTILINE)
_ANNEX = re.compile(r"^[ \t]*(?:Приложение|Қосымша)\s*(\d+)?", re.MULTILINE)

_SEC_ITEM = re.compile(r"^[ \t]*Item\s+(\d{1,2}[A-C]?)\.?\s+(?=[A-Z][a-z])", re.MULTILINE)

MIN_SECTION_CHARS = 60

MAX_HEADING_CHARS = 200

MIN_ARTICLES_FOR_LAW = 1

MIN_ITEMS_FOR_FILING = 4

MIN_FILING_SECTION_CHARS = 500


@dataclass(frozen=True)
class Section:
    ref: str
    chapter: str | None
    text: str
    char_start: int
    char_end: int

    @property
    def heading(self) -> str:
        first_line = self.text.split("\n", 1)[0].strip()
        return first_line if len(first_line) <= MAX_HEADING_CHARS else ""


def _find_markers(text: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for match in _RU_ARTICLE.finditer(text):
        found.append((match.start(), f"Статья {match.group(1)}"))
    for match in _KZ_ARTICLE.finditer(text):
        found.append((match.start(), f"{match.group(1)}-бап"))
    found.sort(key=lambda pair: pair[0])
    return found


def _find_point_markers(text: str) -> list[tuple[int, str]]:
    annexes = [(m.start(), m.group(1)) for m in _ANNEX.finditer(text)]

    def annex_at(offset: int) -> str | None:
        current = None
        for start, number in annexes:
            if start > offset:
                break
            current = f"Приложение {number}" if number else "Приложение"
        return current

    found: list[tuple[int, str]] = []
    for pattern, template in ((_RU_POINT, "пункт {}"), (_KZ_POINT, "{}-тармақ")):
        for match in pattern.finditer(text):
            ref = template.format(match.group(1))
            annex = annex_at(match.start())
            found.append((match.start(), f"{annex}, {ref}" if annex else ref))

    found.sort(key=lambda pair: pair[0])
    return found


def _find_item_markers(text: str) -> list[tuple[int, str]]:
    return [(m.start(), f"Item {m.group(1)}") for m in _SEC_ITEM.finditer(text)]


def detect_kind(text: str) -> str:
    if len(_find_item_markers(text)) >= MIN_ITEMS_FOR_FILING:
        return "sec_filing"
    return "law" if len(_find_markers(text)) >= MIN_ARTICLES_FOR_LAW else "resolution"


def _chapter_at(text: str, offset: int) -> str | None:
    latest: tuple[int, str] | None = None
    for pattern, template in ((_RU_CHAPTER, "Глава {}"), (_KZ_CHAPTER, "{}-тарау")):
        for match in pattern.finditer(text, 0, offset):
            if latest is None or match.start() > latest[0]:
                latest = (match.start(), template.format(match.group(1)))
    return latest[1] if latest else None


def split_articles(text: str) -> list[Section]:
    kind = detect_kind(text)
    if kind == "sec_filing":
        markers = _find_item_markers(text)
    elif kind == "law":
        markers = _find_markers(text)
    else:
        markers = _find_point_markers(text)
    if not markers:
        return [
            Section(
                ref="Документ",
                chapter=None,
                text=text.strip(),
                char_start=0,
                char_end=len(text),
            )
        ]

    sections: list[Section] = []

    floor = MIN_FILING_SECTION_CHARS if kind == "sec_filing" else MIN_SECTION_CHARS

    preamble = "" if kind == "sec_filing" else text[: markers[0][0]].strip()
    if len(preamble) >= floor:
        sections.append(
            Section(
                ref="Преамбула",
                chapter=None,
                text=preamble,
                char_start=0,
                char_end=markers[0][0],
            )
        )

    for i, (start, ref) in enumerate(markers):
        end = markers[i + 1][0] if i + 1 < len(markers) else len(text)
        body = text[start:end].strip()
        if not body or (kind == "sec_filing" and len(body) < floor):
            continue
        sections.append(
            Section(
                ref=ref,
                chapter=_chapter_at(text, start),
                text=body,
                char_start=start,
                char_end=end,
            )
        )

    return sections
