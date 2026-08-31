from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_failure import AUDIT_EXECUTION_FAILED_MESSAGE
from app.models.audit_task import AuditStage, AuditStatus, AuditTask
from app.models.audit_task_page import AuditTaskPage, AuditTaskPageStatus
from app.schemas.audit_maintenance import AuditTimeoutStage


class AuditMaintenanceRepository:
    """回收进程退出后遗留的流水线或逐页审计状态。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_stale_task_ids(
        self,
        *,
        stage: AuditTimeoutStage,
        stale_before: datetime,
    ) -> list[UUID]:
        """只筛选候选 ID；真正回收必须在取得对应 Redis 总锁后进行。"""
        if stage == AuditTimeoutStage.PIPELINE:
            running_page_exists = (
                select(AuditTaskPage.id)
                .where(
                    AuditTaskPage.task_id == AuditTask.id,
                    AuditTaskPage.status == AuditTaskPageStatus.RUNNING,
                )
                .exists()
            )
            statement = select(AuditTask.id).where(
                AuditTask.status == AuditStatus.RUNNING,
                or_(
                    AuditTask.stage.in_([AuditStage.PARSING, AuditStage.INDEXING]),
                    and_(
                        AuditTask.stage == AuditStage.AUDITING,
                        ~running_page_exists,
                    ),
                ),
                AuditTask.updated_at <= stale_before,
            )
        else:
            statement = (
                select(AuditTaskPage.task_id)
                .join(AuditTask, AuditTask.id == AuditTaskPage.task_id)
                .where(
                    AuditTask.status == AuditStatus.RUNNING,
                    AuditTask.stage == AuditStage.AUDITING,
                    AuditTaskPage.status == AuditTaskPageStatus.RUNNING,
                    AuditTaskPage.started_at <= stale_before,
                )
                .distinct()
            )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def mark_stale_failed(
        self,
        *,
        task_id: UUID,
        stage: AuditTimeoutStage,
        stale_before: datetime,
        completed_at: datetime,
    ) -> int:
        if stage == AuditTimeoutStage.PIPELINE:
            running_page_exists = (
                select(AuditTaskPage.id)
                .where(
                    AuditTaskPage.task_id == AuditTask.id,
                    AuditTaskPage.status == AuditTaskPageStatus.RUNNING,
                )
                .exists()
            )
            statement = (
                update(AuditTask)
                .where(
                    AuditTask.id == task_id,
                    AuditTask.status == AuditStatus.RUNNING,
                    or_(
                        AuditTask.stage.in_([AuditStage.PARSING, AuditStage.INDEXING]),
                        and_(
                            AuditTask.stage == AuditStage.AUDITING,
                            ~running_page_exists,
                        ),
                    ),
                    AuditTask.updated_at <= stale_before,
                )
                .values(
                    status=AuditStatus.FAILED,
                    # 回收任务同时使旧后台执行者持有的 fencing token 失效。
                    lock_version=AuditTask.lock_version + 1,
                    error=AUDIT_EXECUTION_FAILED_MESSAGE,
                    completed_at=completed_at,
                )
            )
            result = cast(CursorResult[Any], await self.session.execute(statement))
            return max(cast(int, cast(object, result.rowcount)), 0)

        # 先锁定仍然超时的页面。若正常执行者正在提交页面结果，这里会等待；
        # 它提交后页面不再是 RUNNING，本次查询便不会接管该页。
        stale_page_result = await self.session.execute(
            select(AuditTaskPage.id)
            .join(AuditTask, AuditTask.id == AuditTaskPage.task_id)
            .where(
                AuditTask.id == task_id,
                AuditTask.status == AuditStatus.RUNNING,
                AuditTask.stage == AuditStage.AUDITING,
                AuditTaskPage.status == AuditTaskPageStatus.RUNNING,
                AuditTaskPage.started_at <= stale_before,
            )
            .with_for_update(of=AuditTaskPage)
        )
        stale_page_ids = list(stale_page_result.scalars().all())
        if not stale_page_ids:
            return 0

        page_statement = (
            update(AuditTaskPage)
            .where(
                AuditTaskPage.task_id == task_id,
                AuditTaskPage.status == AuditTaskPageStatus.RUNNING,
            )
            .values(
                status=AuditTaskPageStatus.FAILED,
                finding_count=0,
                error=AUDIT_EXECUTION_FAILED_MESSAGE,
                completed_at=completed_at,
            )
        )
        task_result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(AuditTask)
                .where(
                    AuditTask.id == task_id,
                    AuditTask.status == AuditStatus.RUNNING,
                    AuditTask.stage == AuditStage.AUDITING,
                )
                .values(
                    status=AuditStatus.PARTIAL_FAILED,
                    lock_version=AuditTask.lock_version + 1,
                    stage=AuditStage.COMPLETED,
                    error=AUDIT_EXECUTION_FAILED_MESSAGE,
                    completed_at=completed_at,
                )
            ),
        )
        if max(cast(int, cast(object, task_result.rowcount)), 0) == 0:
            return 0

        # 递增父任务版本后，旧执行者对所有页面的写入都会被 fencing 拒绝。
        # 因此必须同时回收该任务全部 RUNNING 页面，否则未达到单页超时阈值的
        # 页面会永久停在 RUNNING，并且后续重试不会再领取它们。
        page_result = cast(CursorResult[Any], await self.session.execute(page_statement))
        return max(cast(int, cast(object, page_result.rowcount)), 0)
