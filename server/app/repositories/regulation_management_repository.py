from uuid import UUID

from sqlalchemy import and_, delete, or_, update

from app.models.regulation import (
    Regulation,
    RegulationChunkStatus,
    RegulationIndexStatus,
    RegulationRuleStatus,
    RegulationStatus,
)
from app.repositories.regulation_repository import RegulationRepository


class RegulationManagementRepository(RegulationRepository):
    """补充法规物理删除需要的领取和 fencing 操作。"""

    async def claim_for_deletion(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
    ) -> Regulation | None:
        """持久化删除意图并领取新版本，供失败后的删除请求继续执行。"""
        result = await self.session.execute(
            update(Regulation)
            .where(
                Regulation.id == regulation_id,
                Regulation.uploaded_by == user_id,
                or_(
                    Regulation.status == RegulationStatus.DELETING,
                    Regulation.status == RegulationStatus.FAILED,
                    Regulation.chunk_status == RegulationChunkStatus.FAILED,
                    Regulation.index_status == RegulationIndexStatus.FAILED,
                    Regulation.rule_status == RegulationRuleStatus.FAILED,
                    and_(
                        Regulation.status == RegulationStatus.READY,
                        Regulation.chunk_status == RegulationChunkStatus.READY,
                        Regulation.index_status == RegulationIndexStatus.READY,
                        Regulation.rule_status == RegulationRuleStatus.READY,
                    ),
                ),
            )
            .values(
                status=RegulationStatus.DELETING,
                enabled=False,
                lock_version=Regulation.lock_version + 1,
            )
            .returning(Regulation)
        )
        return result.scalar_one_or_none()

    async def delete_if_lock_version(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
        expected_lock_version: int,
    ) -> bool:
        """仅删除仍属于当前执行版本的知识源；数据库级联清理块数据。"""
        result = await self.session.execute(
            delete(Regulation)
            .where(
                Regulation.id == regulation_id,
                Regulation.uploaded_by == user_id,
                Regulation.status == RegulationStatus.DELETING,
                Regulation.lock_version == expected_lock_version,
            )
            .returning(Regulation.id)
        )
        return result.scalar_one_or_none() is not None
