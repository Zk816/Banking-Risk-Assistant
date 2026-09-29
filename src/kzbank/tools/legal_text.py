"""Retrieval, exposed as a tool the model can call.

The text path already exists — hybrid search plus a reranker. Wrapping it as a
tool is what turns two separate pipelines into one system: the model sees both
`get_exchange_rate` and `search_legal_text` and picks.

That choice *is* the router. No separate classifier, no rules table — a question
about a rate leads to the metrics tool, a question about what the law says leads
to here, and a question needing both leads to both calls in sequence.

What the tool returns is text, not an answer. The model still has to read it and
the same grounding rule still applies afterwards.
"""

from typing import Any

from kzbank.config import settings
from kzbank.logging_setup import get_logger
from kzbank.retrieval.hybrid import gather_candidates
from kzbank.retrieval.rerank import rerank

log = get_logger(__name__)

# Tool results go into the model's context, so they have to stay small. Five
# short articles is roughly what a 7B can hold onto while also reasoning.
MAX_SNIPPETS = 5
MAX_SNIPPET_CHARS = 700

# Retrieval always returns its top five, however bad they are. Asked about
# insurance companies against a banking corpus, it returns the bank capital
# article — and the model then stated the bank's figure as the insurer's,
# citing a real article, with a number that really is in the source. Every
# string-level check passed. The claim was false.
#
# The reranker already knew. Measured on this corpus:
#     in corpus        0.96 … 0.996
#     adjacent, absent 0.04 … 0.099
#     unrelated        0.0000 … 0.0005
# Three orders of magnitude, and `search_legal_text` was throwing the number away.
#
# PROVISIONAL. This is an absolute threshold on a cross-encoder score, which
# Step 4's notes warn against — the warning was about *fine* distinctions
# (is 0.6 better than 0.4?), not about a floor with a 10x gap either side. It is
# still fitted to seven queries on a ten-chunk corpus and must be re-measured on
# real data.
MIN_RELEVANCE = 0.30


def search_legal_text(query: str) -> dict[str, Any]:
    """Find passages of banking law relevant to `query`.

    Returns numbered snippets with their article reference, so the model can cite
    one. `found: False` means retrieval came back empty — the model must relay
    that rather than answer from memory.
    """
    candidates = gather_candidates(query)
    hits = rerank(query, candidates, top_k=MAX_SNIPPETS)

    if not hits:
        log.info("tool search_legal_text empty chars=%d", len(query))
        return {"found": False, "error": "По этому запросу ничего не найдено"}

    best = hits[0].rerank_score or 0.0
    if best < MIN_RELEVANCE:
        log.info("tool search_legal_text below floor best=%.4f chars=%d", best, len(query))
        return {
            "found": False,
            "error": (
                "В корпусе нет документов по этому вопросу. Ближайшие найденные "
                "статьи относятся к другой теме — не используй их."
            ),
            "best_relevance": round(best, 4),
        }

    return {
        "found": True,
        "snippets": [
            {
                "n": i,
                "article": fused.hit.article_ref or f"фрагмент {fused.hit.ordinal}",
                "document": fused.hit.document_title,
                # Kept so the decision stays inspectable, and so the model can see
                # which snippet the reranker actually rated.
                "relevance": round(fused.rerank_score or 0.0, 4),
                "text": fused.hit.text[:MAX_SNIPPET_CHARS],
            }
            for i, fused in enumerate(hits, start=1)
        ],
    }


SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_legal_text",
        "description": (
            "Поиск по текстам банковского законодательства Казахстана. "
            "Используй для вопросов о том, ЧТО ГОВОРИТ ЗАКОН: требования, "
            "определения, нормативы, обязанности банков, пруденциальные "
            "коэффициенты k1/k1-2/k2, неработающие займы. "
            "НЕ используй для курсов валют."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Поисковый запрос на языке вопроса пользователя",
                }
            },
            "required": ["query"],
        },
    },
}
