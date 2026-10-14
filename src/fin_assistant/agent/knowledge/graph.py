import networkx as nx

from fin_assistant.storage.db import fetch_all

from .facts import KZ_NORMS, bin_of


def build_graph(companies: list[str]) -> nx.DiGraph:
    g = nx.DiGraph()
    for norm, spec in KZ_NORMS.items():
        g.add_node(norm, kind="kz_norm", label=f"{norm}\n≥{spec['kz_min']:.1%}")
        g.add_node(spec["us_concept"], kind="us_concept")
        g.add_edge(norm, spec["us_concept"], rel="equivalent_to")
        g.add_edge(norm, "R2 capital adequacy", rel="checked_by")
    for rule in ("R2 capital adequacy", "R3 common period", "R5 NPL risk", "R7 trend"):
        g.add_node(rule, kind="rule")
    metric_concept = {"tier1_ratio": "Tier 1 ratio", "total_capital_ratio": "Total capital ratio"}
    for c in companies:
        b = bin_of(c)
        if not b:
            continue
        g.add_node(c, kind="bank")
        for r in fetch_all("SELECT DISTINCT metric FROM metrics WHERE bin = %s", (b,)):
            m = r["metric"]
            if m in metric_concept:
                g.add_edge(c, metric_concept[m], rel="reports")
            elif m in ("nonaccrual_loans", "overdue_loans"):
                g.add_node(m, kind="metric")
                g.add_edge(c, m, rel="reports")
                g.add_edge(m, "R5 NPL risk", rel="input_to")
                g.add_edge(m, "R7 trend", rel="input_to")
            elif m in ("assets", "deposits"):
                g.add_node(m, kind="metric")
                g.add_edge(c, m, rel="reports")
                g.add_edge(m, "R3 common period", rel="compared_by")
    return g
