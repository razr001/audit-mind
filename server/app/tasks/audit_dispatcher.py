import asyncio
from uuid import UUID

from app.core.logger import logger
from app.tasks.audit_tasks import execute_audit_pipeline


async def enqueue_audit_pipeline(
    *,
    task_id: UUID,
    user_id: UUID,
    request_id: str,
) -> str:
    """把审计任务投递给 Dramatiq，并返回消息 ID。

    Dramatiq 的 send() 是同步 Redis 调用，因此放到工作线程执行，
    避免 Redis 网络延迟阻塞 FastAPI 的 asyncio 事件循环。
    """
    try:
        message = await asyncio.to_thread(
            execute_audit_pipeline.send,
            str(task_id),
            str(user_id),
            request_id,
        )
    except Exception as exc:
        logger.error(
            "audit.pipeline.enqueue_failed",
            task_id=str(task_id),
            user_id=str(user_id),
            request_id=request_id,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        raise

    logger.info(
        "audit.pipeline.enqueued",
        task_id=str(task_id),
        user_id=str(user_id),
        request_id=request_id,
        message_id=message.message_id,
    )
    return message.message_id
