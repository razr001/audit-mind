from app.ai.agent.services.agent_tool_execution_service import AgentToolExecutionService
from app.ai.agent.services.assistant_action_service import AssistantActionService
from app.ai.agent.services.system_agent_state_service import SystemAgentStateService
from app.models.assistant import (
    AssistantAction,
    AssistantActionStatus,
    AssistantAgentRun,
    AssistantAgentRunStatus,
    AssistantToolCallStatus,
)
from app.schemas.assistant import AssistantActionDecisionType

_TERMINAL_ACTION_STATUSES = {
    AssistantActionStatus.SUCCEEDED,
    AssistantActionStatus.PARTIAL,
    AssistantActionStatus.FAILED,
}


async def recover_interrupted_resume(
    *,
    decision: AssistantActionDecisionType,
    agent_run: AssistantAgentRun,
    action: AssistantAction,
    tool_execution_service: AgentToolExecutionService,
    action_service: AssistantActionService,
    state_service: SystemAgentStateService,
) -> None:
    if decision != AssistantActionDecisionType.APPROVE:
        await state_service.finish_run(
            agent_run,
            AssistantAgentRunStatus.FAILED,
            "AGENT_RESUME_INTERRUPTED",
        )
        return
    tool_call = await tool_execution_service.find_call(
        run_id=agent_run.id,
        tool_call_id=action.tool_call_id,
    )
    if tool_call is not None and tool_call.status in {
        AssistantToolCallStatus.RUNNING,
        AssistantToolCallStatus.RECONCILIATION_REQUIRED,
    }:
        if tool_call.status == AssistantToolCallStatus.RUNNING:
            await tool_execution_service.mark_reconciliation_required(tool_call)
        await action_service.mark_reconciliation_required(
            action_id=action.id,
            user_id=action.user_id,
        )
        await state_service.finish_run(
            agent_run,
            AssistantAgentRunStatus.FAILED,
            "SIDE_EFFECT_RECONCILIATION_REQUIRED",
        )
        return
    if tool_call is not None and tool_call.status == AssistantToolCallStatus.SUCCEEDED:
        current_action = await action_service.get_owned(
            action_id=action.id,
            user_id=action.user_id,
        )
        if current_action.status in _TERMINAL_ACTION_STATUSES:
            return
        # 工具已经成功，但 Action/消息尚未原子收尾。不能重放工具，也不能
        # 宣称执行前失败，转人工对账后即可安全删除 LangGraph checkpoint。
        await action_service.mark_reconciliation_required(
            action_id=action.id,
            user_id=action.user_id,
        )
        await state_service.finish_run(
            agent_run,
            AssistantAgentRunStatus.FAILED,
            "SIDE_EFFECT_RECONCILIATION_REQUIRED",
        )
        return
    if tool_call is None:
        await action_service.mark_interrupted(
            action_id=action.id,
            user_id=action.user_id,
        )
    else:
        await action_service.mark_failed(
            action_id=action.id,
            user_id=action.user_id,
        )
    await state_service.finish_run(
        agent_run,
        AssistantAgentRunStatus.FAILED,
        "AGENT_RESUME_INTERRUPTED",
    )


async def recover_failed_resume(
    *,
    decision: AssistantActionDecisionType,
    agent_run: AssistantAgentRun,
    action: AssistantAction,
    tool_execution_service: AgentToolExecutionService,
    action_service: AssistantActionService,
    state_service: SystemAgentStateService,
) -> None:
    if decision != AssistantActionDecisionType.APPROVE:
        await state_service.finish_run(
            agent_run,
            AssistantAgentRunStatus.FAILED,
            "AGENT_RESUME_FAILED",
        )
        return
    tool_call = await tool_execution_service.find_call(
        run_id=agent_run.id,
        tool_call_id=action.tool_call_id,
    )
    if tool_call is not None and tool_call.status in {
        AssistantToolCallStatus.RUNNING,
        AssistantToolCallStatus.RECONCILIATION_REQUIRED,
    }:
        if tool_call.status == AssistantToolCallStatus.RUNNING:
            await tool_execution_service.mark_reconciliation_required(tool_call)
        await action_service.mark_reconciliation_required(
            action_id=action.id,
            user_id=action.user_id,
        )
        await state_service.finish_run(
            agent_run,
            AssistantAgentRunStatus.FAILED,
            "SIDE_EFFECT_RECONCILIATION_REQUIRED",
        )
        return
    if tool_call is not None and tool_call.status == AssistantToolCallStatus.SUCCEEDED:
        current_action = await action_service.get_owned(
            action_id=action.id,
            user_id=action.user_id,
        )
        if current_action.status in _TERMINAL_ACTION_STATUSES:
            return
        await action_service.mark_reconciliation_required(
            action_id=action.id,
            user_id=action.user_id,
        )
        await state_service.finish_run(
            agent_run,
            AssistantAgentRunStatus.FAILED,
            "SIDE_EFFECT_RECONCILIATION_REQUIRED",
        )
        return
    await action_service.mark_failed(
        action_id=action.id,
        user_id=action.user_id,
    )
    await state_service.finish_run(
        agent_run,
        AssistantAgentRunStatus.FAILED,
        "AGENT_RESUME_FAILED",
    )
