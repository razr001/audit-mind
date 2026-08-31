from datetime import date
from uuid import UUID

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.regulation import (
    KnowledgeVisibility,
    Regulation,
    RegulationChunkStatus,
    RegulationIndexStatus,
    RegulationRuleStatus,
    RegulationStatus,
)
from app.models.regulation_chunk import RegulationChunk


class RegulationChunkRepository:
    """管理由 ParseBlock 确定性生成的法规全文 Chunk。"""

    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        self.session = session

    async def replace_by_regulation(
        self,
        *,
        regulation_id: UUID,
        chunks: list[RegulationChunk],
    ) -> None:
        """整体替换法规 Chunk，使失败重试和重复执行保持幂等。"""
        await self.session.execute(
            delete(RegulationChunk).where(
                RegulationChunk.regulation_id == regulation_id,
            )
        )

        self.session.add_all(chunks)

    async def find_by_regulation(
        self,
        regulation_id: UUID,
    ) -> list[RegulationChunk]:
        """按原文中的规则顺序返回法规全部 Chunk。"""
        result = await self.session.execute(
            select(RegulationChunk)
            .where(
                RegulationChunk.regulation_id == regulation_id,
            )
            .order_by(RegulationChunk.chunk_index)
        )

        return list(result.scalars().all())

    async def find_searchable_ids(
        self,
        *,
        chunk_ids: list[UUID],
        user_id: UUID,
        audit_as_of: date | None = None,
    ) -> set[UUID]:
        """用 PostgreSQL 复核 ES 候选确实属于当前可检索的 READY 数据。"""
        if not chunk_ids:
            return set()

        conditions = [
            RegulationChunk.id.in_(chunk_ids),
            Regulation.enabled.is_(True),
            Regulation.status == RegulationStatus.READY,
            Regulation.chunk_status == RegulationChunkStatus.READY,
            Regulation.index_status == RegulationIndexStatus.READY,
            Regulation.rule_status == RegulationRuleStatus.READY,
            or_(
                Regulation.visibility == KnowledgeVisibility.SHARED,
                and_(
                    Regulation.visibility == KnowledgeVisibility.PRIVATE,
                    Regulation.uploaded_by == user_id,
                ),
            ),
        ]
        if audit_as_of is not None:
            conditions.extend(
                [
                    or_(
                        Regulation.effective_date.is_(None),
                        Regulation.effective_date <= audit_as_of,
                    ),
                    or_(
                        Regulation.expiration_date.is_(None),
                        Regulation.expiration_date >= audit_as_of,
                    ),
                ]
            )

        result = await self.session.scalars(
            select(RegulationChunk.id)
            .join(Regulation, Regulation.id == RegulationChunk.regulation_id)
            .where(*conditions)
        )
        return set(result.all())
