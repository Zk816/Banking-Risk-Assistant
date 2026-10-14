def _num(x: float, digits: int = 2) -> str:
    s = f"{x:,.{digits}f}".replace(",", " ").replace(".", ",")
    return s


def fmt_kzt(value: float) -> str:
    a = abs(value)
    if a >= 1e12:
        return f"{_num(value / 1e12)} трлн тенге"
    if a >= 1e9:
        return f"{_num(value / 1e9, 2 if a < 1e11 else 1)} млрд тенге"
    if a >= 1e6:
        return f"{_num(value / 1e6, 1)} млн тенге"
    return f"{_num(value, 0)} тенге"


def fmt_pct(ratio: float) -> str:
    return f"{_num(ratio * 100)}%"


METRICS: dict[str, tuple[str, str, str]] = {
    "npl90_share": (
        "nbk_npl90/nbk_loans",
        "pct",
        "Доля кредитов с просрочкой свыше 90 дней в ссудном портфеле (NPL 90+)",
    ),
    "npl90": ("nbk_npl90", "kzt", "Кредиты с просрочкой свыше 90 дней"),
    "overdue30": ("nbk_overdue30", "kzt", "Кредиты с просрочкой свыше 30 дней"),
    "stage3": ("nbk_stage3", "kzt", "Кредиты 3 стадии (обесцененные, МСФО 9)"),
    "loans": ("nbk_loans", "kzt", "Ссудный портфель"),
    "assets": ("nbk_assets", "kzt", "Активы"),
    "liabilities": ("nbk_liabilities", "kzt", "Обязательства"),
    "equity": ("nbk_equity", "kzt", "Собственный капитал (баланс)"),
    "deposits": ("nbk_deposits", "kzt", "Вклады клиентов, всего"),
    "deposits_individuals": ("nbk_deposits_individuals", "kzt", "Вклады физических лиц"),
    "deposits_legal": ("nbk_deposits_legal", "kzt", "Вклады юридических лиц"),
    "provisions": ("nbk_provisions", "kzt", "Провизии (резервы) по кредитам"),
    "provision_coverage": ("nbk_provisions/nbk_npl90", "pct", "Покрытие NPL 90+ провизиями"),
    "net_income_ytd": ("nbk_net_income_ytd", "kzt", "Чистая прибыль с начала года"),
    "regulatory_capital": ("reg_capital", "kzt", "Собственный капитал (регуляторный)"),
    "k1": ("k1", "ratio_pct", "Достаточность основного капитала k1 (минимум 5,5%)"),
    "k1_2": ("k1_2", "ratio_pct", "Достаточность капитала 1 уровня k1-2 (минимум 6,5%)"),
    "k2": ("k2", "ratio_pct", "Достаточность собственного капитала k2 (минимум 8%)"),
    "k4": ("k4", "ratio", "Коэффициент текущей ликвидности k4 (минимум 0,3)"),
    "interest_margin": ("nbk_interest_margin", "ratio_pct", "Чистая процентная маржа"),
    "interest_spread": ("nbk_interest_spread", "ratio_pct", "Процентный спрэд"),
    "net_interest_income_ytd": (
        "nbk_net_interest_income_ytd",
        "kzt",
        "Чистый процентный доход с начала года",
    ),
    "equity_to_assets": ("nbk_equity/nbk_assets", "pct", "Собственный капитал / активы"),
    "loans_to_deposits": ("nbk_loans/nbk_deposits", "ratio", "Кредиты / депозиты"),
}

MINIMUMS = {"k1": 0.055, "k1_2": 0.065, "k2": 0.08, "k4": 0.3}

SOURCE = "bank_statistics (Национальный Банк РК, ежемесячная статистика по банкам)"


def _format(metric: str, value: float) -> str:
    unit = METRICS[metric][1]
    if unit == "kzt":
        return fmt_kzt(value)
    if unit in ("pct", "ratio_pct"):
        return fmt_pct(value)
    return _num(value)
