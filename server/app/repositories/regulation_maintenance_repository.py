from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.regulation_failure import REGULATION_FAILURE_CODES
from app.models.regulation import (
    Regulation,
    RegulationChunkStatus,
    RegulationIndexStatus,
    RegulationRuleStatus,
    RegulationStatus,
)
from app.schemas.regulation_maintenance import RegulationTimeoutStage


class RegulationMaintenanceRepository:
    """用单条条件 UPDATE 回收进程退出后遗留的法规任务状态。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_stale_regulation_ids(
        self,
        *,
        stage: RegulationTimeoutStage,
        stale_before: datetime,
    ) -> list[UUID]:
        """只读取超时候选；Service 取得对应 Redis 总锁后才能修改状态。"""
        status_condition, started_at_column = self._stale_conditions(stage)
        result = await self.session.execute(
            select(Regulation.id).where(
                status_condition,
                or_(
                    started_at_column.is_(None),
                    started_at_column <= stale_before,
                ),
            )
        )
        return list(result.scalars().all())

    async def mark_stale_failed(
        self,
        *,
        regulation_id: UUID,
        stage: RegulationTimeoutStage,
        stale_before: datetime,
        completed_at: datetime,
    ) -> int:
        """只更新仍处于运行状态且开始时间已经超时的记录。"""
        if stage == RegulationTimeoutStage.PARSE:
            statement = (
                update(Regulation)
                .where(
                    Regulation.id == regulation_id,
                    Regulation.status == RegulationStatus.PARSING,
                    or_(
                        Regulation.parse_started_at.is_(None),
                        Regulation.parse_started_at <= stale_before,
                    ),
                )
                .values(
                    status=RegulationStatus.FAILED,
                    lock_version=Regulation.lock_version + 1,
                    parse_error=REGULATION_FAILURE_CODES["parse"],
                    parse_completed_at=completed_at,
                )
            )
        elif stage == RegulationTimeoutStage.CHUNK:
            statement = (
                update(Regulation)
                .where(
                    Regulation.id == regulation_id,
                    Regulation.chunk_status == RegulationChunkStatus.PROCESSING,
                    or_(
                        Regulation.chunk_started_at.is_(None),
                        Regulation.chunk_started_at <= stale_before,
                    ),
                )
                .values(
                    chunk_status=RegulationChunkStatus.FAILED,
                    lock_version=Regulation.lock_version + 1,
                    chunk_error=REGULATION_FAILURE_CODES["chunk"],
                    chunk_completed_at=completed_at,
                )
            )
        elif stage == RegulationTimeoutStage.INDEX:
            statement = (
                update(Regulation)
                .where(
                    Regulation.id == regulation_id,
                    Regulation.index_status == RegulationIndexStatus.PROCESSING,
                    or_(
                        Regulation.index_started_at.is_(None),
                        Regulation.index_started_at <= stale_before,
                    ),
                )
                .values(
                    index_status=RegulationIndexStatus.FAILED,
                    lock_version=Regulation.lock_version + 1,
                    index_error=REGULATION_FAILURE_CODES["index"],
                    index_completed_at=completed_at,
                )
            )
        else:
            statement = (
                update(Regulation)
                .where(
                    Regulation.id == regulation_id,
                    Regulation.rule_status == RegulationRuleStatus.PROCESSING,
                    or_(
                        Regulation.rule_started_at.is_(None),
                        Regulation.rule_started_at <= stale_before,
                    ),
                )
                .values(
                    rule_status=RegulationRuleStatus.FAILED,
                    lock_version=Regulation.lock_version + 1,
                    rule_error=REGULATION_FAILURE_CODES["rule"],
                    # 清空本次 fencing token，阻止已经超时的旧任务随后
                    # 覆盖维护任务写入的 FAILED 状态。
                    rule_started_at=None,
                    rule_completed_at=completed_at,
                )
            )

        # 维护更新不需要让当前 Session 在内存中推导对象状态。关闭 synchronize_session
        # 可避免 SQLite 测试和部分驱动在 Python 侧比较时区时间，并减少批量维护开销。
        statement = statement.execution_options(synchronize_session=False)

        # UPDATE 语句实际返回 CursorResult；显式收窄类型，让静态检查器知道
        # rowcount 可用于获取本次被标记为失败的法规数量。
        result = cast(CursorResult[Any], await self.session.execute(statement))
        # SQLAlchemy 使用 memoized_property 实现 rowcount，部分 PyCharm 版本会
        # 将它误判为可调用方法；这里仅收窄静态类型，运行时仍读取属性值。
        affected_rows = cast(int, cast(object, result.rowcount))
        return affected_rows if affected_rows >= 0 else 0

    @staticmethod
    def _stale_conditions(stage: RegulationTimeoutStage):
        """集中维护阶段状态列与开始时间列，避免候选查询和更新条件漂移。"""
        return {
            RegulationTimeoutStage.PARSE: (
                Regulation.status == RegulationStatus.PARSING,
                Regulation.parse_started_at,
            ),
            RegulationTimeoutStage.CHUNK: (
                Regulation.chunk_status == RegulationChunkStatus.PROCESSING,
                Regulation.chunk_started_at,
            ),
            RegulationTimeoutStage.INDEX: (
                Regulation.index_status == RegulationIndexStatus.PROCESSING,
                Regulation.index_started_at,
            ),
            RegulationTimeoutStage.RULE: (
                Regulation.rule_status == RegulationRuleStatus.PROCESSING,
                Regulation.rule_started_at,
            ),
        }[stage]
