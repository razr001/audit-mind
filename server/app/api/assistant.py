import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.ai.agent.services.assistant_action_reconciliation_service import (
    AssistantActionReconciliationService,
    get_assistant_action_reconciliation_service,
)
from app.ai.agent.services.assistant_action_service import (
    AssistantActionService,
    get_assistant_action_service,
)
from app.ai.agent.services.system_agent_dependency import get_system_agent_service
from app.ai.agent.services.system_agent_service import SystemAgentService
from app.api.assistant_stream import safe_fail_turn, streaming_response
from app.api.dependencies import PaginationDep
from app.api.regulation_queries import encode_sse_event
from app.core.asyncio_utils import await_cancellation_safe
from app.core.config import get_settings
from app.core.error_codes import ASSISTANT_CONVERSATION_BUSY
from app.core.exceptions import BusinessException
from app.core.request_context import bind_current_user, get_request_user
from app.infrastructure.redis_client import redis_client
from app.infrastructure.redis_lock import RedisLease, RedisLeaseLostError
from app.models.assistant import AssistantActionStatus, AssistantMessageStatus
from app.schemas.assistant import (
    AssistantActionDecisionRequest,
    AssistantActionReconciliationRequest,
    AssistantActionResponse,
    AssistantConversationResponse,
    AssistantConversationUpdate,
    AssistantMessageRequest,
    AssistantMessageResponse,
)
from app.schemas.page_result import PageResult
from app.schemas.response import Response
from app.services.assistant_service import AssistantService, get_assistant_service

router = APIRouter(prefix="/assistant", tags=["assistant"], dependencies=[Depends(bind_current_user)])

settings = get_settings()


@router.get("/conversations", response_model=Response[PageResult[AssistantConversationResponse]])
async def list_conversations(
    pagination: PaginationDep,
    assistant_service: Annotated[AssistantService, Depends(get_assistant_service)],
) -> Response[PageResult[AssistantConversationResponse]]:
    conversations, total = await assistant_service.list_conversations(
        get_request_user().user_id, pagination.offset, pagination.limit
    )
    page = PageResult(
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        items=[
            AssistantConversationResponse.model_validate(conversation)
            for conversation in conversations
        ],
    )
    return Response(data=page)


@router.patch(
    "/conversations/{conversation_id}",
    response_model=Response[AssistantConversationResponse],
)
async def rename_conversation(
    conversation_id: UUID,
    body: AssistantConversationUpdate,
    assistant_service: Annotated[AssistantService, Depends(get_assistant_service)],
) -> Response[AssistantConversationResponse]:
    conversation = await assistant_service.rename_conversation(
        conversation_id, get_request_user().user_id, body
    )
    return Response(data=AssistantConversationResponse.model_validate(conversation))


@router.delete("/conversations/{conversation_id}", response_model=Response[None])
async def delete_conversation(
    conversation_id: UUID,
    assistant_service: Annotated[AssistantService, Depends(get_assistant_service)],
) -> Response[None]:
    await assistant_service.delete_conversation(conversation_id, get_request_user().user_id)
    return Response(data=None)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=Response[PageResult[AssistantMessageResponse]],
)
async def list_messages(
    conversation_id: UUID,
    pagination: PaginationDep,
    assistant_service: Annotated[AssistantService, Depends(get_assistant_service)],
) -> Response[PageResult[AssistantMessageResponse]]:
    messages, total = await assistant_service.list_messages(
        conversation_id,
        get_request_user().user_id,
        pagination.offset,
        pagination.limit,
    )
    page = PageResult(
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        items=[AssistantMessageResponse.model_validate(message) for message in messages],
    )
    return Response(data=page)


@router.get(
    "/conversations/{conversation_id}/actions/active",
    response_model=Response[list[AssistantActionResponse]],
)
async def list_active_actions(
    conversation_id: UUID,
    action_service: Annotated[AssistantActionService, Depends(get_assistant_action_service)],
) -> Response[list[AssistantActionResponse]]:
    active_actions = await action_service.list_active(
        conversation_id=conversation_id,
        user_id=get_request_user().user_id,
    )
    actions = [AssistantActionResponse.model_validate(action) for action in active_actions]
    return Response(data=actions)


@router.post(
    "/actions/{action_id}/reconciliation",
    response_model=Response[AssistantActionResponse],
)
async def reconcile_assistant_action(
    action_id: UUID,
    body: AssistantActionReconciliationRequest,
    action_service: Annotated[AssistantActionService, Depends(get_assistant_action_service)],
    reconciliation_service: Annotated[
        AssistantActionReconciliationService,
        Depends(get_assistant_action_reconciliation_service),
    ],
) -> Response[AssistantActionResponse]:
    user_id = get_request_user().user_id
    action = await action_service.get_owned(action_id=action_id, user_id=user_id)
    reconciled = await reconciliation_service.reconcile(action=action, request=body)
    return Response(data=AssistantActionResponse.model_validate(reconciled))


@router.post("/actions/{action_id}/decision", response_class=StreamingResponse)
async def decide_assistant_action(
    action_id: UUID,
    body: AssistantActionDecisionRequest,
    assistant_service: Annotated[AssistantService, Depends(get_assistant_service)],
    action_service: Annotated[AssistantActionService, Depends(get_assistant_action_service)],
    system_agent_service: Annotated[SystemAgentService, Depends(get_system_agent_service)],
) -> StreamingResponse:
    user_id = get_request_user().user_id
    pending_action = await action_service.get_owned(action_id=action_id, user_id=user_id)
    lease = await _acquire_conversation_lease(
        user_id=user_id,
        conversation_id=pending_action.conversation_id,
    )
    try:
        action = await action_service.decide(
            action_id=action_id,
            user_id=user_id,
            expected_version=body.version,
            decision=body.decision,
            arguments_hash=body.arguments_hash,
        )
    except BaseException:
        await await_cancellation_safe(lease.release())
        raise

    async def event_stream():
        agent_run = None
        final_persisted = False
        try:
            agent_run = await system_agent_service.get_run(run_id=action.run_id, user_id=user_id)
            async for event in system_agent_service.resume_stream(
                action=action,
                decision=body.decision,
                lease=lease,
            ):
                if event["type"] != "final-result":
                    yield encode_sse_event(event["type"], event["data"])
                    continue
                final_result = event["data"]
                final_persisted = True
                for start in range(0, len(final_result["answer"]), 24):
                    yield encode_sse_event(
                        "text-delta",
                        {"textDelta": final_result["answer"][start : start + 24]},
                    )
                yield encode_sse_event("sources", {"sources": final_result["sources"]})
                yield encode_sse_event("verified", {"answered": final_result["answered"]})
                yield encode_sse_event("done", {"status": "completed"})
            if not final_persisted:
                raise RuntimeError("assistant agent resume produced no final result")
        except asyncio.CancelledError:
            if agent_run is not None:
                async def persist_cancelled_state() -> None:
                    current_action = await action_service.get_owned(
                        action_id=action.id,
                        user_id=user_id,
                    )
                    if current_action.status == AssistantActionStatus.RECONCILIATION_REQUIRED:
                        await assistant_service.fail_waiting_message(
                            agent_run.assistant_message_id
                        )

                await await_cancellation_safe(persist_cancelled_state())
            raise
        except (TimeoutError, RedisLeaseLostError):
            current_action = await action_service.get_owned(
                action_id=action.id,
                user_id=user_id,
            )
            uncertain = current_action.status == AssistantActionStatus.RECONCILIATION_REQUIRED
            if uncertain and agent_run is not None:
                await assistant_service.fail_waiting_message(agent_run.assistant_message_id)
            yield encode_sse_event(
                "error",
                {
                    "code": 50400,
                    "message": (
                        "操作执行结果不确定，已转入对账状态，请检查资源状态"
                        if uncertain
                        else "操作在产生副作用前中断，请重新发起操作"
                    ),
                },
            )
            yield encode_sse_event("done", {})
        except Exception:
            if agent_run is not None:
                current_action = await action_service.get_owned(
                    action_id=action.id,
                    user_id=user_id,
                )
                if current_action.status not in {
                    AssistantActionStatus.APPROVED,
                    AssistantActionStatus.EXECUTING,
                }:
                    await assistant_service.fail_waiting_message(agent_run.assistant_message_id)
            yield encode_sse_event(
                "error",
                {"code": 50000, "message": "操作执行失败，请检查任务状态后重试"},
            )
            yield encode_sse_event("done", {})
        finally:
            await await_cancellation_safe(lease.release())

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


async def _acquire_conversation_lease(
    *,
    user_id: UUID,
    conversation_id: UUID,
) -> RedisLease:
    """为新建和已有会话提供完全相同的跨进程并发保护。"""
    lease = RedisLease(
        client=redis_client,
        key=f"lock:assistant:conversation:{user_id}:{conversation_id}",
        ttl_seconds=settings.ASSISTANT_CONVERSATION_LOCK_TTL_SECONDS,
        max_hold_seconds=settings.ASSISTANT_TURN_TIMEOUT_SECONDS,
    )
    if not await lease.acquire():
        raise BusinessException(
            ASSISTANT_CONVERSATION_BUSY,
            "assistant conversation is generating a response",
        )
    return lease


@router.post(
    "/conversations/stream",
    response_class=StreamingResponse,
)
async def stream_new_conversation(
    body: AssistantMessageRequest,
    request: Request,
    assistant_service: Annotated[AssistantService, Depends(get_assistant_service)],
    answer_service: Annotated[SystemAgentService, Depends(get_system_agent_service)],
) -> StreamingResponse:
    user_id = get_request_user().user_id
    # 新增会话
    turn = await assistant_service.begin_new_turn(user_id, body.question)
    try:
        lease = await _acquire_conversation_lease(
            user_id=user_id,
            conversation_id=turn.conversation.id,
        )
    except BaseException:
        await await_cancellation_safe(
            safe_fail_turn(
                assistant_service,
                turn,
                AssistantMessageStatus.FAILED,
            )
        )
        raise
    return streaming_response(
        turn=turn,
        question=body.question,
        request=request,
        assistant_service=assistant_service,
        answer_service=answer_service,
        user_id=user_id,
        lease=lease,
    )


@router.post(
    "/conversations/{conversation_id}/messages/stream",
    response_class=StreamingResponse,
)
async def stream_message(
    conversation_id: UUID,
    body: AssistantMessageRequest,
    request: Request,
    assistant_service: Annotated[AssistantService, Depends(get_assistant_service)],
    answer_service: Annotated[SystemAgentService, Depends(get_system_agent_service)],
) -> StreamingResponse:
    user_id = get_request_user().user_id
    # 前端只能防止单个标签页重复提交；Redis 租约负责跨进程、跨标签页
    # 保证同一用户的同一会话最多只有一个回答任务。
    lease = await _acquire_conversation_lease(
        user_id=user_id,
        conversation_id=conversation_id,
    )
    try:
        turn = await assistant_service.begin_turn(conversation_id, user_id, body.question)
    except BaseException:
        await await_cancellation_safe(lease.release())
        raise
    return streaming_response(
        turn=turn,
        question=body.question,
        request=request,
        assistant_service=assistant_service,
        answer_service=answer_service,
        user_id=user_id,
        lease=lease,
    )
