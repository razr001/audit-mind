from datetime import date
from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.regulation import (
    KnowledgeCategory,
    KnowledgeVisibility,
    Regulation,
    RegulationChunkStatus,
    RegulationIndexStatus,
    RegulationRuleStatus,
    RegulationStatus,
)
from app.models.regulation_rule import RegulationRule, RegulationRuleType


class RegulationRuleRepository:
    """管理经过来源校验并可供审核使用的结构化法规规则。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def delete_by_regulation(
        self,
        regulation_id: UUID,
    ) -> None:
        """显式删除法规规则，不依赖数据库外键级联。"""
        await self.session.execute(
            delete(RegulationRule).where(
                RegulationRule.regulation_id == regulation_id,
            )
        )

    async def replace_by_regulation(
        self,
        *,
        regulation_id: UUID,
        rules: list[RegulationRule],
    ) -> None:
        """在同一事务中整体替换规则，避免留下部分提取结果。"""
        await self.delete_by_regulation(regulation_id)
        self.session.add_all(rules)

    async def find_by_regulation(
        self,
        regulation_id: UUID,
    ) -> list[RegulationRule]:
        """按照法规原文顺序返回结构化规则。"""
        result = await self.session.execute(
            select(RegulationRule)
            .where(RegulationRule.regulation_id == regulation_id)
            .order_by(RegulationRule.rule_index)
        )
        return list(result.scalars().all())

    async def find_page_by_regulation(
        self,
        *,
        regulation_id: UUID,
        offset: int,
        limit: int,
        rule_type: RegulationRuleType | None = None,
    ) -> tuple[list[RegulationRule], int]:
        """按原文顺序分页，避免大型法规一次返回全部规则原文。"""
        conditions = [RegulationRule.regulation_id == regulation_id]
        if rule_type is not None:
            conditions.append(RegulationRule.rule_type == rule_type)
        result = await self.session.execute(
            select(RegulationRule)
            .where(*conditions)
            .order_by(RegulationRule.rule_index)
            .offset(offset)
            .limit(limit)
        )
        items = list(result.scalars().all())
        total = (
            await self.session.scalar(select(func.count(RegulationRule.id)).where(*conditions)) or 0
        )
        return items, total

    async def count_accessible(self, *, user_id: UUID) -> int:
        """统计当前用户可访问且已完成构建的结构化规则。"""

        total = await self.session.scalar(
            select(func.count(RegulationRule.id))
            .join(Regulation, Regulation.id == RegulationRule.regulation_id)
            .where(
                Regulation.enabled.is_(True),
                Regulation.status == RegulationStatus.READY,
                Regulation.rule_status == RegulationRuleStatus.READY,
                or_(
                    Regulation.visibility == KnowledgeVisibility.SHARED,
                    Regulation.uploaded_by == user_id,
                ),
            )
        )
        return total or 0

    async def find_audit_candidates_by_ids(
        self,
        *,
        rule_ids: list[UUID],
        user_id: UUID,
        audit_as_of: date,
        regulation_ids: list[UUID] | None = None,
        categories: list[KnowledgeCategory] | None = None,
        jurisdictions: list[str] | None = None,
        rule_types: list[RegulationRuleType] | None = None,
    ) -> list[RegulationRule]:
        """按 ES 候选 ID 复核事实数据中的权限、有效期和 READY 状态。"""
        if not rule_ids:
            return []
        conditions = [
            RegulationRule.id.in_(rule_ids),
            Regulation.enabled.is_(True),
            Regulation.status == RegulationStatus.READY,
            Regulation.chunk_status == RegulationChunkStatus.READY,
            Regulation.index_status == RegulationIndexStatus.READY,
            Regulation.rule_status == RegulationRuleStatus.READY,
            or_(
                Regulation.visibility == KnowledgeVisibility.SHARED,
                Regulation.uploaded_by == user_id,
            ),
            or_(Regulation.effective_date.is_(None), Regulation.effective_date <= audit_as_of),
            or_(Regulation.expiration_date.is_(None), Regulation.expiration_date >= audit_as_of),
        ]
        if regulation_ids:
            conditions.append(RegulationRule.regulation_id.in_(regulation_ids))
        if categories:
            conditions.append(Regulation.category.in_(categories))
        if jurisdictions:
            conditions.append(Regulation.jurisdiction.in_(jurisdictions))
        if rule_types:
            conditions.append(RegulationRule.rule_type.in_(rule_types))
        result = await self.session.execute(
            select(RegulationRule)
            .join(Regulation, Regulation.id == RegulationRule.regulation_id)
            .where(*conditions)
        )
        return list(result.scalars().all())
