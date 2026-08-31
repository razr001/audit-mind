from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.tools import BaseTool
from langgraph.types import Interrupt

from app.ai.agent.runner import interrupted_tool_call_id
from app.ai.agent.services.assistant_action_service import AssistantActionService
from app.ai.agent.services.system_agent_output_service import action_summary
from app.ai.agent.services.system_agent_state_service import SystemAgentStateService
from app.ai.agent.tool_registry import normalize_tool_arguments
from app.models.assistant import AssistantActionRisk, AssistantAgentRun


async def approval_events(
    *,
    agent_run: AssistantAgentRun,
    agent_result: dict[str, Any],
    interruption: Interrupt,
    available_tools: Sequence[BaseTool],
    action_service: AssistantActionService,
    state_service: SystemAgentStateService,
) -> AsyncIterator[dict[str, Any]]:
    """把 LangGraph 中断转换成持久化 Action 和前端确认事件。"""

    action_requests = interruption.value.get("action_requests", [])
    # 首期策略是一批只审批一个写操作，避免一次确认授权多个副作用。
    if len(action_requests) != 1:
        raise RuntimeError("system agent must request one write action at a time")
    approval_request = action_requests[0]
    # 先通过工具 schema 补齐默认值，再计算摘要。前端展示、用户批准和最终执行
    # 都使用这一份规范化参数，不能再让模型临时修改。
    normalized_arguments = normalize_tool_arguments(
        available_tools,
        tool_name=approval_request["name"],
        arguments=approval_request["args"],
    )
    action = action_service.build_pending(
        run_id=agent_run.id,
        conversation_id=agent_run.conversation_id,
        user_id=agent_run.user_id,
        tool_call_id=interrupted_tool_call_id(
            agent_result,
            approval_request["name"],
            approval_request["args"],
        ),
        tool_name=approval_request["name"],
        arguments=normalized_arguments,
        display_summary=action_summary(approval_request["name"], normalized_arguments),
        risk_level=AssistantActionRisk.WRITE,
    )
    # Action、Run 和 assistant_message 在同一事务中进入等待审批状态。
    await state_service.pause_for_approval(agent_run, action)
    yield {
        "type": "confirmation-required",
        "data": {
            "actionId": str(action.id),
            "version": action.version,
            "toolName": action.tool_name,
            "summary": action.display_summary,
            "arguments": action.arguments,
            "argumentsHash": action.arguments_hash,
            "expiresAt": action.expires_at.isoformat(),
        },
    }
    yield {"type": "done", "data": {"status": "waiting_approval"}}
