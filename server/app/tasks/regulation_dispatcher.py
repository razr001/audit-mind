import asyncio
from uuid import UUID

from app.core.logger import logger
from app.tasks.regulation_tasks import execute_regulation_pipeline


async def enqueue_regulation_pipeline(
    *,
    regulation_id: UUID,
    user_id: UUID,
    request_id: str,
) -> str:
    """异步投递法规流水线，避免同步 Redis 调用阻塞 FastAPI。"""
    try:
        message = await asyncio.to_thread(
            execute_regulation_pipeline.send,
            str(regulation_id),
            str(user_id),
            request_id,
        )
    except Exception as exc:
        logger.error(
            "regulation.pipeline.enqueue_failed",
            regulation_id=str(regulation_id),
            user_id=str(user_id),
            request_id=request_id,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        raise

    logger.info(
        "regulation.pipeline.enqueued",
        regulation_id=str(regulation_id),
        user_id=str(user_id),
        request_id=request_id,
        message_id=message.message_id,
    )
    return message.message_id
