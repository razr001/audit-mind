from uuid import UUID

import dramatiq
from structlog.contextvars import bound_contextvars

from app.core.config import get_settings
from app.infrastructure.task_broker import task_broker
from app.services.audit_pipeline_service import run_audit_pipeline

settings = get_settings()


@dramatiq.actor(
    broker=task_broker,
    queue_name="audit-pipeline",
    # 业务层已有失败状态、手工重试、XXL-JOB、Redis 锁和乐观锁。
    # 禁用中间件自动重试，避免重复调用 MinerU 和 LLM 等昂贵外部服务。
    max_retries=0,
    # Dramatiq 使用毫秒；显式覆盖默认 10 分钟限制。
    time_limit=settings.DRAMATIQ_AUDIT_PIPELINE_TIME_LIMIT_SECONDS * 1000,
)
async def execute_audit_pipeline(task_id: str, user_id: str, request_id: str) -> None:
    """在独立 Worker 中执行审计流水线。"""
    with bound_contextvars(
        request_id=request_id,
        user_id=user_id,
        task_id=task_id,
    ):
        await run_audit_pipeline(
            task_id=UUID(task_id),
            user_id=UUID(user_id),
        )
