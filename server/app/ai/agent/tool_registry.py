from collections.abc import Iterable
from typing import Any, cast

from langchain_core.tools import BaseTool
from pydantic import BaseModel

from app.ai.agent.schemas import AgentIntent

_TOOLS_BY_INTENT: dict[AgentIntent, frozenset[str]] = {
    AgentIntent.REGULATION_QA: frozenset({"answer_regulation_question", "search_regulations"}),
    AgentIntent.DRAFT_LEGAL_DOCUMENT: frozenset(
        {"answer_regulation_question", "draft_contract", "search_regulations"}
    ),
    AgentIntent.REVIEW_LEGAL_DOCUMENT: frozenset(
        {"answer_regulation_question", "review_contract", "search_regulations"}
    ),
    AgentIntent.SYSTEM_READ: frozenset(
        {
            "list_regulations",
            "list_my_regulations",
            "search_regulations",
            "get_regulation_detail",
            "get_regulation_source_download",
            "get_regulation_page_blocks",
            "get_regulation_asset_download",
            "get_regulation_rules",
            "count_regulation_rules",
            "list_documents",
            "get_document",
            "get_document_download",
            "list_audit_tasks",
            "get_audit_task",
            "get_audit_page_result",
        }
    ),
    AgentIntent.SYSTEM_WRITE: frozenset(
        {
            "list_regulations",
            "list_my_regulations",
            "search_regulations",
            "get_regulation_detail",
            "get_regulation_source_download",
            "get_regulation_page_blocks",
            "get_regulation_asset_download",
            "get_regulation_rules",
            "count_regulation_rules",
            "list_documents",
            "get_document",
            "get_document_download",
            "list_audit_tasks",
            "get_audit_task",
            "get_audit_page_result",
            "create_text_regulation",
            "process_regulation",
            "create_markdown_audit",
            "create_document_audit",
            "retry_audit_task",
            "start_document_parse",
            "sync_document_parse",
        }
    ),
    AgentIntent.SYSTEM_DELETE: frozenset(),
    AgentIntent.UNSUPPORTED: frozenset(),
}


def select_tools(intent: AgentIntent, tools: Iterable[BaseTool]) -> list[BaseTool]:
    allowed = _TOOLS_BY_INTENT[intent]
    return [tool for tool in tools if tool.name in allowed]


def normalize_tool_arguments(
    tools: Iterable[BaseTool],
    *,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Materialize the exact validated tool input that a user approves."""
    tool = next((item for item in tools if item.name == tool_name), None)
    if tool is None:
        raise RuntimeError(f"unknown system agent tool: {tool_name}")
    # tool_call_schema 会排除 runtime 等服务端注入字段，正是审批参数需要的模型。
    # LangChain 的公开类型还包含 dict，因此在这个已知的工具边界做显式收窄。
    schema = cast(type[BaseModel], tool.tool_call_schema)
    return schema.model_validate(arguments).model_dump(mode="json")
