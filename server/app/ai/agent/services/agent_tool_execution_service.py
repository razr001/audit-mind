import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar
from uuid import UUID

from fastapi import Depends

from app.ai.agent.repositories.assistant_action_repository import AssistantActionRepository
from app.ai.agent.repositories.assistant_tool_call_repository import AssistantToolCallRepository
from app.ai.agent.services.assistant_action_service import canonical_action_arguments
from app.ai.agent.services.command_outcome import CommandOutcome
from app.core.asyncio_utils import await_cancellation_safe
from app.core.error_codes import ASSISTANT_ACTION_INVALID
from app.core.exceptions import BusinessException
from app.infrastructure.db.unit_of_work import UnitOfWork, get_uow
from app.models.assistant import (
    AssistantActionStatus,
    AssistantToolCall,
    AssistantToolCallStatus,
)
from app.unit.date import utc_now

T = TypeVar("T")


@dataclass(frozen=True)
class ToolExecutionResult(Generic[T]):
    value: T | None
    call: AssistantToolCall


def tool_call_receipt(call: AssistantToolCall) -> dict[str, str]:
    """把数据库中的工具执行事实转换成可交给最终输出层的精简凭证。"""

    if call.resource_id is None or call.resource_type is None:
        raise RuntimeError("completed write tool is missing its resource receipt")
    partial = call.result_code not in {"SUCCEEDED", "ALREADY_COMPLETED"}
    return {
        "toolName": call.tool_name,
        "status": "PARTIAL" if partial else "SUCCEEDED",
        "resultCode": call.result_code or "SUCCEEDED",
        "resourceType": call.resource_type,
        "resourceId": str(call.resource_id),
    }


class AgentToolExecutionService:
    """写工具的幂等执行围栏。

    它保证：只有已经批准且参数未变化的 Action 才能执行；同一工具调用不会
    被重复创建资源；中断后结果不确定时停止自动重试，转入人工对账。
    """

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        repository: AssistantToolCallRepository,
        action_repository: AssistantActionRepository,
    ) -> None:
        self.uow = uow
        self.repository = repository
        self.action_repository = action_repository

    async def execute(
        self,
        *,
        run_id: UUID,
        user_id: UUID,
        conversation_id: UUID,
        tool_call_id: str,
        tool_name: str,
        arguments: dict,
        operation: Callable[[UUID], Awaitable[CommandOutcome[T]]],
        resource_id: Callable[[T], UUID],
        resource_type: str,
    ) -> ToolExecutionResult[T]:
        """校验批准事实、登记执行令牌、调用业务操作并持久化结果凭证。"""

        # 与审批时采用完全相同的序列化方式，确保“用户批准的参数”没有被替换。
        arguments_hash = canonical_action_arguments(arguments)[1]
        raw_key = (
            f"{user_id}:{conversation_id}:{run_id}:{tool_call_id}:"
            f"{tool_name}:{arguments_hash}"
        )
        idempotency_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        reconciliation_required = False
        async with self.uow:
            # 锁定 Action，且只接受 EXECUTING 状态；PENDING/REJECTED 都不能写数据。
            action = await self.action_repository.find_by_run_and_tool_call(
                run_id=run_id,
                tool_call_id=tool_call_id,
                user_id=user_id,
                conversation_id=conversation_id,
                status=AssistantActionStatus.EXECUTING,
                for_update=True,
            )
            if (
                action is None
                or action.tool_name != tool_name
                or action.arguments_hash != arguments_hash
            ):
                raise BusinessException(
                    ASSISTANT_ACTION_INVALID,
                    "approved action does not match the tool execution arguments",
                )
            existing = await self.repository.find_by_idempotency_key(
                idempotency_key,
                for_update=True,
            )
            if existing is not None:
                # 已成功则复用原凭证；RUNNING 说明上一次可能在副作用后中断，
                # 此时不能猜测是否成功，也不能自动重放。
                existing.retry_count = (existing.retry_count or 0) + 1
                if (
                    existing.status == AssistantToolCallStatus.SUCCEEDED
                    and existing.resource_id is not None
                ):
                    return ToolExecutionResult(None, existing)
                if existing.status == AssistantToolCallStatus.RUNNING:
                    existing.status = AssistantToolCallStatus.RECONCILIATION_REQUIRED
                    existing.result_code = "INTERRUPTED_SIDE_EFFECT_UNCERTAIN"
                reconciliation_required = True
                call = existing
            else:
                call = await self.repository.save(
                    AssistantToolCall(
                        run_id=run_id,
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        arguments_hash=arguments_hash,
                        idempotency_key=idempotency_key,
                        status=AssistantToolCallStatus.RUNNING,
                    )
                )
        if reconciliation_required:
            raise BusinessException(
                ASSISTANT_ACTION_INVALID,
                "agent tool call requires reconciliation before it can continue",
            )
        try:
            # operation 才是真正调用现有业务 Service 的位置。call.id 会写入目标资源，
            # 让故障恢复时能够反查“这次工具调用到底有没有创建资源”。
            outcome = await operation(call.id)
        except (asyncio.CancelledError, GeneratorExit):
            await await_cancellation_safe(self.mark_reconciliation_required(call))
            raise
        except Exception:
            async with self.uow:
                await self.repository.finish_running(
                    call_id=call.id,
                    status=AssistantToolCallStatus.FAILED,
                    result_code="TOOL_EXECUTION_FAILED",
                    completed_at=utc_now(),
                )
            raise
        async with self.uow:
            # 只有仍持有 RUNNING 执行令牌时才能提交成功，防止迟到结果覆盖对账结论。
            completed_call = await self.repository.complete_running(
                call_id=call.id,
                result_code=outcome.result_code,
                resource_type=resource_type,
                resource_id=resource_id(outcome.resource),
                completed_at=utc_now(),
            )
        if completed_call is None:
            raise BusinessException(
                ASSISTANT_ACTION_INVALID,
                "tool execution lost its fencing token and requires reconciliation",
            )
        return ToolExecutionResult(outcome.resource, completed_call)

    async def find_call(
        self, *, run_id: UUID, tool_call_id: str
    ) -> AssistantToolCall | None:
        return await self.repository.find_by_run_and_tool_call(
            run_id=run_id,
            tool_call_id=tool_call_id,
        )

    async def mark_reconciliation_required(self, call: AssistantToolCall) -> None:
        async with self.uow:
            await self.repository.finish_running(
                call_id=call.id,
                status=AssistantToolCallStatus.RECONCILIATION_REQUIRED,
                result_code="INTERRUPTED_SIDE_EFFECT_UNCERTAIN",
                completed_at=utc_now(),
            )


def get_agent_tool_execution_service(
    uow: UnitOfWork = Depends(get_uow),
) -> AgentToolExecutionService:
    return AgentToolExecutionService(
        uow=uow,
        repository=AssistantToolCallRepository(uow.session),
        action_repository=AssistantActionRepository(uow.session),
    )
