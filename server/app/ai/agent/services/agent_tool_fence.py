from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.error_codes import ASSISTANT_ACTION_INVALID
from app.core.exceptions import BusinessException
from app.models.assistant import AssistantToolCall, AssistantToolCallStatus


async def require_running_agent_tool_call(
    session: AsyncSession,
    agent_tool_call_id: UUID | None,
) -> None:
    """Lock the execution token before an Agent-created resource is committed."""
    if agent_tool_call_id is None:
        return
    result = await session.execute(
        select(AssistantToolCall)
        .where(AssistantToolCall.id == agent_tool_call_id)
        .with_for_update()
    )
    call = result.scalar_one_or_none()
    if call is None or call.status != AssistantToolCallStatus.RUNNING:
        raise BusinessException(
            ASSISTANT_ACTION_INVALID,
            "agent tool execution token is no longer active",
        )
