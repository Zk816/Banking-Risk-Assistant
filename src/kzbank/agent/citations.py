"""Check an answer against the sources it was given.

The prompt *asks* the model to cite and to copy numbers exactly. A prompt is a
request, not a constraint. This module is the constraint.

Two checks, in order of how badly they hurt:

1. **Fabricated markers.** The model saw 5 chunks and wrote [7]. That citation
   points at nothing.
2. **Untraceable numbers.** Every digit sequence in the answer must appear in the
   retrieved text. This is the one that matters: a model that writes 0,08 where
   the law says 0,080 has produced a plausible, wrong, uncheckable number — and
   "0,08" reads exactly like a correct answer.
"""

import re
from dataclasses import dataclass

_MARKER_RE = re.compile(r"\[(\d+)\]")

# A number as it appears in our two kinds of source: Russian legal text writes
# "0,080" and "10 000 000 000"; a tool's JSON writes "513.46". Both decimal marks
# are accepted, and spaces act as thousands separators.
_NUMBER_RE = re.compile(r"\d[\d   ]*(?:[.,]\d+)?")

# Years and small ordinals are almost always structural ("Статья 43", "2026 года",
# "1."), not claims. Checking them produces noise without catching real errors.
_STRUCTURAL = re.compile(r"^\d{1,4}$")

# ...except when the number carries a unit. Observed: asked about VAT against a
# banking corpus, the model answered "ставка НДС составляет 16%" — invented, and
# waved through because "16" is four digits and looked structural. A percentage
# or a currency amount is a claim however short it is.
_UNIT_SUFFIX = re.compile(
    r"\s*(%|процент\w*|пайыз\w*|тенге|теңге|долл\w*|евро|руб\w*)",
    re.IGNORECASE,
)

# Only a comma between digits is a decimal mark; a comma elsewhere is prose.
_DECIMAL_COMMA = re.compile(r"(?<=\d),(?=\d)")


def _normalise(number: str) -> str:
    """Canonical form for comparing numbers written in different styles.

    Strips thousands separators so "10 000" == "10000", and settles on a dot for
    the decimal mark so "447,85" (Russian prose) == "447.85" (a tool's JSON).

    Only the decimal comma is touched, so "0,08" and "0,080" stay different —
    the rounding case this module exists to catch.
    """
    return _DECIMAL_COMMA.sub(".", re.sub(r"[   ]", "", number.strip()))


def parse_markers(text: str) -> list[int]:
    """Every [n] marker in the text, in order of appearance, with repeats."""
    return [int(m.group(1)) for m in _MARKER_RE.finditer(text)]


def _all_numbers(text: str) -> set[str]:
    """Every number in the text, normalised. Used to build the source index."""
    return {
        n for n in (_normalise(m.group(0)) for m in _NUMBER_RE.finditer(text)) if n
    }


def extract_numbers(text: str) -> list[str]:
    """Numbers a reader would treat as a claim.

    Citation markers are removed first, so [1] is never mistaken for the value 1.
    Bare integers up to four digits are skipped as structural — see _STRUCTURAL.
    """
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


def untraceable_numbers(text: str, sources: list[str]) -> list[str]:
    """Numbers stated in `text` that appear in none of `sources`.

    Compared as whole numbers, never as substrings: "0,08" IS a substring of
    "0,080", and that rounding is what this check exists to catch.

    Shared by the retrieval path (sources are chunks) and the tool path (sources
    are the JSON the tools returned). Both have the same rule — a number the
    system cannot attribute is a number it must not state.
    """
    known = _all_numbers(" ".join(sources))

    # Compare in canonical form, but report the spelling the model actually used —
    # an error quoting "0.08" when the answer said "0,08" sends the reader looking
    # for the wrong string.
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
    """What survived checking. `ok` is the only thing callers need to branch on."""

    cited: list[int]
    invented: list[int]
    untraceable_numbers: list[str]
    has_any_citation: bool

    @property
    def ok(self) -> bool:
        """True when every marker resolves and every number is traceable."""
        return not self.invented and not self.untraceable_numbers and self.has_any_citation

    @property
    def reason(self) -> str:
        """Why the answer was rejected. Empty when `ok`."""
        problems = []
        if self.invented:
            problems.append(f"ссылки на несуществующие источники: {self.invented}")
        if self.untraceable_numbers:
            problems.append(f"числа, которых нет в источниках: {self.untraceable_numbers}")
        if not self.has_any_citation:
            problems.append("ответ без единой ссылки на источник")
        return "; ".join(problems)


def check(answer_text: str, sources: list[str]) -> CitationReport:
    """Validate an answer against the chunks the model was shown.

    `sources` is the retrieved text, in the same order the markers refer to:
    marker [1] is sources[0].
    """
    markers = parse_markers(answer_text)
    valid = sorted({n for n in markers if 1 <= n <= len(sources)})
    invented = sorted({n for n in markers if not 1 <= n <= len(sources)})

    # Compared as whole numbers, never as substrings. "0,08" IS a substring of
    # "0,080", and that rounding is precisely what this check exists to catch.
    #
    # Checked against every source shown, not only the cited one: a model that
    # miscites but copies the figure correctly has made a smaller mistake than
    # one that invented the figure.
    untraceable = untraceable_numbers(answer_text, sources)

    return CitationReport(
        cited=valid,
        invented=invented,
        untraceable_numbers=untraceable,
        has_any_citation=bool(valid),
    )
