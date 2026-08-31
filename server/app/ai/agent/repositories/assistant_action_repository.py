from typing import Any, cast
from uuid import UUID

from sqlalchemy import or_, select, tuple_, update
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


class AssistantActionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save(self, action: AssistantAction) -> AssistantAction:
        self.session.add(action)
        await self.session.flush()
        return action

    async def find_owned(
        self, *, action_id: UUID, user_id: UUID, for_update: bool = False
    ) -> AssistantAction | None:
        statement = select(AssistantAction).where(
            AssistantAction.id == action_id,
            AssistantAction.user_id == user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def find_by_run_and_tool_call(
        self,
        *,
        run_id: UUID,
        tool_call_id: str,
        user_id: UUID | None = None,
        conversation_id: UUID | None = None,
        status: AssistantActionStatus | None = None,
        for_update: bool = False,
    ) -> AssistantAction | None:
        conditions = [
            AssistantAction.run_id == run_id,
            AssistantAction.tool_call_id == tool_call_id,
        ]
        if user_id is not None:
            conditions.append(AssistantAction.user_id == user_id)
        if conversation_id is not None:
            conditions.append(AssistantAction.conversation_id == conversation_id)
        if status is not None:
            conditions.append(AssistantAction.status == status)
        statement = select(AssistantAction).where(*conditions)
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def find_active_for_conversation(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
    ) -> list[AssistantAction]:
        result = await self.session.execute(
            select(AssistantAction)
            .join(AssistantAgentRun, AssistantAgentRun.id == AssistantAction.run_id)
            .where(
                AssistantAction.conversation_id == conversation_id,
                AssistantAction.user_id == user_id,
                or_(
                    AssistantAction.status.in_(
                        [
                            AssistantActionStatus.PENDING,
                            AssistantActionStatus.APPROVED,
                            AssistantActionStatus.EXECUTING,
                            AssistantActionStatus.RECONCILIATION_REQUIRED,
                        ]
                    ),
                    (
                        (AssistantAction.status == AssistantActionStatus.REJECTED)
                        & (AssistantAgentRun.status == AssistantAgentRunStatus.WAITING_APPROVAL)
                    ),
                ),
            )
            .order_by(AssistantAction.created_at, AssistantAction.id)
        )
        return list(result.scalars().all())

    async def expire_pending(self, *, conversation_id: UUID, user_id: UUID, now) -> int:
        expired_run_ids = select(AssistantAction.run_id).where(
            AssistantAction.conversation_id == conversation_id,
            AssistantAction.user_id == user_id,
            AssistantAction.status == AssistantActionStatus.PENDING,
            AssistantAction.expires_at <= now,
        )
        expired_message_ids = select(AssistantAgentRun.assistant_message_id).where(
            AssistantAgentRun.id.in_(expired_run_ids),
            AssistantAgentRun.status == AssistantAgentRunStatus.WAITING_APPROVAL,
        )
        # Action、Run 和承载确认卡片的 assistant message 必须在同一事务中
        # 一起终止，否则前端会永久显示“等待审批”。
        await self.session.execute(
            update(AssistantMessage)
            .where(
                AssistantMessage.id.in_(expired_message_ids),
                AssistantMessage.status == AssistantMessageStatus.WAITING_APPROVAL,
            )
            .values(status=AssistantMessageStatus.CANCELED)
        )
        await self.session.execute(
            update(AssistantAgentRun)
            .where(
                AssistantAgentRun.id.in_(expired_run_ids),
                AssistantAgentRun.status == AssistantAgentRunStatus.WAITING_APPROVAL,
            )
            .values(status=AssistantAgentRunStatus.EXPIRED, completed_at=now)
        )
        result = await self.session.execute(
            update(AssistantAction)
            .where(
                AssistantAction.conversation_id == conversation_id,
                AssistantAction.user_id == user_id,
                AssistantAction.status == AssistantActionStatus.PENDING,
                AssistantAction.expires_at <= now,
            )
            .values(
                status=AssistantActionStatus.EXPIRED,
                result_code="APPROVAL_EXPIRED",
                version=AssistantAction.version + 1,
            )
        )
        return int(cast(Any, result).rowcount or 0)

    async def decide(
        self,
        *,
        action_id: UUID,
        user_id: UUID,
        expected_version: int,
        arguments_hash: str,
        status: AssistantActionStatus,
        now,
    ) -> AssistantAction | None:
        result = await self.session.execute(
            update(AssistantAction)
            .where(
                AssistantAction.id == action_id,
                AssistantAction.user_id == user_id,
                AssistantAction.status == AssistantActionStatus.PENDING,
                AssistantAction.version == expected_version,
                AssistantAction.arguments_hash == arguments_hash,
                AssistantAction.expires_at > now,
            )
            .values(
                status=status,
                decided_at=now,
                version=AssistantAction.version + 1,
            )
            .returning(AssistantAction)
        )
        return result.scalar_one_or_none()

    async def begin_execution(self, *, action_id: UUID, user_id: UUID) -> AssistantAction | None:
        result = await self.session.execute(
            update(AssistantAction)
            .where(
                AssistantAction.id == action_id,
                AssistantAction.user_id == user_id,
                AssistantAction.status == AssistantActionStatus.APPROVED,
            )
            .values(status=AssistantActionStatus.EXECUTING)
            .returning(AssistantAction)
        )
        return result.scalar_one_or_none()

    async def set_result(
        self,
        *,
        action_id: UUID,
        user_id: UUID,
        from_status: AssistantActionStatus,
        status: AssistantActionStatus,
        result_code: str,
        resource_type: str | None = None,
        resource_id: UUID | None = None,
        executed_at=None,
    ) -> AssistantAction | None:
        result = await self.session.execute(
            update(AssistantAction)
            .where(
                AssistantAction.id == action_id,
                AssistantAction.user_id == user_id,
                AssistantAction.status == from_status,
            )
            .values(
                status=status,
                result_code=result_code,
                resource_type=resource_type,
                resource_id=resource_id,
                executed_at=executed_at,
            )
            .returning(AssistantAction)
        )
        return result.scalar_one_or_none()

    async def resolve_reconciliation(
        self,
        *,
        action_id: UUID,
        user_id: UUID,
        expected_version: int,
        status: AssistantActionStatus,
        result_code: str,
        resource_type: str | None,
        resource_id: UUID | None,
        note: str,
        now,
    ) -> AssistantAction | None:
        result = await self.session.execute(
            update(AssistantAction)
            .where(
                AssistantAction.id == action_id,
                AssistantAction.user_id == user_id,
                AssistantAction.status == AssistantActionStatus.RECONCILIATION_REQUIRED,
                AssistantAction.version == expected_version,
            )
            .values(
                status=status,
                result_code=result_code,
                resource_type=resource_type,
                resource_id=resource_id,
                reconciled_at=now,
                reconciled_by=user_id,
                reconciliation_note=note,
                executed_at=now,
                version=AssistantAction.version + 1,
            )
            .returning(AssistantAction)
        )
        return result.scalar_one_or_none()

    async def supersede_pending(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        now,
    ) -> int:
        return await _supersede_actions(
            self.session,
            conversation_id=conversation_id,
            user_id=user_id,
            now=now,
        )


async def _supersede_actions(
    session: AsyncSession, *, conversation_id: UUID, user_id: UUID, now
) -> int:
    """Close replaceable actions and fence side effects whose outcome is uncertain."""
    rejected_waiting_run_ids = select(AssistantAction.run_id).where(
        AssistantAction.conversation_id == conversation_id,
        AssistantAction.user_id == user_id,
        AssistantAction.status == AssistantActionStatus.REJECTED,
    )
    await session.execute(
        update(AssistantAgentRun)
        .where(
            AssistantAgentRun.id.in_(rejected_waiting_run_ids),
            AssistantAgentRun.status == AssistantAgentRunStatus.WAITING_APPROVAL,
        )
        .values(
            status=AssistantAgentRunStatus.CANCELED,
            error_code="SUPERSEDED_BY_NEW_TURN",
            completed_at=now,
        )
    )
    rejectable_run_ids = select(AssistantAction.run_id).where(
        AssistantAction.conversation_id == conversation_id,
        AssistantAction.user_id == user_id,
        AssistantAction.status.in_(
            [
                AssistantActionStatus.PENDING,
                AssistantActionStatus.APPROVED,
            ]
        ),
    )
    await session.execute(
        update(AssistantAgentRun)
        .where(
            AssistantAgentRun.id.in_(rejectable_run_ids),
            AssistantAgentRun.status.in_(
                [
                    AssistantAgentRunStatus.WAITING_APPROVAL,
                    AssistantAgentRunStatus.RUNNING,
                ]
            ),
        )
        .values(
            status=AssistantAgentRunStatus.CANCELED,
            error_code="SUPERSEDED_BY_NEW_TURN",
            completed_at=now,
        )
    )
    result = await session.execute(
        update(AssistantAction)
        .where(
            AssistantAction.conversation_id == conversation_id,
            AssistantAction.user_id == user_id,
            AssistantAction.status.in_(
                [
                    AssistantActionStatus.PENDING,
                    AssistantActionStatus.APPROVED,
                ]
            ),
        )
        .values(
            status=AssistantActionStatus.REJECTED,
            result_code="SUPERSEDED_BY_NEW_TURN",
            decided_at=now,
            version=AssistantAction.version + 1,
        )
    )
    rejected_count = int(cast(Any, result).rowcount or 0)
    uncertain_result = await session.execute(
        select(AssistantAction)
        .where(
            AssistantAction.conversation_id == conversation_id,
            AssistantAction.user_id == user_id,
            AssistantAction.status.in_(
                [
                    AssistantActionStatus.EXECUTING,
                    AssistantActionStatus.RECONCILIATION_REQUIRED,
                ]
            ),
        )
        .with_for_update()
    )
    uncertain_actions = list(uncertain_result.scalars().all())
    if not uncertain_actions:
        return rejected_count
    uncertain_action_ids = [action.id for action in uncertain_actions]
    uncertain_run_ids = [action.run_id for action in uncertain_actions]
    uncertain_pairs = [(action.run_id, action.tool_call_id) for action in uncertain_actions]
    await session.execute(
        update(AssistantToolCall)
        .where(
            tuple_(AssistantToolCall.run_id, AssistantToolCall.tool_call_id).in_(uncertain_pairs),
            AssistantToolCall.status == AssistantToolCallStatus.RUNNING,
        )
        .values(
            status=AssistantToolCallStatus.RECONCILIATION_REQUIRED,
            result_code="SUPERSEDED_SIDE_EFFECT_UNCERTAIN",
            completed_at=now,
        )
    )
    await session.execute(
        update(AssistantAgentRun)
        .where(
            AssistantAgentRun.id.in_(uncertain_run_ids),
            AssistantAgentRun.status.in_(
                [
                    AssistantAgentRunStatus.WAITING_APPROVAL,
                    AssistantAgentRunStatus.RUNNING,
                ]
            ),
        )
        .values(
            status=AssistantAgentRunStatus.FAILED,
            error_code="SIDE_EFFECT_RECONCILIATION_REQUIRED",
            completed_at=now,
        )
    )
    uncertain = await session.execute(
        update(AssistantAction)
        .where(AssistantAction.id.in_(uncertain_action_ids))
        .values(
            status=AssistantActionStatus.RECONCILIATION_REQUIRED,
            result_code="SUPERSEDED_SIDE_EFFECT_UNCERTAIN",
            version=AssistantAction.version + 1,
        )
    )
    return rejected_count + int(cast(Any, uncertain).rowcount or 0)
