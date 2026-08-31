from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.regulation import (
    KnowledgeCategory,
    KnowledgeVisibility,
    Regulation,
    RegulationChunkStatus,
    RegulationIndexStatus,
    RegulationRuleStatus,
    RegulationSourceType,
    RegulationStatus,
)


def _is_accessible_in_list(*, user_id: UUID) -> ColumnElement[bool]:
    """普通知识对授权用户可见；删除中的知识只给上传者保留重试入口。"""
    return and_(
        or_(
            Regulation.enabled.is_(True),
            and_(
                Regulation.uploaded_by == user_id,
                Regulation.status == RegulationStatus.DELETING,
            ),
        ),
        or_(
            Regulation.visibility == KnowledgeVisibility.SHARED,
            and_(
                Regulation.visibility == KnowledgeVisibility.PRIVATE,
                Regulation.uploaded_by == user_id,
            ),
        ),
    )


class RegulationRepository:
    """封装知识源访问控制、去重查询及解析/知识化状态抢占。"""
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save(
        self,
        regulation: Regulation,
    ) -> Regulation:
        self.session.add(regulation)
        await self.session.flush()
        return regulation
    async def find_by_id(
        self,
        regulation_id: UUID,
    ) -> Regulation | None:
        result = await self.session.execute(
            select(Regulation).where(
                Regulation.id == regulation_id,
            )
        )
        return result.scalar_one_or_none()
    async def find_by_agent_tool_call(self, *, agent_tool_call_id: UUID, user_id: UUID) -> Regulation | None:
        result = await self.session.execute(
            select(Regulation).where(
                Regulation.agent_tool_call_id == agent_tool_call_id,
                Regulation.uploaded_by == user_id,
            )
        )
        return result.scalar_one_or_none()
    async def find_duplicate_by_content_hash(
        self,
        *,
        content_hash: str,
        visibility: KnowledgeVisibility,
        user_id: UUID,
    ) -> Regulation | None:
        """按可见范围执行内容去重：共享全局去重，私有按用户去重。"""
        conditions = [
            Regulation.content_hash == content_hash,
            Regulation.visibility == visibility,
        ]

        if visibility == KnowledgeVisibility.PRIVATE:
            conditions.append(Regulation.uploaded_by == user_id)
        result = await self.session.execute(select(Regulation).where(*conditions))
        return result.scalar_one_or_none()
    async def find_accessible_by_id(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
    ) -> Regulation | None:
        """查询当前用户可访问的启用知识：共享知识或自己的私有知识。"""
        result = await self.session.execute(
            select(Regulation).where(
                Regulation.id == regulation_id,
                Regulation.enabled.is_(True),
                or_(
                    Regulation.visibility == KnowledgeVisibility.SHARED,
                    and_(
                        Regulation.visibility == KnowledgeVisibility.PRIVATE,
                        Regulation.uploaded_by == user_id,
                    ),
                ),
            )
        )
        return result.scalar_one_or_none()
    async def find_accessible_page(
        self,
        *,
        user_id: UUID,
        offset: int,
        limit: int,
        category: KnowledgeCategory | None = None,
        source_type: RegulationSourceType | None = None,
    ) -> tuple[list[Regulation], int]:
        """返回可访问知识，并让上传者继续看到自己尚未完成的删除。"""
        conditions = [_is_accessible_in_list(user_id=user_id)]

        if category is not None:
            conditions.append(Regulation.category == category)
        if source_type is not None:
            conditions.append(Regulation.source_type == source_type)
        result = await self.session.execute(
            select(Regulation)
            .where(*conditions)
            .order_by(
                Regulation.created_at.desc(),
                Regulation.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
        items = list(result.scalars().all())
        total = await self.session.scalar(select(func.count(Regulation.id)).where(*conditions)) or 0
        return items, total
    async def find_uploaded_page(
        self,
        *,
        user_id: UUID,
        offset: int,
        limit: int,
        category: KnowledgeCategory | None = None,
    ) -> tuple[list[Regulation], int]:
        """返回当前用户上传的全部知识源，包括失败和已停用记录。"""
        conditions = [Regulation.uploaded_by == user_id]
        if category is not None:
            conditions.append(Regulation.category == category)
        result = await self.session.execute(
            select(Regulation)
            .where(*conditions)
            .order_by(
                Regulation.created_at.desc(),
                Regulation.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
        items = list(result.scalars().all())
        total = await self.session.scalar(select(func.count(Regulation.id)).where(*conditions)) or 0
        return items, total
    async def find_by_id_and_user(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
    ) -> Regulation | None:
        result = await self.session.execute(
            select(Regulation).where(
                Regulation.id == regulation_id,
                Regulation.uploaded_by == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def claim_for_parse(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
        started_at: datetime,
        stale_before: datetime,
    ) -> Regulation | None:
        """抢占新解析，或接管没有 MinerU task_id 的超时提交阶段。"""
        statement = (
            update(Regulation)
            .where(
                Regulation.id == regulation_id,
                Regulation.uploaded_by == user_id,
                or_(
                    Regulation.status.in_(
                        [
                            RegulationStatus.UPLOADED,
                            RegulationStatus.FAILED,
                        ]
                    ),
                    and_(
                        Regulation.status == RegulationStatus.PARSING,
                        Regulation.parse_task_id.is_(None),
                        or_(
                            Regulation.parse_started_at.is_(None),
                            Regulation.parse_started_at <= stale_before,
                        ),
                    ),
                ),
            )
            .values(
                status=RegulationStatus.PARSING,
                lock_version=Regulation.lock_version + 1,
                parse_task_id=None,
                parse_error=None,
                parse_started_at=started_at,
                parse_completed_at=None,
            )
            .returning(Regulation)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()
    async def find_by_id_and_user_for_update(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
    ) -> Regulation | None:
        """锁定上传者自己的法规行，供最终结果提交前再次校验状态。"""
        result = await self.session.execute(
            select(Regulation)
            .where(
                Regulation.id == regulation_id,
                Regulation.uploaded_by == user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()
    async def claim_for_chunks(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
        started_at: datetime,
        stale_before: datetime,
        allow_ready: bool = False,
    ) -> Regulation | None:
        """原子抢占 Chunk 构建权；显式重建时也允许接管 READY。"""
        # 条件 UPDATE 同时完成“检查旧状态”和“写入 PROCESSING”，
        # 不会出现先查询后更新之间被并发请求插入的竞态窗口。
        claimable_statuses = [
            RegulationChunkStatus.PENDING,
            RegulationChunkStatus.FAILED,
        ]
        if allow_ready:
            claimable_statuses.append(RegulationChunkStatus.READY)
        statement = (
            update(Regulation)
            .where(
                Regulation.id == regulation_id,
                Regulation.uploaded_by == user_id,
                Regulation.status == RegulationStatus.READY,
                or_(
                    Regulation.chunk_status.in_(claimable_statuses),
                    and_(
                        Regulation.chunk_status == RegulationChunkStatus.PROCESSING,
                        or_(
                            Regulation.chunk_started_at.is_(None),
                            Regulation.chunk_started_at <= stale_before,
                        ),
                    ),
                ),
                # 索引任务可能在 ES 写入旧 Chunk 后才进行数据库 fencing
                # 校验。重建期间禁止它并发运行，避免旧副本在删除后重新出现。
                Regulation.index_status != RegulationIndexStatus.PROCESSING,
            )
            .values(
                chunk_status=RegulationChunkStatus.PROCESSING,
                lock_version=Regulation.lock_version + 1,
                chunk_error=None,
                chunk_started_at=started_at,
                chunk_completed_at=None,
                # Chunk 一旦开始重建，旧向量副本就不再可信。
                index_status=RegulationIndexStatus.PENDING,
                index_error=None,
                index_started_at=None,
                index_completed_at=None,
            )
            .returning(Regulation)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()
    async def claim_for_index(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
        started_at: datetime,
        stale_before: datetime,
    ) -> Regulation | None:
        """原子抢占新任务，或接管已经超时的 PROCESSING 任务。"""
        statement = (
            update(Regulation)
            .where(
                Regulation.id == regulation_id,
                Regulation.uploaded_by == user_id,
                Regulation.enabled.is_(True),
                Regulation.status == RegulationStatus.READY,
                Regulation.chunk_status == RegulationChunkStatus.READY,
                or_(
                    Regulation.index_status.in_(
                        [
                            RegulationIndexStatus.PENDING,
                            RegulationIndexStatus.FAILED,
                        ]
                    ),
                    and_(
                        Regulation.index_status == RegulationIndexStatus.PROCESSING,
                        or_(
                            Regulation.index_started_at.is_(None),
                            Regulation.index_started_at <= stale_before,
                        ),
                    ),
                ),
            )
            .values(
                index_status=RegulationIndexStatus.PROCESSING,
                lock_version=Regulation.lock_version + 1,
                index_error=None,
                index_started_at=started_at,
                index_completed_at=None,
            )
            .returning(Regulation)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def claim_for_rules(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
        started_at: datetime,
        stale_before: datetime,
        allow_ready: bool = False,
    ) -> Regulation | None:
        """持有 Redis 锁后，将可构建状态统一更新为 PROCESSING。

        READY 默认不可重复抢占；只有本地调试接口显式要求重建时，
        才允许把已经成功的规则重新置为 PROCESSING。
        """
        claimable_statuses = [
            RegulationRuleStatus.PENDING,
            RegulationRuleStatus.FAILED,
        ]
        if allow_ready:
            claimable_statuses.append(RegulationRuleStatus.READY)

        statement = (
            update(Regulation)
            .where(
                Regulation.id == regulation_id,
                Regulation.uploaded_by == user_id,
                Regulation.enabled.is_(True),
                Regulation.status == RegulationStatus.READY,
                Regulation.chunk_status == RegulationChunkStatus.READY,
                or_(
                    Regulation.rule_status.in_(claimable_statuses),
                    and_(
                        Regulation.rule_status == RegulationRuleStatus.PROCESSING,
                        or_(
                            Regulation.rule_started_at.is_(None),
                            Regulation.rule_started_at <= stale_before,
                        ),
                    ),
                ),
            )
            .values(
                rule_status=RegulationRuleStatus.PROCESSING,
                lock_version=Regulation.lock_version + 1,
                rule_error=None,
                rule_started_at=started_at,
                rule_completed_at=None,
            )
            .returning(Regulation)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()
