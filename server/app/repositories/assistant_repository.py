from typing import Any, cast
from uuid import UUID

from sqlalchemy import exists, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assistant import (
    AssistantConversation,
    AssistantMessage,
    AssistantMessageStatus,
)


class AssistantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save_conversation(self, conversation: AssistantConversation) -> AssistantConversation:
        self.session.add(conversation)
        await self.session.flush()
        return conversation

    async def find_conversation(
        self, conversation_id: UUID, user_id: UUID
    ) -> AssistantConversation | None:
        result = await self.session.execute(
            select(AssistantConversation).where(
                AssistantConversation.id == conversation_id,
                AssistantConversation.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_conversations(
        self, user_id: UUID, offset: int, limit: int
    ) -> tuple[list[AssistantConversation], int]:
        has_messages = exists().where(
            AssistantMessage.conversation_id == AssistantConversation.id
        )
        conditions = (AssistantConversation.user_id == user_id, has_messages)
        result = await self.session.execute(
            select(AssistantConversation)
            .where(*conditions)
            .order_by(
                AssistantConversation.last_message_at.desc().nullslast(),
                AssistantConversation.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
        total = await self.session.scalar(
            select(func.count(AssistantConversation.id)).where(*conditions)
        ) or 0
        return list(result.scalars().all()), total

    async def save_message(self, message: AssistantMessage) -> AssistantMessage:
        self.session.add(message)
        await self.session.flush()
        return message

    async def fail_generating_messages(self, conversation_id: UUID) -> int:
        """关闭已失去 Redis 租约的遗留回答，允许用户安全重试。"""
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(AssistantMessage)
                .where(
                    AssistantMessage.conversation_id == conversation_id,
                    AssistantMessage.status.in_([
                        AssistantMessageStatus.GENERATING,
                        AssistantMessageStatus.WAITING_APPROVAL,
                    ]),
                )
                .values(status=AssistantMessageStatus.FAILED)
            ),
        )
        affected_rows = cast(int, cast(object, result.rowcount))
        return max(0, affected_rows)

    async def complete_generating_message(
        self,
        message_id: UUID,
        *,
        content: str,
        sources: list[dict],
        answered: bool,
    ) -> bool:
        """只完成仍属于当前任务的生成中消息，隔离失效的旧任务。"""
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(AssistantMessage)
                .where(
                    AssistantMessage.id == message_id,
                    AssistantMessage.status.in_([
                        AssistantMessageStatus.GENERATING,
                        AssistantMessageStatus.WAITING_APPROVAL,
                    ]),
                )
                .values(
                    content=content,
                    sources=sources,
                    answered=answered,
                    status=AssistantMessageStatus.COMPLETED,
                )
            ),
        )
        return cast(int, cast(object, result.rowcount)) == 1

    async def fail_generating_message(self, message_id: UUID) -> bool:
        """失败或取消只能覆盖 GENERATING，不能回退已完成的结果。"""
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(AssistantMessage)
                .where(
                    AssistantMessage.id == message_id,
                    AssistantMessage.status.in_([
                        AssistantMessageStatus.GENERATING,
                        AssistantMessageStatus.WAITING_APPROVAL,
                    ]),
                )
                .values(status=AssistantMessageStatus.FAILED)
            ),
        )
        return cast(int, cast(object, result.rowcount)) == 1

    async def pause_generating_message(self, message_id: UUID) -> bool:
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(AssistantMessage)
                .where(
                    AssistantMessage.id == message_id,
                    AssistantMessage.status == AssistantMessageStatus.GENERATING,
                )
                .values(status=AssistantMessageStatus.WAITING_APPROVAL)
            ),
        )
        return cast(int, cast(object, result.rowcount)) == 1

    async def list_messages(
        self, conversation_id: UUID, offset: int, limit: int
    ) -> tuple[list[AssistantMessage], int]:
        result = await self.session.execute(
            select(AssistantMessage)
            .where(AssistantMessage.conversation_id == conversation_id)
            .order_by(AssistantMessage.created_at.desc(), AssistantMessage.id.desc())
            .offset(offset)
            .limit(limit)
        )
        total = await self.session.scalar(
            select(func.count(AssistantMessage.id)).where(
                AssistantMessage.conversation_id == conversation_id
            )
        ) or 0
        return list(result.scalars().all()), total

    async def recent_completed_messages(
        self, conversation_id: UUID, limit: int = 6
    ) -> list[AssistantMessage]:
        result = await self.session.execute(
            select(AssistantMessage)
            .where(
                AssistantMessage.conversation_id == conversation_id,
                AssistantMessage.status == AssistantMessageStatus.COMPLETED,
            )
            .order_by(AssistantMessage.created_at.desc(), AssistantMessage.id.desc())
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))
