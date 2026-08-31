from contextlib import asynccontextmanager
from uuid import UUID

from app.core.config import get_settings
from app.infrastructure.redis_client import RedisClient, redis_client
from app.infrastructure.redis_lock import acquire_redis_lease_handle

REGULATION_PIPELINE_LOCK_PREFIX = "lock:regulation:pipeline:"
REGULATION_RULE_INDEX_MAINTENANCE_LOCK_KEY = "lock:regulation:rule-index:maintenance"

settings = get_settings()


def regulation_pipeline_lock_key(regulation_id: UUID) -> str:
    """所有法规写流程统一使用这一种锁键。"""
    return f"{REGULATION_PIPELINE_LOCK_PREFIX}{regulation_id}"


@asynccontextmanager
async def acquire_regulation_pipeline_lease(
    regulation_id: UUID,
    *,
    client: RedisClient = redis_client,
):
    """法规完整流水线或本地单步操作均只获取这一把总锁。"""
    async with acquire_redis_lease_handle(
        key=regulation_pipeline_lock_key(regulation_id),
        ttl_seconds=settings.REGULATION_PIPELINE_LOCK_TTL_SECONDS,
        # 自动续租只解决正常慢任务，不能让挂死任务永久占锁。
        # 最大持有时间与 Worker 硬时限一致，超时后由 fencing 接管。
        max_hold_seconds=settings.DRAMATIQ_REGULATION_PIPELINE_TIME_LIMIT_SECONDS,
        client=client,
    ) as acquired:
        yield acquired


async def is_regulation_rule_index_maintenance_active(
    *,
    client: RedisClient = redis_client,
) -> bool:
    """O(1) 判断是否正在执行会改变全局规则索引的维护操作。"""
    return bool(await client.client.exists(REGULATION_RULE_INDEX_MAINTENANCE_LOCK_KEY))


@asynccontextmanager
async def acquire_regulation_rule_index_maintenance_lease(
    *,
    client: RedisClient = redis_client,
):
    """供重建、切换或清空全局规则索引时显式阻止新审计。"""
    async with acquire_redis_lease_handle(
        key=REGULATION_RULE_INDEX_MAINTENANCE_LOCK_KEY,
        ttl_seconds=settings.REGULATION_PIPELINE_LOCK_TTL_SECONDS,
        max_hold_seconds=settings.REGULATION_RULE_INDEX_MAINTENANCE_MAX_HOLD_SECONDS,
        client=client,
    ) as acquired:
        yield acquired
