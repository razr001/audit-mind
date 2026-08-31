from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Document
from app.models.audit_task import AuditStatus, AuditTask


class AuditTaskRepository:
    """封装审核任务查询，并通过所属文档执行用户隔离。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save(self, task: AuditTask) -> AuditTask:
        self.session.add(task)
        await self.session.flush()
        return task

    async def find_by_id_and_user(
        self,
        *,
        task_id: UUID,
        user_id: UUID,
    ) -> AuditTask | None:
        """只返回其 Document 属于当前用户的审核任务。"""
        result = await self.session.execute(
            select(AuditTask)
            .options(selectinload(AuditTask.document))
            .join(AuditTask.document)
            .where(
                AuditTask.id == task_id,
                AuditTask.document.has(user_id=user_id),
            )
        )
        return result.scalar_one_or_none()

    async def find_by_agent_tool_call(
        self, *, agent_tool_call_id: UUID, user_id: UUID
    ) -> AuditTask | None:
        result = await self.session.execute(
            select(AuditTask)
            .options(selectinload(AuditTask.document))
            .join(AuditTask.document)
            .where(
                AuditTask.agent_tool_call_id == agent_tool_call_id,
                AuditTask.document.has(user_id=user_id),
            )
        )
        return result.scalar_one_or_none()


    async def find_page_by_user(
        self,
        *,
        user_id: UUID,
        offset: int,
        limit: int,
        status: AuditStatus | None = None,
        document_id: UUID | None = None,
    ) -> tuple[list[AuditTask], int]:
        """分页查询用户自己的任务，并复用同一组条件计算总数。"""
        filters = [Document.user_id == user_id]
        if status is not None:
            filters.append(AuditTask.status == status)
        if document_id is not None:
            filters.append(AuditTask.document_id == document_id)

        page_result = await self.session.execute(
            select(AuditTask)
            .options(selectinload(AuditTask.document))
            .join(Document, AuditTask.document_id == Document.id)
            .where(*filters)
            .order_by(
                AuditTask.created_at.desc(),
                AuditTask.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
        tasks = list(page_result.scalars().all())

        total = (
            await self.session.scalar(
                select(func.count(AuditTask.id))
                .join(Document, AuditTask.document_id == Document.id)
                .where(*filters)
            )
            or 0
        )

        return tasks, total

    async def update_pipeline_state(
        self,
        *,
        task_id: UUID,
        user_id: UUID,
        values: dict,
        expected_lock_version: int,
    ) -> AuditTask | None:
        """按任务归属条件更新流水线状态，并返回预加载文档的最新任务。"""
        conditions = [
            AuditTask.id == task_id,
            AuditTask.document.has(Document.user_id == user_id),
        ]
        conditions.append(AuditTask.lock_version == expected_lock_version)
        result = await self.session.execute(
            update(AuditTask).where(*conditions).values(**values).returning(AuditTask.id)
        )
        if result.scalar_one_or_none() is None:
            return None
        return await self.find_by_id_and_user(task_id=task_id, user_id=user_id)

    async def claim_pipeline_execution(
        self,
        *,
        task_id: UUID,
        user_id: UUID,
        started_at: datetime,
        stale_before: datetime,
    ) -> int | None:
        """领取新/失败任务，或接管已经超过执行时限的 RUNNING 任务。"""
        statement = (
            update(AuditTask)
            .where(
                AuditTask.id == task_id,
                or_(
                    AuditTask.status.in_(
                        (
                            AuditStatus.CREATED,
                            AuditStatus.FAILED,
                            AuditStatus.PARTIAL_FAILED,
                        )
                    ),
                    and_(
                        AuditTask.status == AuditStatus.RUNNING,
                        or_(
                            AuditTask.started_at.is_(None),
                            AuditTask.started_at <= stale_before,
                        ),
                    ),
                ),
                AuditTask.document.has(Document.user_id == user_id),
            )
            .values(
                status=AuditStatus.RUNNING,
                lock_version=AuditTask.lock_version + 1,
                error=None,
                started_at=started_at,
                completed_at=None,
            )
            .returning(AuditTask.lock_version)
            .execution_options(synchronize_session=False)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def fail_for_rules_maintenance(
        self,
        *,
        task_id: UUID,
        user_id: UUID,
        expected_lock_version: int,
        error: str,
        completed_at: datetime,
    ) -> bool:
        """把尚未结束的任务标记为可重试失败，不覆盖已经完成的结果。"""
        statement = (
            update(AuditTask)
            .where(
                AuditTask.id == task_id,
                AuditTask.lock_version == expected_lock_version,
                AuditTask.status.in_((AuditStatus.CREATED, AuditStatus.RUNNING)),
                AuditTask.document.has(Document.user_id == user_id),
            )
            .values(
                status=AuditStatus.FAILED,
                error=error[:2000],
                completed_at=completed_at,
            )
            .returning(AuditTask.id)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def mark_dispatch_failed(
        self,
        *,
        task_id: UUID,
        user_id: UUID,
        expected_lock_version: int,
        failure_status: AuditStatus,
        error: str,
        completed_at: datetime,
    ) -> AuditTask | None:
        """入队失败时记录可重试状态，不覆盖已经被 Worker 领取的任务。"""
        statement = (
            update(AuditTask)
            .where(
                AuditTask.id == task_id,
                AuditTask.lock_version == expected_lock_version,
                AuditTask.status.in_(
                    (
                        AuditStatus.CREATED,
                        AuditStatus.FAILED,
                        AuditStatus.PARTIAL_FAILED,
                    )
                ),
                AuditTask.document.has(Document.user_id == user_id),
            )
            .values(
                status=failure_status,
                error=error[:2000],
                completed_at=completed_at,
                lock_version=AuditTask.lock_version + 1,
            )
            .returning(AuditTask.id)
        )
        result = await self.session.execute(statement)
        if result.scalar_one_or_none() is None:
            return None

        return await self.find_by_id_and_user(
            task_id=task_id,
            user_id=user_id,
        )
