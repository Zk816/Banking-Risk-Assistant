"""One entry point for any question, numbers or text.

There is no classifier here. Both paths are offered to the model as tools, and
the routing decision is which tool it calls — the simplest agentic pattern that
handles "both" without special-casing it.

The trade-off, stated plainly: routing accuracy now depends on a 7B model reading
two tool descriptions correctly, and this one has already been caught refusing
without checking and rewriting numbers it was handed. Which is why nothing it
says is trusted afterwards — `answer_with_tools` still validates every figure
against what the tools returned.
"""

from typing import Any

from kzbank.agent.prompts import ROUTER_SYSTEM
from kzbank.agent.toolloop import ToolAnswer, answer_with_tools
from kzbank.tools import bank_metrics, company_metrics, legal_text

# Order matters only for readability; the model sees them all at once.
ALL_TOOLS: list[dict[str, Any]] = [
    *bank_metrics.TOOL_SCHEMAS,
    *company_metrics.SCHEMAS,
    legal_text.SCHEMA,
]

ALL_REGISTRY = {
    **bank_metrics.TOOL_REGISTRY,
    **company_metrics.REGISTRY,
    "search_legal_text": legal_text.search_legal_text,
}


def answer(question: str) -> ToolAnswer:
    """Answer a question using whichever tools it needs.

    Raises:
        ValueError: if the question is empty.
        RuntimeError: if the model server is unreachable.
    """
    return answer_with_tools(
        question,
        ROUTER_SYSTEM,
        tools=ALL_TOOLS,
        registry=ALL_REGISTRY,
    )
