from uuid import UUID

from sqlalchemy import select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assistant import (
    AssistantAction,
    AssistantActionStatus,
    AssistantAgentRun,
    AssistantAgentRunStatus,
    AssistantMessage,
    AssistantMessageStatus,
    AssistantToolCall,
    AssistantToolCallStatus,
)


class AssistantReconciliationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def recover_stale_executions(
        self,
        *,
        user_id: UUID,
        stale_before,
        now,
        conversation_id: UUID | None = None,
        action_id: UUID | None = None,
    ) -> int:
        conditions = [
            AssistantAction.user_id == user_id,
            AssistantAction.status == AssistantActionStatus.EXECUTING,
            AssistantAction.updated_at < stale_before,
        ]
        if conversation_id is not None:
            conditions.append(AssistantAction.conversation_id == conversation_id)
        if action_id is not None:
            conditions.append(AssistantAction.id == action_id)
        result = await self.session.execute(
            select(AssistantAction).where(*conditions).with_for_update(skip_locked=True)
        )
        stale_actions = list(result.scalars().all())
        if not stale_actions:
            return 0
        action_ids = [action.id for action in stale_actions]
        stale_pairs = [(action.run_id, action.tool_call_id) for action in stale_actions]
        stale_run_ids = [action.run_id for action in stale_actions]
        await self.session.execute(
            update(AssistantAction)
            .where(AssistantAction.id.in_(action_ids))
            .values(
                status=AssistantActionStatus.RECONCILIATION_REQUIRED,
                result_code="STALE_SIDE_EFFECT_UNCERTAIN",
                version=AssistantAction.version + 1,
                updated_at=now,
            )
        )
        await self.session.execute(
            update(AssistantToolCall)
            .where(
                tuple_(AssistantToolCall.run_id, AssistantToolCall.tool_call_id).in_(stale_pairs),
                AssistantToolCall.status == AssistantToolCallStatus.RUNNING,
            )
            .values(
                status=AssistantToolCallStatus.RECONCILIATION_REQUIRED,
                result_code="STALE_SIDE_EFFECT_UNCERTAIN",
                completed_at=now,
                updated_at=now,
            )
        )
        stale_message_ids = select(AssistantAgentRun.assistant_message_id).where(
            AssistantAgentRun.id.in_(stale_run_ids)
        )
        await self.session.execute(
            update(AssistantMessage)
            .where(
                AssistantMessage.id.in_(stale_message_ids),
                AssistantMessage.status.in_([
                    AssistantMessageStatus.GENERATING,
                    AssistantMessageStatus.WAITING_APPROVAL,
                ]),
            )
            .values(status=AssistantMessageStatus.FAILED, updated_at=now)
        )
        await self.session.execute(
            update(AssistantAgentRun)
            .where(
                AssistantAgentRun.id.in_(stale_run_ids),
                AssistantAgentRun.status.in_([
                    AssistantAgentRunStatus.RUNNING,
                    AssistantAgentRunStatus.WAITING_APPROVAL,
                ]),
            )
            .values(
                status=AssistantAgentRunStatus.FAILED,
                error_code="STALE_SIDE_EFFECT_RECONCILIATION_REQUIRED",
                completed_at=now,
                updated_at=now,
            )
        )
        return len(stale_actions)
