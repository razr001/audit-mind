from datetime import timedelta

from fastapi import Depends

from app.core.config import Settings, get_settings
from app.core.logger import logger
from app.infrastructure.db.unit_of_work import UnitOfWork, get_uow
from app.infrastructure.regulation_pipeline_lock import acquire_regulation_pipeline_lease
from app.repositories.regulation_maintenance_repository import (
    RegulationMaintenanceRepository,
)
from app.schemas.regulation_maintenance import (
    RegulationTimeoutResult,
    RegulationTimeoutStage,
)
from app.unit.date import utc_now


class RegulationMaintenanceService:
    """按阶段将超过固定阈值的运行中法规任务标记为失败。"""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        repository: RegulationMaintenanceRepository,
        settings: Settings,
    ) -> None:
        self.uow = uow
        self.repository = repository
        self.settings = settings

    async def mark_timed_out_failed(
        self,
        *,
        stage: RegulationTimeoutStage,
    ) -> RegulationTimeoutResult:
        now = utc_now()
        stale_before = now - timedelta(seconds=self._stale_seconds(stage))
        logger.info(
            "regulation.maintenance.timeout_started",
            stage=stage.value,
            stale_before=stale_before.isoformat(),
        )
        try:
            async with self.uow:
                regulation_ids = await self.repository.find_stale_regulation_ids(
                    stage=stage,
                    stale_before=stale_before,
                )
            updated_count = 0
            skipped_locked = 0
            for regulation_id in regulation_ids:
                # 与正常法规流水线竞争同一把总锁。拿不到锁表示任务仍可能
                # 正常运行，维护任务必须零写入跳过，并等待下一轮调度。
                async with acquire_regulation_pipeline_lease(regulation_id) as acquired:
                    if not acquired:
                        skipped_locked += 1
                        continue
                    # 获取锁后再次带超时条件更新，候选查询之后恢复的任务不会
                    # 被误标为失败。
                    async with self.uow:
                        updated_count += await self.repository.mark_stale_failed(
                            regulation_id=regulation_id,
                            stage=stage,
                            stale_before=stale_before,
                            completed_at=now,
                        )
        except Exception as exc:
            logger.error(
                "regulation.maintenance.timeout_failed",
                stage=stage.value,
                stale_before=stale_before.isoformat(),
                error_type=type(exc).__name__,
            )
            raise

        logger.info(
            "regulation.maintenance.timeout_completed",
            stage=stage.value,
            stale_before=stale_before.isoformat(),
            updated_count=updated_count,
            skipped_locked_count=skipped_locked,
        )
        return RegulationTimeoutResult(
            stage=stage,
            stale_before=stale_before,
            updated_count=updated_count,
        )

    def _stale_seconds(self, stage: RegulationTimeoutStage) -> int:
        return {
            RegulationTimeoutStage.PARSE: self.settings.REGULATION_PARSE_STALE_SECONDS,
            RegulationTimeoutStage.CHUNK: self.settings.REGULATION_CHUNK_STALE_SECONDS,
            RegulationTimeoutStage.INDEX: self.settings.REGULATION_INDEX_STALE_SECONDS,
            RegulationTimeoutStage.RULE: self.settings.REGULATION_RULE_STALE_SECONDS,
        }[stage]


def get_regulation_maintenance_service(
    uow: UnitOfWork = Depends(get_uow),
    settings: Settings = Depends(get_settings),
) -> RegulationMaintenanceService:
    return RegulationMaintenanceService(
        uow=uow,
        repository=RegulationMaintenanceRepository(uow.session),
        settings=settings,
    )
