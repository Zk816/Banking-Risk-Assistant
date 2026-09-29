"""Question in, grounded answer out.

retrieve -> build context -> generate -> report what was used.

The contract: every number in the answer must come from a retrieved chunk, and
if the chunks do not contain the answer the system says so. Step 1 enforces this
with the prompt alone. That is not enough, and proving it isn't is the point —
Step 5 adds code that validates citations against what was actually retrieved.
"""

import re
from dataclasses import dataclass, field

from kzbank.agent import prompts
from kzbank.agent.citations import CitationReport, check
from kzbank.agent.llm import chat
from kzbank.config import settings
from kzbank.retrieval.hybrid import FusedHit, gather_candidates
from kzbank.logging_setup import get_logger
from kzbank.retrieval.rerank import rerank
from kzbank.retrieval.vector_search import Hit


log = get_logger(__name__)

_CITATION_RE = re.compile(r"\[(\d+)\]")


@dataclass(frozen=True)
class Answer:
    """A generated answer plus the evidence it was given."""

    question: str
    text: str
    hits: list[Hit]
    refused: bool
    # Per-hit provenance from fusion: which retriever found it, at what rank.
    # Empty only when nothing was retrieved.
    fused: list[FusedHit] = field(default_factory=list)
    # What validation found. None only when the model was never called.
    report: CitationReport | None = None
    # What the model actually said, kept when the answer was rejected so the
    # failure can be inspected instead of disappearing.
    raw_text: str | None = None

    @property
    def cited(self) -> list[tuple[int, Hit]]:
        """The (marker, hit) pairs the answer actually cites.

        Retrieving five chunks and using one is normal. Listing all five as
        "sources" would imply evidence that was never used, so we report only
        the markers that appear in the answer text.

        Markers pointing outside the retrieved set are dropped — a 7B model does
        occasionally invent [7] when it saw five chunks.
        """
        seen: dict[int, Hit] = {}
        for match in _CITATION_RE.finditer(self.text):
            n = int(match.group(1))
            if 1 <= n <= len(self.hits):
                seen[n] = self.hits[n - 1]
        return sorted(seen.items())

    @property
    def invented_markers(self) -> list[int]:
        """Citation markers with no corresponding retrieved chunk.

        Non-empty means the model fabricated a source. Step 5 turns this into a
        hard failure; for now it is surfaced so you can see how often it happens.
        """
        return sorted(
            {
                n
                for m in _CITATION_RE.finditer(self.text)
                if not (1 <= (n := int(m.group(1))) <= len(self.hits))
            }
        )


def _fit_to_budget(hits: list[Hit]) -> list[Hit]:
    """Drop the weakest hits until the context fits MAX_CONTEXT_CHARS.

    Hits arrive best-first, so truncating from the end discards the least
    relevant evidence.
    """
    kept: list[Hit] = []
    used = 0
    for hit in hits:
        cost = len(hit.text) + 16  # rough allowance for the "[n] " wrapper
        if used + cost > settings.max_context_chars:
            break
        kept.append(hit)
        used += cost
    return kept


def answer_question(question: str, *, top_k: int | None = None) -> Answer:
    """Retrieve, then answer strictly from what was retrieved.

    Raises:
        ValueError: if the question is empty.
        RuntimeError: if the model server is unreachable.
    """
    question = question.strip()
    if not question:
        raise ValueError("question must not be empty")

    k = top_k if top_k is not None else settings.top_k
    # Retrieve wide, then let the cross-encoder pick. Hybrid decides what is
    # plausible; the reranker decides what is actually relevant.
    candidates = gather_candidates(question)
    fused = rerank(question, candidates, top_k=k)
    hits = _fit_to_budget([f.hit for f in fused])
    # Keep fusion provenance aligned with the hits that survived the budget.
    kept = {h.chunk_id for h in hits}
    fused = [f for f in fused if f.hit.chunk_id in kept]

    # Nothing retrieved means nothing to ground on. Do not ask the model —
    # it would answer from its own weights, which is the failure we exist to prevent.
    if not hits:
        return Answer(question=question, text=prompts.REFUSAL, hits=[], refused=True, fused=[])

    context = prompts.format_context([h.text for h in hits])
    text = chat(
        prompts.ANSWER_SYSTEM,
        prompts.ANSWER_USER.format(context=context, question=question),
    )

    if prompts.REFUSAL in text:
        return Answer(
            question=question, text=text, hits=hits, refused=True,
            fused=fused, raw_text=text,
        )

    # The prompt asked for grounding; this decides whether it happened. An answer
    # that cites a source it was not given, or states a number that appears in no
    # source, is refused rather than shown — a wrong number reads exactly like a
    # right one, so returning it with a warning attached is not good enough.
    report = check(text, [h.text for h in hits])
    if not report.ok:
        log.warning("answer rejected: %s", report.reason)
        return Answer(
            question=question,
            text=f"{prompts.REFUSAL} (ответ не прошёл проверку: {report.reason})",
            hits=hits,
            refused=True,
            fused=fused,
            report=report,
            raw_text=text,
        )

    return Answer(
        question=question,
        text=text,
        hits=hits,
        refused=False,
        fused=fused,
        report=report,
        raw_text=text,
    )
