from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Never
from uuid import UUID

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent.repositories.assistant_action_repository import AssistantActionRepository
from app.ai.agent.repositories.assistant_agent_run_repository import AssistantAgentRunRepository
from app.core.error_codes import ASSISTANT_CONVERSATION_NOT_FOUND
from app.core.exceptions import BusinessException
from app.infrastructure.assistant_conversation_cache import (
    AssistantConversationCache,
    assistant_conversation_cache,
)
from app.infrastructure.db.session import get_db
from app.infrastructure.db.unit_of_work import UnitOfWork, get_uow
from app.models.assistant import (
    AssistantAgentRunStatus,
    AssistantConversation,
    AssistantMessage,
    AssistantMessageRole,
    AssistantMessageStatus,
)
from app.repositories.assistant_repository import AssistantRepository
from app.schemas.assistant import AssistantConversationUpdate


@dataclass
class AssistantTurn:
    conversation: AssistantConversation
    user_message: AssistantMessage
    assistant_message: AssistantMessage
    history: list[dict[str, str]]


class AssistantService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        uow: UnitOfWork,
        repository: AssistantRepository,
        action_repository: AssistantActionRepository | None = None,
        run_repository: AssistantAgentRunRepository | None = None,
        conversation_cache: AssistantConversationCache | None = None,
    ) -> None:
        self.session = session
        self.uow = uow
        self.repository = repository
        self.action_repository = action_repository
        self.run_repository = run_repository
        self.conversation_cache = conversation_cache

    async def get_conversation(
        self, conversation_id: UUID, user_id: UUID
    ) -> AssistantConversation:
        """读取会话快照；Redis 未命中或不可用时自动回源数据库。"""

        if self.conversation_cache is not None:
            cached = await self.conversation_cache.get(conversation_id, user_id)
            if cached is not None:
                if cached.user_id == user_id:
                    return cached
                await self.conversation_cache.invalidate(conversation_id, user_id)

        conversation = await self._get_persistent_conversation(conversation_id, user_id)
        if self.conversation_cache is not None:
            await self.conversation_cache.set(conversation)
        return conversation

    async def _get_persistent_conversation(
        self, conversation_id: UUID, user_id: UUID
    ) -> AssistantConversation:
        """从当前 Session 读取可修改实体；写事务不能使用 Redis 快照。"""

        conversation = await self.repository.find_conversation(conversation_id, user_id)
        if conversation is None:
            self._raise_conversation_not_found()
        return conversation

    @staticmethod
    def _raise_conversation_not_found() -> Never:
        raise BusinessException(
            ASSISTANT_CONVERSATION_NOT_FOUND,
            "assistant conversation not found",
        )

    async def list_conversations(
        self, user_id: UUID, offset: int, limit: int
    ) -> tuple[list[AssistantConversation], int]:
        if self.conversation_cache is not None:
            cached = await self.conversation_cache.get_list(
                user_id=user_id,
                offset=offset,
                limit=limit,
            )
            if cached is not None:
                return cached
        items, total = await self.repository.list_conversations(user_id, offset, limit)
        if self.conversation_cache is not None:
            await self.conversation_cache.set_list(
                user_id=user_id,
                offset=offset,
                limit=limit,
                items=items,
                total=total,
            )
        return items, total

    async def rename_conversation(
        self,
        conversation_id: UUID,
        user_id: UUID,
        request: AssistantConversationUpdate,
    ) -> AssistantConversation:
        # 先失效可关闭“数据库已更新、旧缓存尚未删除”的并发窗口；提交后再写新快照。
        if self.conversation_cache is not None:
            await self.conversation_cache.invalidate(conversation_id, user_id)
            await self.conversation_cache.invalidate_list(user_id)
        async with self.uow:
            conversation = await self._get_persistent_conversation(conversation_id, user_id)
            conversation.title = request.title
        if self.conversation_cache is not None:
            await self.conversation_cache.set(conversation)
            await self.conversation_cache.invalidate_list(user_id)
        return conversation

    async def delete_conversation(self, conversation_id: UUID, user_id: UUID) -> None:
        if self.conversation_cache is not None:
            await self.conversation_cache.invalidate(conversation_id, user_id)
            await self.conversation_cache.invalidate_list(user_id)
        async with self.uow:
            conversation = await self._get_persistent_conversation(conversation_id, user_id)
            await self.repository.session.delete(conversation)
        # 再删一次，防止事务执行期间其他请求把旧数据库结果重新填回缓存。
        if self.conversation_cache is not None:
            await self.conversation_cache.invalidate(conversation_id, user_id)
            await self.conversation_cache.invalidate_list(user_id)

    async def list_messages(
        self, conversation_id: UUID, user_id: UUID, offset: int, limit: int
    ) -> tuple[list[AssistantMessage], int]:
        await self.get_conversation(conversation_id, user_id)
        return await self.repository.list_messages(conversation_id, offset, limit)

    async def begin_turn(
        self, conversation_id: UUID, user_id: UUID, question: str
    ) -> AssistantTurn:
        now = datetime.now(timezone.utc)
        if self.conversation_cache is not None:
            # last_message_at 会改变列表排序，事务前后各推进一次版本以隔离并发回填。
            await self.conversation_cache.invalidate_list(user_id)
        async with self.uow:
            conversation = await self._get_persistent_conversation(conversation_id, user_id)
            if self.action_repository is not None:
                await self.action_repository.supersede_pending(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    now=now,
                )
            # 能进入这里说明当前请求已获得该会话的 Redis 租约
            # GENERATING 的旧消息来自异常退出的任务，可以安全关闭后重试。
            await self.repository.fail_generating_messages(conversation_id)
            history_messages = await self.repository.recent_completed_messages(conversation_id)
            history = self._bounded_history(history_messages)
            user_message = await self.repository.save_message(
                AssistantMessage(
                    conversation_id=conversation_id,
                    role=AssistantMessageRole.USER,
                    content=question,
                    status=AssistantMessageStatus.COMPLETED,
                    sources=[],
                )
            )
            assistant_message = await self.repository.save_message(
                AssistantMessage(
                    conversation_id=conversation_id,
                    role=AssistantMessageRole.ASSISTANT,
                    content="",
                    status=AssistantMessageStatus.GENERATING,
                    sources=[],
                )
            )
            conversation.last_message_at = now
        if self.conversation_cache is not None:
            await self.conversation_cache.set(conversation)
            await self.conversation_cache.invalidate_list(user_id)
        return AssistantTurn(conversation, user_message, assistant_message, history)

    async def begin_new_turn(self, user_id: UUID, question: str) -> AssistantTurn:
        """Create the conversation and its first turn in one transaction."""
        now = datetime.now(timezone.utc)
        title = question[:30] + ("…" if len(question) > 30 else "")
        conversation = AssistantConversation(
            user_id=user_id,
            title=title,
            last_message_at=now,
        )
        if self.conversation_cache is not None:
            await self.conversation_cache.invalidate_list(user_id)
        async with self.uow:
            await self.repository.save_conversation(conversation)
            user_message = await self.repository.save_message(
                AssistantMessage(
                    conversation_id=conversation.id,
                    role=AssistantMessageRole.USER,
                    content=question,
                    status=AssistantMessageStatus.COMPLETED,
                    sources=[],
                )
            )
            assistant_message = await self.repository.save_message(
                AssistantMessage(
                    conversation_id=conversation.id,
                    role=AssistantMessageRole.ASSISTANT,
                    content="",
                    status=AssistantMessageStatus.GENERATING,
                    sources=[],
                )
            )
        if self.conversation_cache is not None:
            await self.conversation_cache.set(conversation)
            await self.conversation_cache.invalidate_list(user_id)
        return AssistantTurn(conversation, user_message, assistant_message, [])

    async def finish_turn(
        self,
        assistant_message: AssistantMessage,
        *,
        content: str,
        sources: list[dict],
        answered: bool,
    ) -> bool:
        async with self.uow:
            completed = await self.repository.complete_generating_message(
                assistant_message.id,
                content=content,
                sources=sources,
                answered=answered,
            )
            if completed and self.run_repository is not None:
                await self.run_repository.set_status_for_message(
                    assistant_message_id=assistant_message.id,
                    status=AssistantAgentRunStatus.COMPLETED,
                    completed_at=datetime.now(timezone.utc),
                )
            return completed

    async def fail_waiting_message(self, message_id: UUID) -> None:
        async with self.uow:
            await self.repository.fail_generating_message(message_id)

    async def fail_turn(
        self, assistant_message: AssistantMessage, status: AssistantMessageStatus
    ) -> None:
        message_id = assistant_message.id
        await self.session.rollback()
        async with self.uow:
            if status == AssistantMessageStatus.FAILED:
                current = await self.session.get(AssistantMessage, message_id)
                if (
                    current is not None
                    and current.status == AssistantMessageStatus.WAITING_APPROVAL
                ):
                    return
                failed = await self.repository.fail_generating_message(message_id)
                if failed and self.run_repository is not None:
                    await self.run_repository.set_status_for_message(
                        assistant_message_id=message_id,
                        status=AssistantAgentRunStatus.FAILED,
                        error_code="ASSISTANT_STREAM_FAILED",
                        completed_at=datetime.now(timezone.utc),
                    )
                return
            current = await self.session.get(AssistantMessage, message_id)
            if current is not None and current.status == AssistantMessageStatus.GENERATING:
                current.status = status
                if self.run_repository is not None:
                    await self.run_repository.set_status_for_message(
                        assistant_message_id=message_id,
                        status=AssistantAgentRunStatus.CANCELED,
                        error_code="ASSISTANT_STREAM_CANCELED",
                        completed_at=datetime.now(timezone.utc),
                    )

    @staticmethod
    def _bounded_history(messages: list[AssistantMessage]) -> list[dict[str, str]]:
        remaining = 6000
        history: list[dict[str, str]] = []
        for message in reversed(messages):
            content = message.content[-remaining:]
            if not content:
                continue
            history.append({"role": message.role.value.lower(), "content": content})
            remaining -= len(content)
            if remaining <= 0:
                break
        return list(reversed(history))


def get_assistant_service(
    session: AsyncSession = Depends(get_db),
    uow: UnitOfWork = Depends(get_uow),
) -> AssistantService:
    return AssistantService(
        session=session,
        uow=uow,
        repository=AssistantRepository(session),
        action_repository=AssistantActionRepository(session),
        run_repository=AssistantAgentRunRepository(session),
        conversation_cache=assistant_conversation_cache,
    )
