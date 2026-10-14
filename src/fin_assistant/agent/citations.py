import re
from dataclasses import dataclass

_MARKER_RE = re.compile(r"\[(\d+)\]")

_NUMBER_RE = re.compile(r"\d[\d   ]*(?:[.,]\d+)?")

_STRUCTURAL = re.compile(r"^\d{1,4}$")

_UNIT_SUFFIX = re.compile(
    r"\s*(%|процент\w*|пайыз\w*|тенге|теңге|долл\w*|евро|руб\w*)",
    re.IGNORECASE,
)

_DECIMAL_COMMA = re.compile(r"(?<=\d),(?=\d)")


def _normalise(number: str) -> str:
    return _DECIMAL_COMMA.sub(".", re.sub(r"[   ]", "", number.strip()))


def parse_markers(text: str) -> list[int]:
    return [int(m.group(1)) for m in _MARKER_RE.finditer(text)]


def _all_numbers(text: str) -> set[str]:
    return {n for n in (_normalise(m.group(0)) for m in _NUMBER_RE.finditer(text)) if n}


def extract_numbers(text: str) -> list[str]:
    without_markers = _MARKER_RE.sub(" ", text)
    found = []
    for match in _NUMBER_RE.finditer(without_markers):
        raw = _normalise(match.group(0))
        if not raw:
            continue
        if _STRUCTURAL.match(raw) and not _UNIT_SUFFIX.match(without_markers, match.end()):
            continue
        found.append(raw)
    return found


_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def _date_variants(text: str) -> set[str]:
    out: set[str] = set()
    for y, m, d in _ISO_DATE.findall(text):
        out |= {_normalise(v) for v in (f"{d}.{m}.{y}", f"{d}.{m}", f"{m}.{y}", f"{int(d)}.{m}.{y}")}
    return out


def untraceable_numbers(text: str, sources: list[str]) -> list[str]:
    joined = " ".join(sources)
    known = _all_numbers(joined) | _date_variants(joined)

    without_markers = _MARKER_RE.sub(" ", text)
    offenders: dict[str, str] = {}
    for match in _NUMBER_RE.finditer(without_markers):
        original = match.group(0).strip()
        canonical = _normalise(original)
        if not canonical:
            continue
        if _STRUCTURAL.match(canonical) and not _UNIT_SUFFIX.match(without_markers, match.end()):
            continue
        if canonical not in known:
            offenders.setdefault(canonical, original)
    return [offenders[k] for k in sorted(offenders)]


@dataclass(frozen=True)
class CitationReport:
    cited: list[int]
    invented: list[int]
    untraceable_numbers: list[str]
    has_any_citation: bool

    @property
    def ok(self) -> bool:
        return not self.invented and not self.untraceable_numbers and self.has_any_citation

    @property
    def reason(self) -> str:
        problems = []
        if self.invented:
            problems.append(f"ссылки на несуществующие источники: {self.invented}")
        if self.untraceable_numbers:
            problems.append(f"числа, которых нет в источниках: {self.untraceable_numbers}")
        if not self.has_any_citation:
            problems.append("ответ без единой ссылки на источник")
        return "; ".join(problems)


def check(answer_text: str, sources: list[str]) -> CitationReport:
    markers = parse_markers(answer_text)
    valid = sorted({n for n in markers if 1 <= n <= len(sources)})
    invented = sorted({n for n in markers if not 1 <= n <= len(sources)})

    untraceable = untraceable_numbers(answer_text, sources)

    return CitationReport(
        cited=valid,
        invented=invented,
        untraceable_numbers=untraceable,
        has_any_citation=bool(valid),
    )
