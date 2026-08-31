from uuid import UUID

import dramatiq
from structlog.contextvars import bound_contextvars

from app.core.config import get_settings
from app.infrastructure.task_broker import task_broker
from app.services.regulation_pipeline_service import run_regulation_pipeline

settings = get_settings()


@dramatiq.actor(
    broker=task_broker,
    queue_name="regulation-pipeline",
    # 流水线已经通过 Redis 总锁和阶段状态实现幂等续跑。自动重试会放大
    # MinerU、Embedding 和 LLM 调用，因此失败后交给用户或维护任务重试。
    max_retries=0,
    # Dramatiq 使用毫秒；显式覆盖默认 10 分钟限制。
    time_limit=settings.DRAMATIQ_REGULATION_PIPELINE_TIME_LIMIT_SECONDS * 1000,
)
async def execute_regulation_pipeline(
    regulation_id: str,
    user_id: str,
    request_id: str,
) -> None:
    """在独立 Worker 中执行完整法规处理流水线。"""
    with bound_contextvars(
        request_id=request_id,
        user_id=user_id,
        regulation_id=regulation_id,
    ):
        await run_regulation_pipeline(
            regulation_id=UUID(regulation_id),
            user_id=UUID(user_id),
        )
