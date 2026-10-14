import json
import subprocess
from dataclasses import dataclass
from datetime import date

SOURCE = "country_indicators"
API = "https://api.worldbank.org/v2/country/{countries}/indicator/{code}?format=json&per_page=20000&date=1990:2030"
TIMEOUT_S = 60

INDICATORS: dict[str, tuple[str, str, str]] = {
    "FB.AST.NPER.ZS": ("wb_npl_ratio", "%", "Bank non-performing loans to total gross loans"),
    "FB.BNK.CAPA.ZS": ("wb_bank_capital_to_assets", "%", "Bank capital to assets"),
    "GFDD.SI.05": ("wb_regulatory_capital_to_rwa", "%", "Bank regulatory capital to risk-weighted assets"),
    "GFDD.SI.01": ("wb_bank_z_score", "index", "Bank Z-score: distance to insolvency"),
    "GFDD.SI.04": ("wb_credit_to_deposits", "%", "Bank credit to bank deposits"),
    "GFDD.EI.01": ("wb_net_interest_margin", "%", "Bank net interest margin"),
    "GFDD.EI.05": ("wb_bank_roa", "%", "Bank return on assets, after tax"),
    "GFDD.EI.06": ("wb_bank_roe", "%", "Bank return on equity, after tax"),
    "FD.RES.LIQU.AS.ZS": ("wb_liquid_reserves_to_assets", "%", "Bank liquid reserves to bank assets"),
    "FS.AST.PRVT.GD.ZS": ("wb_private_credit_to_gdp", "%", "Domestic credit to private sector, % of GDP"),
    "FR.INR.LEND": ("wb_lending_rate", "%", "Lending interest rate"),
    "FR.INR.DPST": ("wb_deposit_rate", "%", "Deposit interest rate"),
    "FR.INR.RINR": ("wb_real_interest_rate", "%", "Real interest rate"),
    "FP.CPI.TOTL.ZG": ("wb_inflation", "%", "Inflation, consumer prices, annual"),
    "NY.GDP.MKTP.KD.ZG": ("wb_gdp_growth", "%", "GDP growth, annual"),
    "NY.GDP.PCAP.CD": ("wb_gdp_per_capita_usd", "USD", "GDP per capita, current USD"),
    "PA.NUS.FCRF": ("wb_usd_rate_avg", "KZT per USD", "Official exchange rate, period average"),
    "SL.UEM.TOTL.ZS": ("wb_unemployment", "%", "Unemployment, % of labour force"),
    "BN.CAB.XOKA.GD.ZS": ("wb_current_account_to_gdp", "%", "Current account balance, % of GDP"),
    "GC.DOD.TOTL.GD.ZS": ("wb_government_debt_to_gdp", "%", "Central government debt, % of GDP"),
}

COUNTRIES: dict[str, str] = {
    "KAZ": "Kazakhstan",
    "UZB": "Uzbekistan",
    "KGZ": "Kyrgyz Republic",
    "RUS": "Russian Federation",
    "AZE": "Azerbaijan",
    "GEO": "Georgia",
    "ARM": "Armenia",
    "TUR": "Turkiye",
    "POL": "Poland",
    "MNG": "Mongolia",
    "WLD": "World",
}


class WorldBankError(Exception):
    pass


@dataclass(frozen=True)
class Value:
    country: str
    metric: str
    year: int
    value: float
    unit: str


def fetch(code: str) -> list[Value]:
    metric, unit, _ = INDICATORS[code]
    url = API.format(countries=";".join(COUNTRIES), code=code)
    r = subprocess.run(
        ["curl", "-s", "-m", str(TIMEOUT_S), url], capture_output=True, text=True, timeout=TIMEOUT_S + 10
    )
    try:
        body = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise WorldBankError(f"{code}: not JSON ({r.stdout[:80]!r})") from e
    if len(body) < 2 or body[1] is None:
        return []
    return [
        Value(x["countryiso3code"], metric, int(x["date"]), float(x["value"]), unit)
        for x in body[1]
        if x["value"] is not None and x["countryiso3code"] in COUNTRIES
    ]


def period(year: int) -> date:
    return date(year, 12, 31)
