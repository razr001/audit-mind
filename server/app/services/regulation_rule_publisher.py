from datetime import datetime
from uuid import UUID, uuid4

from app.core.error_codes import REGULATION_NOT_FOUND, REGULATION_STATUS_INVALID
from app.core.exceptions import BusinessException
from app.core.logger import logger
from app.infrastructure.db.unit_of_work import UnitOfWork
from app.models.regulation import Regulation, RegulationRuleStatus
from app.models.regulation_rule import RegulationRule
from app.repositories.regulation_repository import RegulationRepository
from app.repositories.regulation_rule_repository import RegulationRuleRepository
from app.services.regulation_rule_index_service import RegulationRuleIndexService
from app.unit.date import utc_now


async def publish_regulation_rules(
    *,
    uow: UnitOfWork,
    regulation_repository: RegulationRepository,
    rule_repository: RegulationRuleRepository,
    rule_index_service: RegulationRuleIndexService | None,
    regulation: Regulation,
    rules: list[RegulationRule],
    user_id: UUID,
    expected_started_at: datetime,
    expected_lock_version: int,
) -> Regulation:
    """提交规则事实、同步 ES，最后才将规则阶段标记为 READY。"""
    # SQLAlchemy 的 mapped_column(default=uuid4) 会在 INSERT/flush 时才填充主键。
    # 但 Embedding 和 ES 文档必须在数据库事务外提前构建，因此需要先在应用层
    # 分配稳定 UUID，保证 PostgreSQL 与 ES 始终引用同一条规则，而不是写入
    # 字符串 "None"。重试时保留已经存在的 ID，不重复生成。
    for rule in rules:
        if rule.id is None:
            rule.id = uuid4()

    index_documents = None
    if rule_index_service is not None:
        # Embedding 是外部网络调用，必须在数据库事务外完成。
        index_documents = await rule_index_service.build_documents(
            regulation=regulation,
            rules=rules,
        )
        logger.info(
            "regulation.rule.index_documents_built",
            regulation_id=str(regulation.id),
            rule_count=len(rules),
        )

    async with uow:
        locked = await regulation_repository.find_by_id_and_user_for_update(
            regulation_id=regulation.id,
            user_id=user_id,
        )
        _validate_claim(
            locked=locked,
            expected_started_at=expected_started_at,
            expected_lock_version=expected_lock_version,
        )
        await rule_repository.replace_by_regulation(
            regulation_id=regulation.id,
            rules=rules,
        )

    if rule_index_service is not None and index_documents is not None:
        # 构建 Embedding 后任务可能已被超时接管。ES 不参与 PostgreSQL
        # 事务，因此在写入前必须再次确认当前执行版本仍然有效。
        async with uow:
            locked = await regulation_repository.find_by_id_and_user_for_update(
                regulation_id=regulation.id,
                user_id=user_id,
            )
            _validate_claim(
                locked=locked,
                expected_started_at=expected_started_at,
                expected_lock_version=expected_lock_version,
            )
        logger.info(
            "regulation.rule.index_replace_started",
            regulation_id=str(regulation.id),
            rule_count=len(index_documents),
        )
        await rule_index_service.replace_regulation_rules(
            regulation_id=regulation.id,
            documents=index_documents,
        )
        logger.info(
            "regulation.rule.index_replace_completed",
            regulation_id=str(regulation.id),
            rule_count=len(index_documents),
        )

    async with uow:
        locked = await regulation_repository.find_by_id_and_user_for_update(
            regulation_id=regulation.id,
            user_id=user_id,
        )
        locked = _validate_claim(
            locked=locked,
            expected_started_at=expected_started_at,
            expected_lock_version=expected_lock_version,
        )
        locked.rule_status = RegulationRuleStatus.READY
        locked.rule_error = None
        locked.rule_completed_at = utc_now()
    return locked


def _validate_claim(
    *,
    locked: Regulation | None,
    expected_started_at: datetime,
    expected_lock_version: int,
) -> Regulation:
    if locked is None:
        raise BusinessException(REGULATION_NOT_FOUND, "regulation not found")
    if (
        locked.rule_status != RegulationRuleStatus.PROCESSING
        or locked.rule_started_at != expected_started_at
        or locked.lock_version != expected_lock_version
    ):
        raise BusinessException(
            REGULATION_STATUS_INVALID,
            "regulation rule state has changed",
        )
    return locked
