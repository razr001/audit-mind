from contextlib import asynccontextmanager
from uuid import UUID

from app.core.config import get_settings
from app.infrastructure.redis_client import RedisClient, redis_client
from app.infrastructure.redis_lock import acquire_redis_lease_handle

AUDIT_PIPELINE_LOCK_PREFIX = "lock:audit:pipeline:"

settings = get_settings()


def audit_pipeline_lock_key(task_id: UUID) -> str:
    """审计执行和超时回收必须使用完全相同的任务锁键。"""
    return f"{AUDIT_PIPELINE_LOCK_PREFIX}{task_id}"


@asynccontextmanager
async def acquire_audit_pipeline_lease(
    task_id: UUID,
    *,
    client: RedisClient = redis_client,
):
    """非阻塞获取单个审计任务的总锁，并在持有期间自动续租。"""
    async with acquire_redis_lease_handle(
        key=audit_pipeline_lock_key(task_id),
        ttl_seconds=settings.AUDIT_PIPELINE_LOCK_TTL_SECONDS,
        max_hold_seconds=settings.DRAMATIQ_AUDIT_PIPELINE_TIME_LIMIT_SECONDS,
        client=client,
    ) as acquired:
        yield acquired
