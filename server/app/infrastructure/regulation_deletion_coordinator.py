from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from app.infrastructure.redis_client import RedisClient, redis_client
from app.infrastructure.regulation_pipeline_lock import (
    acquire_regulation_pipeline_lease,
)


@dataclass(frozen=True)
class RegulationDeletionGuard:
    acquired: bool
    reason: str | None = None


class RegulationDeletionCoordinator:
    """用法规级总锁协调同一法规的处理和物理删除。"""

    def __init__(self, *, client: RedisClient = redis_client) -> None:
        self.client = client

    @asynccontextmanager
    async def acquire(self, regulation_id: UUID):
        # 删除复用法规流水线总锁，避免解析、分块、索引和规则生成与删除交叉。
        async with acquire_regulation_pipeline_lease(
            regulation_id,
            client=self.client,
        ) as regulation_acquired:
            if not regulation_acquired:
                yield RegulationDeletionGuard(False, "regulation_processing")
                return

            # 审计在候选规则加载后使用内存数据并保存来源快照，因此无需扫描、
            # 阻塞所有审计任务；数据库二次校验会过滤尚未加载就被删除的规则。
            yield RegulationDeletionGuard(True)


regulation_deletion_coordinator = RegulationDeletionCoordinator()
