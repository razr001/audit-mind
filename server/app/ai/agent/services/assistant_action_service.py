import hashlib
import json
from datetime import timedelta
from typing import Any
from uuid import UUID

from fastapi import Depends

from app.ai.agent.repositories.assistant_action_repository import AssistantActionRepository
from app.ai.agent.repositories.assistant_reconciliation_repository import (
    AssistantReconciliationRepository,
)
from app.core.config import get_settings
from app.core.error_codes import ASSISTANT_ACTION_INVALID, ASSISTANT_ACTION_NOT_FOUND
from app.core.exceptions import BusinessException
from app.infrastructure.db.unit_of_work import UnitOfWork, get_uow
from app.models.assistant import (
    AssistantAction,
    AssistantActionRisk,
    AssistantActionStatus,
)
from app.schemas.assistant import AssistantActionDecisionType
from app.unit.date import utc_now

settings = get_settings()


def canonical_action_arguments(arguments: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """稳定序列化工具参数，并生成审批和执行共同使用的 SHA-256 摘要。"""

    encoded = json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return json.loads(encoded), hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class AssistantActionService:
    """管理需要用户确认的写操作 Action 生命周期。"""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        repository: AssistantActionRepository,
        reconciliation_repository: AssistantReconciliationRepository | None = None,
    ) -> None:
        self.uow = uow
        self.repository = repository
        self.reconciliation_repository = reconciliation_repository

    def build_pending(
        self,
        *,
        run_id: UUID,
        conversation_id: UUID,
        user_id: UUID,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        display_summary: str,
        risk_level: AssistantActionRisk = AssistantActionRisk.WRITE,
    ) -> AssistantAction:
        """在内存中构造 PENDING Action；调用方负责与 Run/Message 原子保存。"""

        normalized, arguments_hash = canonical_action_arguments(arguments)
        return AssistantAction(
            run_id=run_id,
            conversation_id=conversation_id,
            user_id=user_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            risk_level=risk_level,
            arguments=normalized,
            arguments_hash=arguments_hash,
            display_summary=display_summary[:500],
            status=AssistantActionStatus.PENDING,
            expires_at=utc_now() + timedelta(seconds=settings.ASSISTANT_AGENT_ACTION_TTL_SECONDS),
        )

    async def list_active(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
    ) -> list[AssistantAction]:
        now = utc_now()
        stale_before = now - timedelta(
            seconds=settings.ASSISTANT_TURN_TIMEOUT_SECONDS + 60
        )
        async with self.uow:
            if self.reconciliation_repository is not None:
                await self.reconciliation_repository.recover_stale_executions(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    stale_before=stale_before,
                    now=now,
                )
            await self.repository.expire_pending(
                conversation_id=conversation_id,
                user_id=user_id,
                now=now,
            )
            return await self.repository.find_active_for_conversation(
                conversation_id=conversation_id,
                user_id=user_id,
            )

    async def get_owned(self, *, action_id: UUID, user_id: UUID) -> AssistantAction:
        now = utc_now()
        async with self.uow:
            if self.reconciliation_repository is not None:
                await self.reconciliation_repository.recover_stale_executions(
                    action_id=action_id,
                    user_id=user_id,
                    stale_before=now - timedelta(
                        seconds=settings.ASSISTANT_TURN_TIMEOUT_SECONDS + 60
                    ),
                    now=now,
                )
            action = await self.repository.find_owned(action_id=action_id, user_id=user_id)
        if action is None:
            raise BusinessException(ASSISTANT_ACTION_NOT_FOUND, "assistant action not found")
        return action

    async def decide(
        self,
        *,
        action_id: UUID,
        user_id: UUID,
        expected_version: int,
        decision: AssistantActionDecisionType,
        arguments_hash: str,
    ) -> AssistantAction:
        """按版本号和参数摘要原子批准/拒绝 Action，并支持安全的重复提交。"""

        target = (
            AssistantActionStatus.APPROVED
            if decision == AssistantActionDecisionType.APPROVE
            else AssistantActionStatus.REJECTED
        )
        async with self.uow:
            action = await self.repository.decide(
                action_id=action_id,
                user_id=user_id,
                expected_version=expected_version,
                arguments_hash=arguments_hash,
                status=target,
                now=utc_now(),
            )
        if action is not None:
            return action
        existing = await self.repository.find_owned(action_id=action_id, user_id=user_id)
        if existing is None:
            raise BusinessException(ASSISTANT_ACTION_NOT_FOUND, "assistant action not found")
        if existing.status == AssistantActionStatus.PENDING and existing.expires_at <= utc_now():
            async with self.uow:
                await self.repository.expire_pending(
                    conversation_id=existing.conversation_id,
                    user_id=user_id,
                    now=utc_now(),
                )
            raise BusinessException(ASSISTANT_ACTION_INVALID, "assistant action is expired")
        if existing.arguments_hash != arguments_hash:
            raise BusinessException(ASSISTANT_ACTION_INVALID, "assistant action arguments changed")
        # 用户或前端可能重发同一个决定。只接受紧邻版本的同向重复决定，
        # 反向决定或更旧版本仍然失败，避免覆盖已经开始的执行。
        idempotent_statuses = {
            AssistantActionDecisionType.APPROVE: {
                AssistantActionStatus.APPROVED,
                AssistantActionStatus.EXECUTING,
            },
            AssistantActionDecisionType.REJECT: {AssistantActionStatus.REJECTED},
        }
        if existing.status in idempotent_statuses[decision] and expected_version in {
            existing.version,
            existing.version - 1,
        }:
            return existing
        raise BusinessException(
            ASSISTANT_ACTION_INVALID,
            "assistant action is expired or no longer pending",
        )

    async def begin_execution(self, *, action_id: UUID, user_id: UUID) -> AssistantAction:
        """把 APPROVED 原子推进为 EXECUTING，作为写工具的执行前置条件。"""

        async with self.uow:
            action = await self.repository.begin_execution(
                action_id=action_id,
                user_id=user_id,
            )
        if action is not None:
            return action
        existing = await self.get_owned(action_id=action_id, user_id=user_id)
        if existing.status == AssistantActionStatus.EXECUTING:
            return existing
        raise BusinessException(ASSISTANT_ACTION_INVALID, "assistant action cannot execute")

    async def mark_failed(self, *, action_id: UUID, user_id: UUID) -> None:
        async with self.uow:
            await self.repository.set_result(
                action_id=action_id,
                user_id=user_id,
                from_status=AssistantActionStatus.EXECUTING,
                status=AssistantActionStatus.FAILED,
                result_code="TOOL_EXECUTION_FAILED",
                executed_at=utc_now(),
            )

    async def mark_interrupted(self, *, action_id: UUID, user_id: UUID) -> None:
        """确认副作用尚未开始时，关闭被取消或超时的执行中 Action。"""

        async with self.uow:
            await self.repository.set_result(
                action_id=action_id,
                user_id=user_id,
                from_status=AssistantActionStatus.EXECUTING,
                status=AssistantActionStatus.FAILED,
                result_code="INTERRUPTED_BEFORE_SIDE_EFFECT",
                executed_at=utc_now(),
            )

    async def mark_reconciliation_required(
        self, *, action_id: UUID, user_id: UUID
    ) -> None:
        async with self.uow:
            await self.repository.set_result(
                action_id=action_id,
                user_id=user_id,
                from_status=AssistantActionStatus.EXECUTING,
                status=AssistantActionStatus.RECONCILIATION_REQUIRED,
                result_code="INTERRUPTED_SIDE_EFFECT_UNCERTAIN",
                executed_at=utc_now(),
            )

def get_assistant_action_service(
    uow: UnitOfWork = Depends(get_uow),
) -> AssistantActionService:
    return AssistantActionService(
        uow=uow,
        repository=AssistantActionRepository(uow.session),
        reconciliation_repository=AssistantReconciliationRepository(uow.session),
    )
