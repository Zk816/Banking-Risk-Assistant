from typing import Any

from fin_assistant.retrieval.filters import SearchFilter
from fin_assistant.retrieval.hybrid import gather_candidates
from fin_assistant.retrieval.rerank import rerank

from .banks import resolve_bank

DATASETS = {
    "laws": "законы РК (о банках, о НБ, о гарантировании депозитов, о платежах, о банкротстве, ГК)",
    "nbk_regulations": "постановления Правления НБ РК: отчетность, пруденциальные нормативы, план счетов",
    "stability_reports": "Отчеты о финансовой стабильности НБ РК (анализ рисков, причины)",
    "company_reports": "МСФО-отчетность, аудиторские заключения, рейтинговые действия банков и компаний (KASE)",
    "basel_standards": "стандарты Базельского комитета (капитал, LCR, NSFR, NPE, МСФО 9)",
    "nbk_publications": "обзоры инфляции, экономические обзоры, статбюллетени, таблицы кредитов по отраслям и ставкам",
    "deposit_market": "обзоры рынка депозитов и ставки банков (КФГД)",
    "worldbank_reports": "отчеты Всемирного банка: FSAP, экономические обзоры Казахстана",
    "web_official": "официальные страницы из интернета, ранее найденные ассистентом и сохраненные (НК РК, КГД, МФЦА, KASE...)",
}

MIN_RELEVANCE = 0.30


def search_documents(
    query: str,
    datasets: list[str] | None = None,
    bank: str | None = None,
    doc_types: list[str] | None = None,
    top_k: int = 6,
    query_en: str | None = None,
) -> dict[str, Any]:
    bin_ = None
    if bank:
        hit = resolve_bank(bank)
        if hit:
            bin_ = hit[0]
    filt = SearchFilter(
        sources=tuple(d for d in (datasets or []) if d in DATASETS),
        bin=bin_,
        doc_types=tuple(doc_types or []),
    )
    queries = [query] + ([query_en] if query_en and query_en.strip() != query.strip() else [])
    pool, seen = [], set()
    for q in queries:
        for f in gather_candidates(q, depth=30, filters=filt):
            if f.hit.chunk_id not in seen:
                seen.add(f.hit.chunk_id)
                pool.append(f)
    if not pool and not filt.empty:
        pool = gather_candidates(query, depth=30)
    scored: dict[int, Any] = {}
    for q in queries:
        for f in rerank(q, pool, top_k=len(pool)):
            if f.hit.chunk_id not in scored or (f.rerank_score or 0) > (
                scored[f.hit.chunk_id].rerank_score or 0
            ):
                scored[f.hit.chunk_id] = f
    ranked = sorted(scored.values(), key=lambda f: -(f.rerank_score or 0))[:top_k]
    ranked = [f for f in ranked if (f.rerank_score or 0) >= MIN_RELEVANCE]
    if not ranked:
        return {"found": False, "reason": "В источниках нет документов по этому вопросу."}
    return {
        "found": True,
        "passages": [
            {
                "n": i + 1,
                "citation": f.hit.citation,
                "dataset": f.hit.source,
                "date": str(f.hit.published_at) if f.hit.published_at else None,
                "relevance": round(f.rerank_score or 0, 3),
                "text": f.hit.text[:1500],
            }
            for i, f in enumerate(ranked)
        ],
    }
