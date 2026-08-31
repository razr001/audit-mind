import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing
from uuid import UUID

from fastapi import Request
from fastapi.responses import StreamingResponse

from app.ai.agent.services.system_agent_service import SystemAgentService
from app.ai.regulation_qa.errors import (
    REGULATION_QA_VERIFICATION_ERROR_CODE,
    REGULATION_QA_VERIFICATION_ERROR_MESSAGE,
    RegulationCitationVerificationError,
)
from app.api.regulation_queries import encode_sse_event
from app.core.asyncio_utils import await_cancellation_safe
from app.core.config import get_settings
from app.core.error_codes import ASSISTANT_CONVERSATION_BUSY
from app.core.logger import logger
from app.infrastructure.redis_lock import RedisLease
from app.models.assistant import AssistantMessageStatus
from app.services.assistant_service import AssistantService, AssistantTurn

settings = get_settings()

ASSISTANT_TURN_SUPERSEDED_MESSAGE = "回答任务已被新的请求接管，请重试"


def log_turn_started(turn: AssistantTurn, question: str) -> None:
    logger.info(
        "assistant.chat.turn_started",
        question_length=len(question),
        conversation_id=str(turn.conversation.id),
        user_message_id=str(turn.user_message.id),
        assistant_message_id=str(turn.assistant_message.id),
    )


async def timeout_frames(
    assistant_service: AssistantService, turn: AssistantTurn
) -> tuple[str, str]:
    await safe_fail_turn(assistant_service, turn, AssistantMessageStatus.FAILED)
    logger.error(
        "assistant.chat.stream_timed_out",
        timeout_seconds=settings.ASSISTANT_TURN_TIMEOUT_SECONDS,
        conversation_id=str(turn.conversation.id),
        assistant_message_id=str(turn.assistant_message.id),
    )
    return (
        encode_sse_event("error", {"code": 50400, "message": "回答生成超时，请稍后重试"}),
        encode_sse_event("done", {}),
    )


async def citation_error_frames(
    assistant_service: AssistantService, turn: AssistantTurn, exc: Exception
) -> tuple[str, str]:
    await safe_fail_turn(assistant_service, turn, AssistantMessageStatus.FAILED)
    logger.error(
        "assistant.chat.citation_verification_failed",
        error_type=type(exc).__name__,
        conversation_id=str(turn.conversation.id),
        assistant_message_id=str(turn.assistant_message.id),
    )
    return (
        encode_sse_event(
            "error",
            {
                "code": REGULATION_QA_VERIFICATION_ERROR_CODE,
                "message": REGULATION_QA_VERIFICATION_ERROR_MESSAGE,
            },
        ),
        encode_sse_event("done", {}),
    )


async def stream_error_frames(
    assistant_service: AssistantService, turn: AssistantTurn, exc: Exception
) -> tuple[str, str]:
    await safe_fail_turn(assistant_service, turn, AssistantMessageStatus.FAILED)
    logger.error(
        "assistant.chat.stream_failed",
        error_type=type(exc).__name__,
        conversation_id=str(turn.conversation.id),
        assistant_message_id=str(turn.assistant_message.id),
    )
    return (
        encode_sse_event("error", {"code": 50000, "message": "回答生成失败，请稍后重试"}),
        encode_sse_event("done", {}),
    )


async def supersede_turn(
    assistant_service: AssistantService,
    turn: AssistantTurn,
) -> tuple[str, str]:
    """关闭失去所有权的消息，并返回完整的 SSE 错误终态。"""
    await safe_fail_turn(
        assistant_service,
        turn,
        AssistantMessageStatus.FAILED,
    )
    return (
        encode_sse_event(
            "error",
            {
                "code": ASSISTANT_CONVERSATION_BUSY,
                "message": ASSISTANT_TURN_SUPERSEDED_MESSAGE,
            },
        ),
        encode_sse_event("done", {}),
    )


async def assistant_event_stream(
    *,
    turn: AssistantTurn,
    question: str,
    request: Request,
    assistant_service: AssistantService,
    answer_service: SystemAgentService,
    user_id: UUID,
    lease: RedisLease | None = None,
) -> AsyncIterator[str]:
    """转发已核验回答，并负责租约、超时和最终消息状态。"""
    try:
        log_turn_started(turn, question)
        yield encode_sse_event(
            "message-start",
            {
                "conversationId": str(turn.conversation.id),
                "userMessageId": str(turn.user_message.id),
                "assistantMessageId": str(turn.assistant_message.id),
                "title": turn.conversation.title,
            },
        )

        answer_parts: list[str] = []
        sources: list[dict] = []
        answered = False
        answer_started = False
        waiting_approval = False
        # 限制整个回答的最长时间
        async with asyncio.timeout(settings.ASSISTANT_TURN_TIMEOUT_SECONDS):
            async with aclosing(
                answer_service.stream(
                    user_id=user_id,
                    question=question,
                    history=turn.history,
                    conversation_id=turn.conversation.id,
                    assistant_message_id=turn.assistant_message.id,
                    request_id=getattr(getattr(request, "state", None), "request_id", None),
                )
            ) as events:
                async for event in events:
                    if await request.is_disconnected():
                        # 用户关闭页面或主动取消请求时
                        # 把 AI 消息标记为 CANCELED
                        await safe_fail_turn(
                            assistant_service,
                            turn,
                            AssistantMessageStatus.CANCELED,
                        )
                        return
                    if event["type"] == "heartbeat":
                        # 模型长时间没有正文时，发送 SSE 心跳，防止 Nginx、浏览器或者其他中间网络设备认为连接已经失效
                        yield ": ping\n\n"
                        continue
                    if event["type"] == "text-delta":
                        # 第一个正文片段输出前再次检查租约，避免已被重试请求
                        # 替代的旧任务继续向原客户端展示未持久化答案。
                        if not answer_started and lease is not None:
                            if not await lease.is_owned():
                                logger.warning(
                                    "assistant.chat.lease_lost_before_answer",
                                    conversation_id=str(turn.conversation.id),
                                )
                                error_frame, done_frame = await supersede_turn(
                                    assistant_service, turn
                                )
                                yield error_frame
                                yield done_frame
                                return
                            answer_started = True
                        text_delta = event["data"]["textDelta"]
                        answer_parts.append(text_delta)
                        yield encode_sse_event("text-delta", {"textDelta": text_delta})
                        continue
                    if event["type"] == "sources":
                        sources = event["data"]["sources"]
                    elif event["type"] == "confirmation-required":
                        waiting_approval = True
                    elif event["type"] == "verified":
                        answered = event["data"]["answered"]
                    if event["type"] != "done":
                        yield encode_sse_event(event["type"], event["data"])

        if waiting_approval:
            return
        if lease is not None and not await lease.is_owned():
            logger.warning(
                "assistant.chat.lease_lost_before_commit",
                conversation_id=str(turn.conversation.id),
            )
            error_frame, done_frame = await supersede_turn(assistant_service, turn)
            yield error_frame
            yield done_frame
            return
        completed = await assistant_service.finish_turn(
            turn.assistant_message,
            content="".join(answer_parts),
            sources=sources,
            answered=answered,
        )
        if not completed:
            logger.warning(
                "assistant.chat.message_ownership_lost",
                conversation_id=str(turn.conversation.id),
                assistant_message_id=str(turn.assistant_message.id),
            )
            error_frame, done_frame = await supersede_turn(assistant_service, turn)
            yield error_frame
            yield done_frame
            return
        logger.info(
            "assistant.chat.turn_completed",
            answer_length=sum(len(part) for part in answer_parts),
            source_count=len(sources),
            answered=answered,
            conversation_id=str(turn.conversation.id),
            assistant_message_id=str(turn.assistant_message.id),
        )
        yield encode_sse_event("done", {})
    except asyncio.CancelledError:
        await await_cancellation_safe(
            safe_fail_turn(
                assistant_service,
                turn,
                AssistantMessageStatus.CANCELED,
            )
        )
        raise
    except TimeoutError:
        for frame in await timeout_frames(assistant_service, turn):
            yield frame
    except RegulationCitationVerificationError as exc:
        for frame in await citation_error_frames(assistant_service, turn, exc):
            yield frame
    except Exception as exc:
        for frame in await stream_error_frames(assistant_service, turn, exc):
            yield frame
    finally:
        if lease is not None:
            try:
                await await_cancellation_safe(lease.release())
            except Exception as exc:
                logger.warning(
                    "assistant.chat.lock_release_failed",
                    error_type=type(exc).__name__,
                )


async def safe_fail_turn(
    assistant_service: AssistantService,
    turn: AssistantTurn,
    status: AssistantMessageStatus,
) -> None:
    try:
        await assistant_service.fail_turn(turn.assistant_message, status)
    except Exception as exc:
        logger.error(
            "assistant.chat.status_update_failed",
            error_type=type(exc).__name__,
            assistant_message_id=str(turn.assistant_message.id),
        )


def streaming_response(
    *,
    turn: AssistantTurn,
    question: str,
    request: Request,
    assistant_service: AssistantService,
    answer_service: SystemAgentService,
    user_id: UUID,
    lease: RedisLease | None = None,
) -> StreamingResponse:
    return StreamingResponse(
        assistant_event_stream(
            turn=turn,
            question=question,
            request=request,
            assistant_service=assistant_service,
            answer_service=answer_service,
            user_id=user_id,
            lease=lease,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
