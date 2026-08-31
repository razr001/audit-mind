from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assistant import AssistantAgentRun, AssistantAgentRunStatus


class AssistantAgentRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save(self, run: AssistantAgentRun) -> AssistantAgentRun:
        self.session.add(run)
        await self.session.flush()
        return run

    async def find_owned(self, *, run_id: UUID, user_id: UUID) -> AssistantAgentRun | None:
        result = await self.session.execute(
            select(AssistantAgentRun).where(
                AssistantAgentRun.id == run_id,
                AssistantAgentRun.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def set_status(
        self,
        *,
        run_id: UUID,
        user_id: UUID,
        status: AssistantAgentRunStatus,
        error_code: str | None = None,
        completed_at=None,
    ) -> bool:
        result = await self.session.execute(
            update(AssistantAgentRun)
            .where(
                AssistantAgentRun.id == run_id,
                AssistantAgentRun.user_id == user_id,
            )
            .values(
                status=status,
                error_code=error_code,
                completed_at=completed_at,
                lock_version=AssistantAgentRun.lock_version + 1,
            )
        )
        return bool(cast(Any, result).rowcount)

    async def set_status_for_message(
        self,
        *,
        assistant_message_id: UUID,
        status: AssistantAgentRunStatus,
        error_code: str | None = None,
        completed_at=None,
        from_statuses: tuple[AssistantAgentRunStatus, ...] = (
            AssistantAgentRunStatus.RUNNING,
        ),
    ) -> bool:
        result = await self.session.execute(
            update(AssistantAgentRun)
            .where(
                AssistantAgentRun.assistant_message_id == assistant_message_id,
                AssistantAgentRun.status.in_(from_statuses),
            )
            .values(
                status=status,
                error_code=error_code,
                completed_at=completed_at,
                lock_version=AssistantAgentRun.lock_version + 1,
            )
        )
        return bool(cast(Any, result).rowcount)

    async def set_usage(
        self, *, run_id: UUID, user_id: UUID, model_calls: int, tool_calls: int
    ) -> bool:
        result = await self.session.execute(
            update(AssistantAgentRun)
            .where(
                AssistantAgentRun.id == run_id,
                AssistantAgentRun.user_id == user_id,
            )
            .values(
                model_call_count=model_calls,
                tool_call_count=tool_calls,
            )
        )
        return bool(cast(Any, result).rowcount)
