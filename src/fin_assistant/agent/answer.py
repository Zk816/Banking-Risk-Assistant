import re
from dataclasses import dataclass, field

from fin_assistant.agent import prompts
from fin_assistant.agent.citations import CitationReport, check
from fin_assistant.agent.llm import chat
from fin_assistant.config import settings
from fin_assistant.logging_setup import get_logger
from fin_assistant.retrieval.hybrid import FusedHit, gather_candidates
from fin_assistant.retrieval.rerank import rerank
from fin_assistant.retrieval.vector_search import Hit

log = get_logger(__name__)

_CITATION_RE = re.compile(r"\[(\d+)\]")


@dataclass(frozen=True)
class Answer:
    question: str
    text: str
    hits: list[Hit]
    refused: bool
    fused: list[FusedHit] = field(default_factory=list)
    report: CitationReport | None = None
    raw_text: str | None = None

    @property
    def cited(self) -> list[tuple[int, Hit]]:
        seen: dict[int, Hit] = {}
        for match in _CITATION_RE.finditer(self.text):
            n = int(match.group(1))
            if 1 <= n <= len(self.hits):
                seen[n] = self.hits[n - 1]
        return sorted(seen.items())

    @property
    def invented_markers(self) -> list[int]:
        return sorted(
            {
                n
                for m in _CITATION_RE.finditer(self.text)
                if not (1 <= (n := int(m.group(1))) <= len(self.hits))
            }
        )


def _fit_to_budget(hits: list[Hit]) -> list[Hit]:
    kept: list[Hit] = []
    used = 0
    for hit in hits:
        cost = len(hit.text) + 16
        if used + cost > settings.max_context_chars:
            break
        kept.append(hit)
        used += cost
    return kept


def answer_question(question: str, *, top_k: int | None = None) -> Answer:
    question = question.strip()
    if not question:
        raise ValueError("question must not be empty")

    k = top_k if top_k is not None else settings.top_k
    candidates = gather_candidates(question)
    fused = rerank(question, candidates, top_k=k)
    hits = _fit_to_budget([f.hit for f in fused])
    kept = {h.chunk_id for h in hits}
    fused = [f for f in fused if f.hit.chunk_id in kept]

    if not hits:
        return Answer(question=question, text=prompts.REFUSAL, hits=[], refused=True, fused=[])

    context = prompts.format_context([h.text for h in hits])
    text = chat(
        prompts.ANSWER_SYSTEM,
        prompts.ANSWER_USER.format(context=context, question=question),
    )

    if prompts.REFUSAL in text:
        return Answer(
            question=question,
            text=text,
            hits=hits,
            refused=True,
            fused=fused,
            raw_text=text,
        )

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
