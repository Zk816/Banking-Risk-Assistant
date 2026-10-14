from typing import Any

from fin_assistant.logging_setup import get_logger
from fin_assistant.retrieval.hybrid import gather_candidates
from fin_assistant.retrieval.rerank import rerank

log = get_logger(__name__)

MAX_SNIPPETS = 5
MAX_SNIPPET_CHARS = 700

MIN_RELEVANCE = 0.30


def search_legal_text(query: str) -> dict[str, Any]:
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
