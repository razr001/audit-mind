from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assistant import AssistantToolCall, AssistantToolCallStatus


class AssistantToolCallRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save(self, call: AssistantToolCall) -> AssistantToolCall:
        self.session.add(call)
        await self.session.flush()
        return call

    async def find_by_idempotency_key(
        self, key: str, *, for_update: bool = False
    ) -> AssistantToolCall | None:
        statement = select(AssistantToolCall).where(AssistantToolCall.idempotency_key == key)
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def complete_running(
        self,
        *,
        call_id: UUID,
        result_code: str,
        resource_type: str,
        resource_id: UUID,
        completed_at,
    ) -> AssistantToolCall | None:
        result = await self.session.execute(
            update(AssistantToolCall)
            .where(
                AssistantToolCall.id == call_id,
                AssistantToolCall.status == AssistantToolCallStatus.RUNNING,
            )
            .values(
                status=AssistantToolCallStatus.SUCCEEDED,
                result_code=result_code,
                resource_type=resource_type,
                resource_id=resource_id,
                completed_at=completed_at,
            )
            .returning(AssistantToolCall)
        )
        return result.scalar_one_or_none()

    async def finish_running(
        self,
        *,
        call_id: UUID,
        status: AssistantToolCallStatus,
        result_code: str,
        completed_at,
    ) -> bool:
        result = await self.session.execute(
            update(AssistantToolCall)
            .where(
                AssistantToolCall.id == call_id,
                AssistantToolCall.status == AssistantToolCallStatus.RUNNING,
            )
            .values(
                status=status,
                result_code=result_code,
                completed_at=completed_at,
            )
        )
        return bool(cast(Any, result).rowcount)

    async def find_by_run_and_tool_call(
        self, *, run_id: UUID, tool_call_id: str, for_update: bool = False
    ) -> AssistantToolCall | None:
        statement = select(AssistantToolCall).where(
                AssistantToolCall.run_id == run_id,
                AssistantToolCall.tool_call_id == tool_call_id,
            )
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def resolve_reconciliation(
        self,
        *,
        run_id: UUID,
        tool_call_id: str,
        status: AssistantToolCallStatus,
        result_code: str,
        resource_type: str | None,
        resource_id: UUID | None,
        completed_at,
    ) -> bool:
        result = await self.session.execute(
            update(AssistantToolCall)
            .where(
                AssistantToolCall.run_id == run_id,
                AssistantToolCall.tool_call_id == tool_call_id,
                AssistantToolCall.status.in_([
                    AssistantToolCallStatus.RUNNING,
                    AssistantToolCallStatus.RECONCILIATION_REQUIRED,
                ]),
            )
            .values(
                status=status,
                result_code=result_code,
                resource_type=resource_type,
                resource_id=resource_id,
                completed_at=completed_at,
            )
        )
        return bool(cast(Any, result).rowcount)
