from typing import Any

from fin_assistant.storage.db import fetch_all

from .formatting import _num

COUNTRY = {
    "kazakhstan": "KAZ",
    "казахстан": "KAZ",
    "uzbekistan": "UZB",
    "узбекистан": "UZB",
    "kyrgyzstan": "KGZ",
    "кыргызстан": "KGZ",
    "киргизия": "KGZ",
    "russia": "RUS",
    "россия": "RUS",
    "azerbaijan": "AZE",
    "азербайджан": "AZE",
    "georgia": "GEO",
    "грузия": "GEO",
    "armenia": "ARM",
    "армения": "ARM",
    "turkey": "TUR",
    "турция": "TUR",
    "poland": "POL",
    "польша": "POL",
    "mongolia": "MNG",
    "монголия": "MNG",
    "world": "WLD",
    "мир": "WLD",
}

INDICATORS = {
    "npl_ratio": "wb_npl_ratio",
    "bank_capital_to_assets": "wb_bank_capital_to_assets",
    "regulatory_capital_to_rwa": "wb_regulatory_capital_to_rwa",
    "bank_roe": "wb_bank_roe",
    "bank_roa": "wb_bank_roa",
    "net_interest_margin": "wb_net_interest_margin",
    "inflation": "wb_inflation",
    "gdp_growth": "wb_gdp_growth",
    "gdp_per_capita_usd": "wb_gdp_per_capita_usd",
    "usd_rate_avg": "wb_usd_rate_avg",
    "unemployment": "wb_unemployment",
    "private_credit_to_gdp": "wb_private_credit_to_gdp",
    "government_debt_to_gdp": "wb_government_debt_to_gdp",
}


def country_indicator(
    indicator: str, countries: list[str] | None = None, year: int | None = None
) -> dict[str, Any]:
    if indicator not in INDICATORS:
        return {"found": False, "reason": f"Неизвестный индикатор; доступны: {', '.join(INDICATORS)}"}
    codes = [COUNTRY.get(c.lower().strip(), c.upper()[:3]) for c in (countries or ["kazakhstan"])]
    rows = fetch_all(
        """SELECT m.bin, e.name_en, m.period, m.value, m.unit FROM metrics m JOIN entities e ON e.bin = m.bin
                        WHERE m.metric = %s AND m.bin = ANY(%s) ORDER BY m.period""",
        (INDICATORS[indicator], [f"WB:{c}" for c in codes]),
    )
    out: dict[str, dict] = {}
    for r in rows:
        if year and r["period"].year != year:
            continue
        out[r["name_en"]] = {
            "year": r["period"].year,
            "value": f"{_num(float(r['value']))}" + (" %" if r["unit"] == "%" else f" {r['unit']}"),
        }
    if not out:
        return {"found": False, "reason": "нет данных за этот год" if year else "нет данных"}
    return {
        "found": True,
        "indicator": indicator,
        "values": out,
        "source": "country_indicators (Всемирный банк, годовые данные)",
    }
