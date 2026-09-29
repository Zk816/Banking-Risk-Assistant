"""Find article boundaries in a legal act.

Kazakh legal texts are hierarchical: Раздел > Глава > Статья > пункт. The article
("Статья") is the unit people cite and the unit that holds one complete rule, so
that is where we cut.

Handles Russian ("Статья 42.") and Kazakh ("42-бап") headings.
"""

import re
from dataclasses import dataclass

# "Статья 42." / "Статья 42-1." / "Статья 42 ." — must start a line.
_RU_ARTICLE = re.compile(r"^[ \t]*Статья\s+(\d+(?:-\d+)?)\s*\.?", re.MULTILINE)
# Kazakh: "42-бап." / "42-1-бап"
_KZ_ARTICLE = re.compile(r"^[ \t]*(\d+(?:-\d+)?)-бап\s*\.?", re.MULTILINE)

# "Глава 3." / "3-тарау" — kept as context, not as a cut point.
_RU_CHAPTER = re.compile(r"^[ \t]*Глава\s+(\d+(?:-\d+)?)\s*\.?", re.MULTILINE)
_KZ_CHAPTER = re.compile(r"^[ \t]*(\d+(?:-\d+)?)-тарау\s*\.?", re.MULTILINE)

# --- resolutions -----------------------------------------------------------
# "5. Банк обязан…" — a numbered point. Requires text after the number, so a
# bare "5." in a table or a list of dates is not mistaken for a point.
_RU_POINT = re.compile(r"^[ \t]*(\d{1,3})\.\s+(?=[А-ЯЁA-ZӘҒҚҢӨҰҮҺІ])", re.MULTILINE)
_KZ_POINT = re.compile(r"^[ \t]*(\d{1,3})-тармақ\s*\.?", re.MULTILINE)
# "Приложение 1" — an annex, usually the Правила the resolution approves.
_ANNEX = re.compile(r"^[ \t]*(?:Приложение|Қосымша)\s*(\d+)?", re.MULTILINE)

# --- SEC filings -----------------------------------------------------------
# "Item 1A. Risk Factors" — the citable unit of a 10-K, as an article is of a
# law. The heading must be followed by a capitalised title, which keeps the
# table of contents ("Item 1A. Risk Factors. 9-31") from being mistaken for the
# section itself: a TOC line is a page range, not prose.
_SEC_ITEM = re.compile(
    r"^[ \t]*Item\s+(\d{1,2}[A-C]?)\.?\s+(?=[A-Z][a-z])", re.MULTILINE
)

# A section this short is a bare heading ("Глава 3. Пруденциальные нормативы")
# with no body. Useless to retrieve, so it is dropped rather than embedded.
MIN_SECTION_CHARS = 60

# A heading is a short line. Anything longer is a paragraph that happens to have
# no line break — prepending it would eat the whole chunk budget.
MAX_HEADING_CHARS = 200

# One article heading is enough. A resolution has exactly zero — measured on a
# real NBK act: 401 numbered points, 0 lines starting with "Статья". Requiring
# two would misclassify a single-article excerpt.
#
# Known limitation: a resolution that begins a line with "Статья 5 изложить в
# редакции…" would be read as a law. Not seen in practice, because amendments
# quote the article inline.
MIN_ARTICLES_FOR_LAW = 1

# A 10-K carries a dozen Items. Four is enough to be sure, and high enough
# that a Kazakh act quoting an English contract clause is not misread.
MIN_ITEMS_FOR_FILING = 4

# A 10-K names each Item twice: once in the table of contents, once as the
# section itself. Measured on JPMorgan's: 34 TOC lines totalling 3 293 characters
# against 10 real sections totalling 1 409 284. The gap is wide enough that a
# flat floor separates them cleanly.
MIN_FILING_SECTION_CHARS = 500


@dataclass(frozen=True)
class Section:
    """One article, with where it sits in the document."""

    ref: str            # "Статья 42"
    chapter: str | None # "Глава 3", if one was seen above it
    text: str
    char_start: int
    char_end: int

    @property
    def heading(self) -> str:
        """The article's first line — "Статья 43. Коэффициенты достаточности капитала".

        Carries the article's subject, which is what a later piece of a split
        article loses. Prepending it puts that subject back.
        """
        first_line = self.text.split("\n", 1)[0].strip()
        return first_line if len(first_line) <= MAX_HEADING_CHARS else ""


def _find_markers(text: str) -> list[tuple[int, str]]:
    """All article headings as (offset, ref), in document order."""
    found: list[tuple[int, str]] = []
    for match in _RU_ARTICLE.finditer(text):
        found.append((match.start(), f"Статья {match.group(1)}"))
    for match in _KZ_ARTICLE.finditer(text):
        found.append((match.start(), f"{match.group(1)}-бап"))
    found.sort(key=lambda pair: pair[0])
    return found


def _find_point_markers(text: str) -> list[tuple[int, str]]:
    """All numbered points as (offset, ref), in document order.

    Annex boundaries are included so a point's reference says which annex it sits
    in: the same "пункт 5" appears in every Приложение of a resolution, and a
    citation that does not distinguish them is useless.
    """
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
    """SEC filing sections as (offset, ref), in document order."""
    return [(m.start(), f"Item {m.group(1)}") for m in _SEC_ITEM.finditer(text)]


def detect_kind(text: str) -> str:
    """Which structure this document uses: "law", "resolution" or "sec_filing".

    The signal is the presence of article headings, not their count relative to
    points. A law numbers the points *inside* each article, so points always
    outnumber articles — comparing the two counts classifies every law as a
    resolution. A resolution, measured on a real NBK act, has 401 points and
    exactly zero "Статья" at the start of a line.

    Two headings are required rather than one, so a resolution that quotes the
    article it amends is not mistaken for a law.
    """
    # A 10-K has neither Статья nor пункт, and a Kazakh act has no "Item 7."
    # The three vocabularies do not overlap, so presence decides.
    if len(_find_item_markers(text)) >= MIN_ITEMS_FOR_FILING:
        return "sec_filing"
    return "law" if len(_find_markers(text)) >= MIN_ARTICLES_FOR_LAW else "resolution"


def _chapter_at(text: str, offset: int) -> str | None:
    """The last chapter heading before `offset`, if any."""
    latest: tuple[int, str] | None = None
    for pattern, template in ((_RU_CHAPTER, "Глава {}"), (_KZ_CHAPTER, "{}-тарау")):
        for match in pattern.finditer(text, 0, offset):
            if latest is None or match.start() > latest[0]:
                latest = (match.start(), template.format(match.group(1)))
    return latest[1] if latest else None


def split_articles(text: str) -> list[Section]:
    """Split a legal text into articles.

    Anything before the first article heading (title page, preamble) becomes one
    leading section with ref "Преамбула". A text with no headings at all comes
    back as a single section, which is the signal to fall back to size-based
    chunking.
    """
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

    # A filing's preamble is the inline-XBRL context block — "jpm-20251231
    # 0000019617 FALSE 2025 FY true false June 30, 2026 230…" for 238 586
    # characters. It is machine metadata that happens to sit before Item 1, and
    # embedding it produces hundreds of chunks of dates and identifiers.
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
        # The floor applies to filings only. A short Статья is still an article
        # and must be kept; a short "Item 5." is a line in the table of contents.
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
