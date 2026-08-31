from typing import Any
from uuid import UUID

from app.ai.agent.repositories.assistant_action_repository import AssistantActionRepository
from app.ai.agent.repositories.assistant_agent_run_repository import AssistantAgentRunRepository
from app.ai.agent.schemas import SystemAgentFinalOutput
from app.infrastructure.db.unit_of_work import UnitOfWork
from app.models.assistant import (
    AssistantAction,
    AssistantActionStatus,
    AssistantAgentRun,
    AssistantAgentRunStatus,
    AssistantMessage,
    AssistantMessageStatus,
)
from app.repositories.assistant_repository import AssistantRepository
from app.unit.date import utc_now


class SystemAgentStateService:
    """集中维护 Run、Action 和聊天消息之间的状态一致性。

    需要同时变化的记录必须放在同一个 UnitOfWork 中提交，避免出现“前端显示
    等待审批，但数据库里没有待审批 Action”这样的半完成状态。
    """

    def __init__(
        self, *, unit_of_work: UnitOfWork, action_repository: AssistantActionRepository
    ) -> None:
        self.unit_of_work = unit_of_work
        self.action_repository = action_repository
        self.run_repository = AssistantAgentRunRepository(unit_of_work.session)
        self.assistant_repository = AssistantRepository(unit_of_work.session)

    async def save_run(self, agent_run: AssistantAgentRun) -> None:
        async with self.unit_of_work:
            await self.run_repository.save(agent_run)

    async def get_run(self, *, run_id: UUID, user_id: UUID) -> AssistantAgentRun:
        agent_run = await self.run_repository.find_owned(run_id=run_id, user_id=user_id)
        if agent_run is None:
            raise RuntimeError("assistant agent run not found")
        return agent_run

    async def pause_for_approval(
        self, agent_run: AssistantAgentRun, pending_action: AssistantAction
    ) -> None:
        """原子保存 Action，并把对应 AI 消息和 Run 一起置为等待审批。"""

        async with self.unit_of_work:
            await self.action_repository.save(pending_action)
            # assistant_message_id 决定聊天界面中的哪一条 AI 消息显示“等待确认”。
            paused = await self.assistant_repository.pause_generating_message(
                agent_run.assistant_message_id
            )
            if not paused:
                raise RuntimeError("assistant message cannot enter approval state")
            await self.run_repository.set_status(
                run_id=agent_run.id,
                user_id=agent_run.user_id,
                status=AssistantAgentRunStatus.WAITING_APPROVAL,
            )

    async def set_running(self, agent_run: AssistantAgentRun) -> None:
        async with self.unit_of_work:
            await self.run_repository.set_status(
                run_id=agent_run.id,
                user_id=agent_run.user_id,
                status=AssistantAgentRunStatus.RUNNING,
                error_code=None,
                completed_at=None,
            )

    async def finish_run(
        self,
        agent_run: AssistantAgentRun,
        status: AssistantAgentRunStatus,
        error_code: str | None = None,
    ) -> None:
        async with self.unit_of_work:
            await self.run_repository.set_status(
                run_id=agent_run.id,
                user_id=agent_run.user_id,
                status=status,
                error_code=error_code,
                completed_at=utc_now(),
            )

    async def interrupt_run(self, agent_run: AssistantAgentRun) -> None:
        async with self.unit_of_work:
            await self.run_repository.set_status(
                run_id=agent_run.id,
                user_id=agent_run.user_id,
                status=AssistantAgentRunStatus.WAITING_APPROVAL,
                error_code="AGENT_RESUME_INTERRUPTED",
                completed_at=None,
            )

    async def record_usage(
        self, agent_run: AssistantAgentRun, agent_result: dict[str, Any]
    ) -> None:
        """从 LangGraph 消息中汇总模型和工具调用次数，用于限额审计。"""

        model_calls = 0
        tool_calls = 0
        for message in agent_result.get("messages", []):
            calls = getattr(message, "tool_calls", None) or []
            tool_calls += len(calls)
            if getattr(message, "usage_metadata", None) is not None or calls:
                model_calls += 1
        async with self.unit_of_work:
            await self.run_repository.set_usage(
                run_id=agent_run.id,
                user_id=agent_run.user_id,
                model_calls=model_calls,
                tool_calls=tool_calls,
            )

    async def commit_resume_result(
        self,
        *,
        agent_run: AssistantAgentRun,
        action: AssistantAction,
        final_output: SystemAgentFinalOutput,
        safe_sources: list[dict[str, Any]],
        tool_receipt: dict[str, Any] | None,
    ) -> None:
        """原子提交恢复后的消息、Action 结果和 Run 最终状态。"""

        async with self.unit_of_work:
            completed = await self.assistant_repository.complete_generating_message(
                agent_run.assistant_message_id,
                content=final_output.answer,
                sources=safe_sources,
                answered=final_output.answered,
            )
            if not completed:
                message = await self.unit_of_work.session.get(
                    AssistantMessage, agent_run.assistant_message_id
                )
                if message is None or message.status != AssistantMessageStatus.COMPLETED:
                    raise RuntimeError("assistant message cannot accept the resumed result")
            if tool_receipt is not None:
                # tool_receipt 是写工具持久化后的事实凭证，不采信模型自行描述的结果。
                await self._commit_action_result(action=action, tool_receipt=tool_receipt)
            await self.run_repository.set_status(
                run_id=agent_run.id,
                user_id=agent_run.user_id,
                status=AssistantAgentRunStatus.COMPLETED,
                completed_at=utc_now(),
            )

    async def _commit_action_result(
        self, *, action: AssistantAction, tool_receipt: dict[str, Any]
    ) -> None:
        partial = tool_receipt.get("status") == "PARTIAL"
        updated = await self.action_repository.set_result(
            action_id=action.id,
            user_id=action.user_id,
            from_status=AssistantActionStatus.EXECUTING,
            status=(AssistantActionStatus.PARTIAL if partial else AssistantActionStatus.SUCCEEDED),
            result_code=(
                tool_receipt.get("resultCode", "PARTIAL_SUCCESS") if partial else "SUCCEEDED"
            ),
            resource_type=tool_receipt.get("resourceType"),
            resource_id=UUID(tool_receipt["resourceId"]),
            executed_at=utc_now(),
        )
        if updated is None:
            raise RuntimeError("assistant action cannot accept the resumed result")
