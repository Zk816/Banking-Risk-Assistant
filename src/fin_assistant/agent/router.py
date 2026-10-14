from typing import Any

from fin_assistant.agent.prompts import ROUTER_SYSTEM
from fin_assistant.agent.toolloop import ToolAnswer, answer_with_tools
from fin_assistant.tools import bank_metrics, company_metrics, legal_text

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
    return answer_with_tools(
        question,
        ROUTER_SYSTEM,
        tools=ALL_TOOLS,
        registry=ALL_REGISTRY,
    )
