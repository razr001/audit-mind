from uuid import UUID

from app.core.audit_failure import AUDIT_EXECUTION_FAILED_MESSAGE
from app.infrastructure.db.unit_of_work import UnitOfWork
from app.models.audit_task import AuditStage, AuditStatus, AuditTask
from app.repositories.audit_result_repository import AuditResultRepository
from app.repositories.audit_task_repository import AuditTaskRepository
from app.unit.date import utc_now


class AuditProgressService:
    """使用任务乐观锁集中维护逐页进度和最终状态。"""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        task_repository: AuditTaskRepository,
        result_repository: AuditResultRepository,
    ) -> None:
        self.uow = uow
        self.task_repository = task_repository
        self.result_repository = result_repository

    async def refresh(
        self,
        *,
        task: AuditTask,
        user_id: UUID,
        expected_lock_version: int,
    ) -> None:
        """每页结束后写轻量进度；失效执行者不能覆盖新执行进度。"""
        async with self.uow:
            total, completed, _failed, finding_count = await self.result_repository.summarize_pages(
                task.id
            )
            updated = await self.task_repository.update_pipeline_state(
                task_id=task.id,
                user_id=user_id,
                expected_lock_version=expected_lock_version,
                values={
                    "total_pages": total,
                    "completed_pages": completed,
                    "finding_count": finding_count,
                },
            )
        if updated is None:
            raise RuntimeError("audit pipeline execution superseded")

    async def finalize(
        self,
        *,
        task: AuditTask,
        user_id: UUID,
        expected_lock_version: int,
    ) -> AuditTask:
        """汇总页面并仅由当前乐观锁版本写入任务终态。"""
        async with self.uow:
            total, completed, failed, finding_count = await self.result_repository.summarize_pages(
                task.id
            )
            # 只有每个页面都成功结束时才能宣告任务完成。仅检查 FAILED 数量
            # 会把遗留的 PENDING/RUNNING 页面误判为已经完成。
            all_pages_completed = completed == total
            status = (
                AuditStatus.COMPLETED
                if all_pages_completed and failed == 0
                else AuditStatus.PARTIAL_FAILED
            )
            updated = await self.task_repository.update_pipeline_state(
                task_id=task.id,
                user_id=user_id,
                expected_lock_version=expected_lock_version,
                values={
                    "status": status,
                    "stage": AuditStage.COMPLETED,
                    "total_pages": total,
                    "completed_pages": completed,
                    "finding_count": finding_count,
                    "error": (
                        None if status == AuditStatus.COMPLETED else AUDIT_EXECUTION_FAILED_MESSAGE
                    ),
                    "completed_at": utc_now(),
                },
            )
        if updated is None:
            raise RuntimeError("audit pipeline execution superseded")
        return updated
