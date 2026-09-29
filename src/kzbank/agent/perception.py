"""Perception for a user query: raw text in, structured signals out.

The offline half of perception (documents -> clean text -> chunks -> embeddings)
lives in extract/ and retrieval/embed.py. This is the online half: what does
one question contain? Companies, financial concepts, dates, currencies, and
from those an intent.

Rules and dictionaries, not a model, on purpose. Every signal must be explainable
in a notebook cell ("matched 'прибыль' -> net_income"), and a 7B model asked to
extract entities invents companies that are not in the database.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache

from kzbank.storage.db import fetch_all

# Financial concepts as they appear in questions, RU and EN, -> CONCEPTS keys.
METRIC_WORDS: dict[str, str] = {
    r"прибыл|net income|profit|earnings": "net_income",
    r"актив|assets": "assets",
    r"депозит|вклад|deposits": "deposits",
    r"собственн\w* капитал|equity": "equity",
    r"обязательств|liabilities": "liabilities",
    r"процентн\w* доход|interest income": "interest_income",
    r"ссудн|займ|кредитн\w* портфел|loans": "loans_net",
    r"резерв|провизи|allowance": "credit_loss_allowance",
    r"просроч|проблемн\w* кредит|overdue|npl": "overdue_loans",
}

# Capital-ratio names. Kazakh norms (k1, k1-2, k2) and their US names.
RATIO_WORDS: dict[str, str] = {
    r"\bk1-2\b|\bк1-2\b|tier\s*1|капитал\w* первого уровня": "k1-2",
    r"\bk2\b|\bк2\b|total capital|совокупн\w* капитал": "k2",
    r"\bk1\b|\bк1\b|cet\s*1|основн\w* капитал": "k1",
}

CURRENCY_WORDS: dict[str, str] = {
    r"доллар|usd|\$": "USD", r"евро|eur|€": "EUR", r"рубл|rub": "RUB",
    r"юан|cny": "CNY", r"фунт|gbp": "GBP", r"драм|amd": "AMD",
}

_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DOT_DATE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")
_YEAR = re.compile(r"\b(20\d{2})\s*(?:год|г\.|year)?")
_FOLLOW_UP = re.compile(r"^\s*(а|и|а что|what about|and)\b", re.IGNORECASE)

# Corporate suffixes stripped to make a matchable alias: "JPMORGAN CHASE & CO" -> "jpmorgan chase".
_SUFFIX = re.compile(
    r"\b(inc|corp|corporation|co|company|group|financial|services|holdings|bancorp|"
    r"bancorporation|bancshares|the|of|usa|national association|n\.a)\b\.?|[,&./]", re.I)

# Short names people actually type, keyed to the CIK so they cannot drift.
# Bank of America needs this: for CIK 70858 the SEC API's entity name is
# "BofA Finance LLC" (a co-registrant), though the figures are the parent's.
EXTRA_ALIASES = {
    "bank of america": "CIK0000070858", "bofa": "CIK0000070858",
    "jpmorgan": "CIK0000019617", "jp morgan": "CIK0000019617",
    "citi": "CIK0000831001", "goldman": "CIK0000886982", "wells fargo": "CIK0000072971",
    "amex": "CIK0000004962", "schwab": "CIK0000316709", "bny mellon": "CIK0001390777",
}


# Kazakh banks (KASE issuer code -> ways people write the name). Mapped to the
# stored entity through the code, so a renamed bank keeps its history.
KZ_ALIASES: dict[str, tuple[str, ...]] = {
    "HSBK": ("halyk", "халык", "народный банк"),
    "CCBN": ("bcc", "центркредит", "центр кредит", "bank centercredit"),
    "ASBN": ("forte", "форте"),
    "FFBN": ("freedom bank", "фридом", "freedom"),
    "EUBN": ("евразийский", "eurasian", "eubank"),
    "HCBN": ("home credit", "хоум кредит"),
    "BERK": ("bereke", "береке"),
    "INBN": ("rbk", "рбк"),
    "NRBN": ("nurbank", "нурбанк"),
    "ATBN": ("altyn", "алтын"),
    "TSBN": ("alatau city", "алатау сити", "alatau"),
    "JSBN": ("otbasy", "отбасы"),
    "MFKM": ("kmf",),
}


# Banks known only from the National Bank's statistics (no KASE listing), by the
# canonical name they are stored under -> ways people write them.
NBK_ALIASES: dict[str, tuple[str, ...]] = {
    "Kaspi Bank": ("kaspi", "каспи"),
    "Citibank Kazakhstan": ("citibank", "ситибанк"),
    "Bank of China Kazakhstan": ("банк китая", "bank of china"),
    "VTB Kazakhstan": ("втб", "vtb"),
    "Shinhan Bank Kazakhstan": ("шинхан", "shinhan"),
    "ICBC Almaty": ("icbc", "промышленный банк китая"),
    "Zaman Bank": ("заман", "zaman"),
    "KZI Bank": ("кзи банк", "зираат"),
    "казкоммерцбанк": ("казкоммерцбанк", "kazkommertsbank", "kazkom", "ккб"),
    "BTA Bank": ("бта", "bta bank"),
    "delta bank": ("delta bank", "дельта банк"),
    "tengri bank": ("tengri", "тенгри"),
    "qazaq banki": ("qazaq banki", "казак банки"),
    "asiacredit bank": ("asiacredit", "азиакредит"),
    "эксимбанк казахстан": ("эксимбанк",),
    "банк астаны": ("банк астаны",),
    "казинвестбанк": ("казинвестбанк",),
    "capital bank kazakhstan": ("capital bank",),
    "атфбанк": ("атфбанк", "atf"),
    "альфа-банк": ("альфа-банк", "alfa-bank"),
}


@dataclass
class Perception:
    """Everything the system could read out of one question."""

    text: str
    intent: str                                   # legal | metric | compare | compliance | fx
    companies: list[str] = field(default_factory=list)   # entity names as stored
    metrics: list[str] = field(default_factory=list)
    ratios: list[str] = field(default_factory=list)
    currencies: list[str] = field(default_factory=list)
    on_date: date | None = None
    year: int | None = None
    follow_up: bool = False
    evidence: list[str] = field(default_factory=list)    # why each signal fired


# First words too common to identify a bank on their own ("First Horizon", "First Citizens").
_GENERIC_FIRST = {"first", "bank", "state", "american", "national", "old", "western", "east",
                  "capital", "citizens", "discover", "popular", "valley", "associated"}
# EDGAR appends a state tag to some names: "/DE/", "/MN". Only at a word end,
# so "Cullen/Frost" keeps its "fr".
_STATE_TAG = re.compile(r"\s*/[a-z]{2}(?:/|$)|/")
_STOP_END = re.compile(r"\b(of|the|and|new)$")


def _clean(name: str) -> str:
    return re.sub(r"\s+", " ", _STATE_TAG.sub(" ", name.lower())).strip()


@lru_cache(maxsize=1)
def company_aliases() -> dict[str, str]:
    """alias (lowercase) -> stored entity name, for every EDGAR company.

    EDGAR names are messy ("WELLS        FARGO & COMPANY/MN", "BANK OF AMERICA
    CORP /DE/"), so each name yields several aliases: the name without legal
    suffixes, its first three and first two words, and the first word when it is
    distinctive. Longer aliases are tried first at match time.
    """
    rows = fetch_all("SELECT bin, name_en FROM entities WHERE type = 'edgar' AND name_en IS NOT NULL")
    by_cik = {r["bin"]: r["name_en"] for r in rows}
    kz = _kz_names()
    aliases: dict[str, str] = {short: by_cik[cik] for short, cik in EXTRA_ALIASES.items()
                               if cik in by_cik}
    for r in rows:
        name = r["name_en"]
        plain = re.sub(r"[,&.]", " ", _clean(name))
        words = plain.split()
        stripped = re.sub(r"\s+", " ", _SUFFIX.sub(" ", plain)).strip()
        variants = {stripped, " ".join(words[:3]), " ".join(words[:2])}
        if words and words[0] not in _GENERIC_FIRST and len(words[0]) >= 5:
            variants.add(words[0])
        for v in variants:
            # "bank of" is not a name. First company to claim an alias keeps it.
            if len(v) >= 4 and not _STOP_END.search(v) and v not in aliases:
                aliases[v] = name
    for code, words in KZ_ALIASES.items():
        if code in kz:
            for w in words:
                aliases[w] = kz[code]          # Kazakh names win over EDGAR look-alikes
    nbk = {r["name_ru"] for r in fetch_all("SELECT name_ru FROM entities WHERE type = 'nbk_bank'")}
    for name in nbk:
        if len(name) >= 5:
            aliases.setdefault(name.lower(), name)
    for name, words in NBK_ALIASES.items():
        if name in nbk:
            for w in words:
                aliases[w] = name
    return aliases


def _kz_names() -> dict[str, str]:
    """KASE code -> stored bank name, for banks loaded from KASE."""
    from kzbank.ingest.kase import BANKS   # local: keeps perception importable without httpx
    stored = {r["name_ru"] for r in fetch_all("SELECT name_ru FROM entities WHERE type = 'kase'")}
    return {code: name for code, name in BANKS.items() if name in stored}


def _match_all(table: dict[str, str], text: str, evidence: list[str], kind: str) -> list[str]:
    found: list[str] = []
    for pattern, value in table.items():
        m = re.search(pattern, text, re.IGNORECASE)
        if m and value not in found:
            found.append(value)
            evidence.append(f"{kind}: '{m.group(0)}' -> {value}")
    return found


def _companies(text: str, evidence: list[str]) -> list[str]:
    low = text.lower()
    hits: list[tuple[int, str]] = []
    # Longest alias first, so "bank of america" wins over a shorter overlapping one.
    for alias, name in sorted(company_aliases().items(), key=lambda kv: -len(kv[0])):
        pos = low.find(alias)
        if pos >= 0 and name not in [n for _, n in hits]:
            hits.append((pos, name))
            evidence.append(f"company: '{alias}' -> {name}")
            low = low.replace(alias, " " * len(alias))
    return [name for _, name in sorted(hits)]


def _date(text: str, today: date, evidence: list[str]) -> tuple[date | None, int | None]:
    low = text.lower()
    relative = {"позавчера": 2, "вчера": 1, "сегодня": 0, "yesterday": 1, "today": 0}
    for word, days in relative.items():
        if word in low:
            evidence.append(f"date: '{word}' -> {today - timedelta(days=days)} (today={today})")
            return today - timedelta(days=days), None
    if m := _ISO_DATE.search(text):
        d = date(int(m[1]), int(m[2]), int(m[3]))
        evidence.append(f"date: '{m[0]}' -> {d}")
        return d, None
    if m := _DOT_DATE.search(text):
        d = date(int(m[3]), int(m[2]), int(m[1]))
        evidence.append(f"date: '{m[0]}' -> {d}")
        return d, None
    if m := _YEAR.search(text):
        evidence.append(f"year: '{m[0].strip()}' -> {m[1]}")
        return None, int(m[1])
    return None, None


def perceive(text: str, *, today: date | None = None) -> Perception:
    """Read one question into structured signals. Never calls a model."""
    today = today or date.today()
    ev: list[str] = []
    companies = _companies(text, ev)
    metrics = _match_all(METRIC_WORDS, text, ev, "metric")
    ratios = _match_all(RATIO_WORDS, text, ev, "ratio")
    # "k1-2" also matches the k1 pattern's neighbourhood; keep the most specific.
    if "k1-2" in ratios and "k1" in ratios and not re.search(r"\bk1\b(?!-)", text, re.I):
        ratios.remove("k1")
    currencies = _match_all(CURRENCY_WORDS, text, ev, "currency")
    on_date, year = _date(text, today, ev)
    follow_up = bool(_FOLLOW_UP.match(text))
    if follow_up:
        ev.append("follow-up: question starts like a continuation")

    low = text.lower()
    if currencies or "курс" in low:
        intent = "fx"
    # "нормативы" alone is also a word of law ("отчётность о нормативах"), so
    # compliance needs a ratio name or a company to check.
    elif ratios or (companies and ("норматив" in low or "соблюда" in low)):
        intent = "compliance"
    elif "overdue_loans" in metrics:
        intent = "risk"          # problem loans are a share, judged against a threshold
    elif len(companies) >= 2 or "сравни" in low or "compare" in low:
        intent = "compare"
    elif companies or (metrics and follow_up):
        intent = "metric"
    else:
        intent = "legal"
    ev.append(f"intent: {intent}")

    return Perception(text=text, intent=intent, companies=companies, metrics=metrics,
                      ratios=ratios, currencies=currencies, on_date=on_date, year=year,
                      follow_up=follow_up, evidence=ev)
