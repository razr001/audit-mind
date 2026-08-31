from collections.abc import Sequence
from typing import Any, cast

from langchain.agents import create_agent
from langchain.agents.middleware import (
    HumanInTheLoopMiddleware,
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_core.tools import BaseTool

from app.ai.agent.checkpointer import agent_checkpointer
from app.ai.agent.context import AgentRuntimeContext
from app.ai.agent.prompts import SYSTEM_AGENT_PROMPT
from app.ai.agent.schemas import SystemAgentFinalOutput
from app.ai.model import get_chat_model
from app.core.config import get_settings

settings = get_settings()

# 只有这里列出的工具会触发 Human-in-the-loop 中断；只读工具可以直接执行。
WRITE_TOOL_NAMES = (
    "create_text_regulation",
    "process_regulation",
    "create_markdown_audit",
    "create_document_audit",
    "retry_audit_task",
    "start_document_parse",
    "sync_document_parse",
)


def create_system_agent(available_tools: Sequence[BaseTool]) -> Any:
    """装配一个有调用上限、人工审批和持久化 checkpoint 的 LangGraph Agent。"""

    # LangChain 的多个 Middleware 状态泛型当前无法在组合后正确统一推断，
    # 将第三方工厂边界收窄为 Any；本项目自己的运行上下文、工具和返回值仍继续检查。
    agent_factory = cast(Any, create_agent)
    return agent_factory(
        model=get_chat_model(),
        tools=available_tools,
        system_prompt=SYSTEM_AGENT_PROMPT,
        context_schema=AgentRuntimeContext,
        middleware=[
            # 防止模型在异常情况下无限自我调用或循环调用工具。
            ModelCallLimitMiddleware(
                run_limit=settings.ASSISTANT_AGENT_MAX_MODEL_CALLS,
                exit_behavior="error",
            ),
            ToolCallLimitMiddleware(
                run_limit=settings.ASSISTANT_AGENT_MAX_TOOL_CALLS,
                exit_behavior="error",
            ),
            HumanInTheLoopMiddleware(
                # 写工具真正执行前只允许用户明确批准或拒绝。
                interrupt_on={
                    name: {"allowed_decisions": ["approve", "reject"]} for name in WRITE_TOOL_NAMES
                }
            ),
        ],
        checkpointer=agent_checkpointer.get(),
        name="auditmind-system-agent",
    )


def interrupted_tool_call_id(
    agent_result: dict[str, Any],
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    """从模型消息中取出被暂停写工具的原始 tool_call_id。"""

    for message in reversed(agent_result.get("messages", [])):
        for tool_call in getattr(message, "tool_calls", []):
            if tool_call.get("name") == tool_name and tool_call.get("args") == arguments:
                return str(tool_call["id"])
    raise RuntimeError("approval interruption is missing its tool call ID")


def read_final_output(agent_result: dict[str, Any]) -> SystemAgentFinalOutput:
    """优先读取结构化结果，并兼容模型只返回普通文本的情况。"""

    structured = agent_result.get("structured_response")
    if isinstance(structured, SystemAgentFinalOutput):
        return structured
    if isinstance(structured, dict):
        return SystemAgentFinalOutput.model_validate(structured)
    for message in reversed(agent_result.get("messages", [])):
        content = getattr(message, "content", None)
        if isinstance(content, str) and content.strip():
            return SystemAgentFinalOutput(answer=content.strip())
    raise RuntimeError("system agent did not return a final response")
