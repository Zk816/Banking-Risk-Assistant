"""The cognitive loop: Input -> Perception -> Attention -> Memory -> Knowledge -> Output.

One call to `CognitiveAgent.handle` runs the whole chain and returns a Trace with
the six things the assignment asks to show for every example.

Memory is touched twice, which is how it works in any system that has it: READ
right after perception (a follow-up "а у Citigroup?" needs the metric from the
previous turn before anything can be searched) and WRITTEN after the output.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from kzbank.agent import knowledge as kb
from kzbank.agent import prompts
from kzbank.agent.citations import check
from kzbank.agent.llm import chat
from kzbank.agent.memory import LongTermMemory, ShortTermMemory
from kzbank.agent.perception import Perception, perceive
from kzbank.retrieval.hybrid import gather_candidates
from kzbank.retrieval.rerank import rerank
from kzbank.storage.db import fetch_all
from kzbank.tools.bank_metrics import get_exchange_rate
from kzbank.tools.legal_text import MIN_RELEVANCE

LEGAL_TOP_K = 5
LEGAL_CONTEXT = 3
_MY_BANKS = re.compile(r"мои[хм]? банк|my banks", re.I)
_RISK = re.compile(r"риск|risk|проблемн", re.I)


@dataclass
class Trace:
    """Everything one question went through. Printed cell by cell in the notebook."""

    input: str
    perception: Perception
    attention: dict[str, Any] = field(default_factory=dict)
    memory: dict[str, Any] = field(default_factory=dict)
    knowledge: list[kb.Inference] = field(default_factory=list)
    output: str = ""


class CognitiveAgent:
    def __init__(self, user_id: str, *, consent: bool, today: date | None = None) -> None:
        self.today = today or date.today()
        self.short = ShortTermMemory()
        self.long = LongTermMemory(user_id, consent=consent)

    # ------------------------------------------------------------------ main
    def handle(self, text: str) -> Trace:
        p = perceive(text, today=self.today)
        t = Trace(input=text, perception=p)

        # Memory READ: fill a follow-up from this session, a "мои банки" from past ones.
        filled = self.short.fill_gaps(p)
        if _MY_BANKS.search(text):
            focus = self.long.focus_companies()
            p.companies = focus
            filled.append(f"companies <- long-term profile: {focus}")
            p.intent = "risk" if _RISK.search(text) else ("compare" if len(focus) > 1 else "metric")
        elif _RISK.search(text) and p.intent in ("legal", "metric", "compare"):
            p.intent = "risk"
        before = self.long.asked_before(text)
        t.memory["read"] = filled
        t.memory["asked_in_earlier_session"] = (
            f"{before['created_at']:%Y-%m-%d %H:%M}: {before['answer'][:100]}" if before else None)

        handler = {"legal": self._legal, "fx": self._fx, "compliance": self._compliance,
                   "compare": self._compare, "metric": self._compare, "risk": self._risk}[p.intent]
        handler(p, t)

        # Memory WRITE: session context always, long-term only with consent.
        self.short.remember(p, t.output, t.attention.get("selected", []))
        stored = self.long.write(p, t.output)
        t.memory["written_short_term"] = dict(self.short.context)
        t.memory["written_long_term"] = stored if stored else "нет согласия — не записано"
        return t

    # -------------------------------------------------------------- handlers
    def _legal(self, p: Perception, t: Trace) -> None:
        ranked = rerank(p.text, gather_candidates(p.text), top_k=LEGAL_TOP_K)
        rows = [{"score": round(f.rerank_score or 0.0, 4), "article": f.hit.article_ref,
                 "document": f.hit.document_title[:60], "text": f.hit.text[:160]} for f in ranked]
        kept = [f for f in ranked if (f.rerank_score or 0.0) >= MIN_RELEVANCE][:LEGAL_CONTEXT]
        t.attention = {"criterion": f"cross-encoder relevance ≥ {MIN_RELEVANCE}",
                       "ranking": rows, "selected": [f"{f.hit.article_ref}" for f in kept]}
        guard = kb.Inference("R6 grounding", "", ["answer must cite retrieved passages",
                                                  "every number must appear in a passage"])
        if not kept:
            best = rows[0]["score"] if rows else 0.0
            guard.conclusion = f"лучший фрагмент {best} < порога {MIN_RELEVANCE} → отказ"
            t.knowledge = [guard]
            t.output = f"{prompts.REFUSAL}: в корпусе нет документов по этому вопросу."
            return
        sources = [f.hit.text for f in kept]
        text = chat(prompts.ANSWER_SYSTEM,
                    prompts.ANSWER_USER.format(context=prompts.format_context(sources), question=p.text))
        report = check(text, sources)
        guard.conclusion = "ответ прошёл проверку" if report.ok else f"ответ отклонён: {report.reason}"
        t.knowledge = [guard]
        cites = "; ".join(f"[{i}] {f.hit.document_title[:70]}, {f.hit.article_ref}"
                          for i, f in enumerate(kept, 1))
        t.output = (text if report.ok else f"{prompts.REFUSAL} ({report.reason})") + f"\n\nИсточники: {cites}"

    def _fx(self, p: Perception, t: Trace) -> None:
        cur = p.currencies[0] if p.currencies else "USD"
        on = p.on_date or self.today
        r = get_exchange_rate(cur, on.isoformat())
        t.attention = {"criterion": f"курс {cur} на дату {on}; если дата не рабочая — ближайшая предыдущая",
                       "selected": [f"{cur} {r.get('period', on)}"]}
        if not r.get("found"):
            # The NBK does not publish on weekends: attention falls back to the
            # most recent published day before the requested one.
            prev = fetch_all("SELECT max(period) AS d FROM metrics WHERE bin IS NULL AND metric = %s "
                             "AND period <= %s", (f"fx_rate_{cur.lower()}", on))
            if prev and prev[0]["d"]:
                r = get_exchange_rate(cur, prev[0]["d"].isoformat())
                t.attention["selected"] = [f"{cur} {prev[0]['d']} (ближайший опубликованный)"]
        if not r.get("found"):
            t.output = f"Курса {cur} на {on} нет в данных НБ РК."
            return
        latest = date.fromisoformat(r["period"])
        t.knowledge = [kb.rule_freshness("nbk", latest, self.today)]
        value = f"{r['value']:.2f}".replace(".", ",")
        t.output = f"Официальный курс НБ РК на {r['period']}: 1 {cur} = {value} тенге ({r['source']})"

    def _compliance(self, p: Perception, t: Trace) -> None:
        norm = p.ratios[0] if p.ratios else "k2"
        company = p.companies[0] if p.companies else None
        if company and kb.is_kz_bank(company):
            # Kazakh banks: the National Bank publishes k1 / k1-2 / k2 directly.
            verdict = kb.rule_kz_capital(company, norm)
            t.attention = {"criterion": f"последнее значение норматива {norm} этого банка из данных НБ РК",
                           "selected": [f"{company}: {norm}"]}
            t.knowledge = [verdict]
            if "period" in verdict.data:
                t.knowledge.append(kb.rule_freshness("nbk_stats", date.fromisoformat(verdict.data["period"]),
                                                     self.today))
            t.output = verdict.conclusion
            return
        norm = p.ratios[0] if p.ratios else "k1-2"
        mapping = kb.rule_concept_mapping(norm)
        use = mapping.data["substitute"]
        company = p.companies[0] if p.companies else None
        t.attention = {"criterion": "из всех показателей банка нужны только пара «фактический / требуемый» "
                                    "для норматива и последний отчётный период",
                       "selected": [f"{company}: {kb.KZ_NORMS[use]['us_metric']}"]}
        if company is None:
            t.knowledge = [mapping]
            t.output = "Не указан банк для проверки норматива."
            return
        verdict = kb.rule_capital_adequacy(company, use)
        t.knowledge = [mapping, verdict]
        if "period" in verdict.data:
            t.knowledge.append(kb.rule_freshness("edgar", date.fromisoformat(verdict.data["period"]),
                                                 self.today))
        note = f"{mapping.conclusion}\n" if use != norm else ""
        t.output = note + verdict.conclusion + (f"\nИсточник: {verdict.data['source']}"
                                                if "source" in verdict.data else "")

    def _compare(self, p: Perception, t: Trace) -> None:
        metric = p.metrics[0] if p.metrics else "net_income"
        companies = p.companies
        if companies and all(kb.is_kz_bank(c) for c in companies):
            metric = kb.NBK_METRIC.get(metric, metric)     # one source for every Kazakh bank
        if not companies:
            t.output = "Не понял, о какой компании вопрос."
            return
        inf = kb.rule_common_period(companies, metric)
        t.attention = {"criterion": "из всех отчётных периодов берётся последний, общий для всех компаний",
                       "selected": [f"{metric} @ {inf.data.get('period')}"]}
        t.knowledge = [inf]
        if "period" in inf.data:
            source = "edgar_annual" if inf.data.get("annual") else kb.source_of(companies)
            t.knowledge.append(kb.rule_freshness(source, date.fromisoformat(inf.data["period"]), self.today))
        t.output = inf.conclusion

    def _risk(self, p: Perception, t: Trace) -> None:
        # Kazakh banks by default: their 700-Н overdue share is one definition,
        # and ranking it together with US non-accrual ratios would mix two.
        banks = p.companies or kb.kz_banks()
        results = [r for r in (kb.rule_npl_risk(b) for b in banks) if "npl_share" in r.data]
        # Rank only banks reporting the same recent quarter; a bank whose last
        # report is a year old would be ranked on a different point in time.
        newest = max((date.fromisoformat(r.data["period"]) for r in results), default=self.today)
        cutoff = date.fromordinal(newest.toordinal() - 100)
        stale = [r for r in results if date.fromisoformat(r.data["period"]) < cutoff]
        scored = sorted([r for r in results if r not in stale],
                        key=lambda r: r.data["npl_share"], reverse=True)
        trends = {b: kb.rule_trend(b) for b in banks}
        t.attention = {"criterion": f"приоритет по доле проблемных кредитов (порог {kb.NPL_WATCH:.0%}) "
                                    f"и её росту за год (> {kb.TREND_WORSE_PP * 100:.0f} п.п.)",
                       "ranking": [{"bank": r.conclusion.split(':')[0], "npl_share": round(r.data['npl_share'], 4),
                                    "high_risk": r.data["high"],
                                    "yoy_pp": round(trends[r.conclusion.split(':')[0]].data.get("delta", 0) * 100, 2)}
                                   for r in scored],
                       "selected": [r.conclusion.split(':')[0] for r in scored[:3]]}
        # Trends only for banks with a current report: "worsening" about a bank
        # whose last filing is years old would describe the past as the present.
        current = {r.conclusion.split(':')[0] for r in scored}
        worse = [tr for b, tr in trends.items() if b in current and tr.data.get("worse")]
        t.knowledge = (scored[:3] if not p.companies else scored) + worse[:3]
        t.attention["excluded_stale"] = [r.conclusion.split(':')[0] for r in stale]
        top = scored[:3] if not p.companies else scored
        t.output = ("Наибольшая доля проблемных кредитов: " + "; ".join(r.conclusion for r in top)
                    + ("\nКачество ухудшается: " + "; ".join(w.conclusion for w in worse[:3]) if worse else "")
                    + (f"\nНе в рейтинге (последний отчёт старше квартала): {', '.join(t.attention['excluded_stale'])}"
                       if stale else ""))
