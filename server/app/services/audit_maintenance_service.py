from datetime import timedelta

from fastapi import Depends

from app.core.config import Settings, get_settings
from app.core.logger import logger
from app.infrastructure.audit_pipeline_lock import acquire_audit_pipeline_lease
from app.infrastructure.db.unit_of_work import UnitOfWork, get_uow
from app.repositories.audit_maintenance_repository import AuditMaintenanceRepository
from app.schemas.audit_maintenance import AuditTimeoutResult, AuditTimeoutStage
from app.unit.date import utc_now


class AuditMaintenanceService:
    def __init__(
        self,
        *,
        uow: UnitOfWork,
        repository: AuditMaintenanceRepository,
        settings: Settings,
    ) -> None:
        self.uow = uow
        self.repository = repository
        self.settings = settings

    async def mark_timed_out_failed(
        self, *, stage: AuditTimeoutStage
    ) -> AuditTimeoutResult:
        seconds = (
            self.settings.AUDIT_TASK_STALE_SECONDS
            if stage == AuditTimeoutStage.PIPELINE
            else self.settings.AUDIT_PAGE_STALE_SECONDS
        )
        now = utc_now()
        stale_before = now - timedelta(seconds=seconds)
        async with self.uow:
            task_ids = await self.repository.find_stale_task_ids(
                stage=stage, stale_before=stale_before
            )
        count = 0
        skipped_locked = 0
        for task_id in task_ids:
            # 与后台流水线竞争同一把锁。拿不到表示任务仍可能正常执行，
            # 维护任务必须零写入地跳过，等待下一轮 XXL-JOB 扫描。
            async with acquire_audit_pipeline_lease(task_id) as acquired:
                if not acquired:
                    skipped_locked += 1
                    continue
                async with self.uow:
                    count += await self.repository.mark_stale_failed(
                        task_id=task_id,
                        stage=stage,
                        stale_before=stale_before,
                        completed_at=now,
                    )
        logger.info(
            "audit.maintenance.timeout_completed",
            stage=stage.value,
            stale_before=stale_before.isoformat(),
            updated_count=count,
            skipped_locked_count=skipped_locked,
        )
        return AuditTimeoutResult(
            stage=stage,
            stale_before=stale_before,
            updated_count=count,
        )


def get_audit_maintenance_service(
    uow: UnitOfWork = Depends(get_uow),
    settings: Settings = Depends(get_settings),
) -> AuditMaintenanceService:
    return AuditMaintenanceService(
        uow=uow,
        repository=AuditMaintenanceRepository(uow.session),
        settings=settings,
    )
