from datetime import datetime, timedelta
from uuid import UUID

from app.ai.regulation.extractor import (
    ComplianceRuleExtractor,
)
from app.core.config import get_settings
from app.core.error_codes import (
    REGULATION_NOT_FOUND,
    REGULATION_STATUS_INVALID,
)
from app.core.exceptions import BusinessException
from app.core.logger import logger
from app.core.regulation_failure import REGULATION_FAILURE_CODES, log_regulation_failure
from app.infrastructure.db.unit_of_work import UnitOfWork
from app.models.regulation import (
    Regulation,
    RegulationChunkStatus,
    RegulationRuleStatus,
    RegulationStatus,
)
from app.models.regulation_chunk import RegulationChunk
from app.models.regulation_parse_block import RegulationParseBlock
from app.models.regulation_rule import RegulationRule, RegulationRuleType
from app.repositories.regulation_chunk_repository import (
    RegulationChunkRepository,
)
from app.repositories.regulation_parse_block_repository import (
    RegulationParseBlockRepository,
)
from app.repositories.regulation_repository import RegulationRepository
from app.repositories.regulation_rule_repository import (
    RegulationRuleRepository,
)
from app.services.regulation_rule_builder import RegulationRuleBuilder
from app.services.regulation_rule_index_service import RegulationRuleIndexService
from app.services.regulation_rule_publisher import publish_regulation_rules
from app.unit.date import utc_now

settings = get_settings()


class RegulationRuleService(RegulationRuleBuilder):
    """从法规 Chunk 提取可追溯的原子规则，并管理构建状态。"""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        regulation_repository: RegulationRepository,
        chunk_repository: RegulationChunkRepository,
        parse_block_repository: RegulationParseBlockRepository,
        rule_repository: RegulationRuleRepository,
        extractor: ComplianceRuleExtractor,
        rule_index_service: RegulationRuleIndexService | None = None,
    ) -> None:
        self.uow = uow
        self.regulation_repository = regulation_repository
        self.chunk_repository = chunk_repository
        self.parse_block_repository = parse_block_repository
        self.rule_repository = rule_repository
        self.extractor = extractor
        self.rule_index_service = rule_index_service

    async def build(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
        rebuild: bool = False,
    ) -> Regulation:
        """兼容内部同步调用；HTTP 接口使用 queue_build 后台执行。"""
        regulation, should_build = await self.queue_build(
            regulation_id=regulation_id,
            user_id=user_id,
            rebuild=rebuild,
        )
        if not should_build:
            return regulation
        return await self.process_queued_build(
            regulation_id=regulation_id,
            user_id=user_id,
            rebuild=rebuild,
        )

    async def queue_build(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
        rebuild: bool = False,
    ) -> tuple[Regulation, bool]:
        """只校验规则构建条件；真正的状态抢占必须在取得 Redis 锁后执行。"""
        async with self.uow:
            regulation = await self.regulation_repository.find_by_id_and_user(
                regulation_id=regulation_id,
                user_id=user_id,
            )
            if regulation is None:
                raise BusinessException(
                    REGULATION_NOT_FOUND,
                    "regulation not found",
                )
            if (
                regulation.chunk_status == RegulationChunkStatus.READY
                and regulation.rule_status == RegulationRuleStatus.READY
                and not rebuild
            ):
                return regulation, False

            allowed_rule_statuses = {
                RegulationRuleStatus.PENDING,
                RegulationRuleStatus.PROCESSING,
                RegulationRuleStatus.FAILED,
            }
            if rebuild:
                allowed_rule_statuses.add(RegulationRuleStatus.READY)
            if (
                regulation.enabled
                and regulation.status == RegulationStatus.READY
                and regulation.chunk_status == RegulationChunkStatus.READY
                and regulation.rule_status in allowed_rule_statuses
            ):
                return regulation, True
            raise BusinessException(
                REGULATION_STATUS_INVALID,
                (
                    "regulation rules cannot be built in status "
                    f"{regulation.status.value}/"
                    f"{regulation.chunk_status.value}/"
                    f"{regulation.rule_status.value}"
                ),
            )

    async def process_queued_build(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
        rebuild: bool = False,
    ) -> Regulation:
        """抢占数据库状态并构建规则；Redis 总锁由最外层调用者统一持有。"""
        started_at = utc_now()
        stale_before = started_at - timedelta(
            seconds=settings.REGULATION_RULE_STALE_SECONDS
        )
        async with self.uow:
            regulation = await self.regulation_repository.claim_for_rules(
                regulation_id=regulation_id,
                user_id=user_id,
                started_at=started_at,
                stale_before=stale_before,
                allow_ready=rebuild,
            )
            if regulation is None:
                regulation = await self.regulation_repository.find_by_id_and_user(
                    regulation_id=regulation_id,
                    user_id=user_id,
                )
                if regulation is None:
                    raise BusinessException(
                        REGULATION_NOT_FOUND,
                        "regulation not found",
                    )
                if regulation.rule_status == RegulationRuleStatus.READY:
                    return regulation
                if regulation.rule_status == RegulationRuleStatus.PROCESSING:
                    return regulation
                raise BusinessException(
                    REGULATION_STATUS_INVALID,
                    "regulation rules cannot be claimed",
                )

        return await self._process_claimed_build(
            regulation=regulation,
            user_id=user_id,
            started_at=started_at,
            expected_lock_version=regulation.lock_version,
        )

    async def _process_claimed_build(
        self,
        *,
        regulation: Regulation,
        user_id: UUID,
        started_at: datetime,
        expected_lock_version: int,
    ) -> Regulation:
        # claim_for_rules 已通过 UPDATE ... RETURNING 原子设置 PROCESSING、
        # 清空旧错误并返回最新实体。Session 使用 expire_on_commit=False，
        # 因此无需在事务提交后立刻重复查询同一行。
        regulation_id = regulation.id

        try:
            # 只读取一次数据库，LangExtract 网络请求期间不占用事务和连接。
            async with self.uow:
                chunks = await self.chunk_repository.find_by_regulation(regulation_id)
                blocks = await self.parse_block_repository.find_by_regulation(regulation_id)

            if not chunks:
                raise RuntimeError("regulation does not contain chunks")

            rules = await self._extract_rules(
                regulation=regulation,
                chunks=chunks,
                blocks=blocks,
            )
            if not rules:
                # 空结果无法区分“原文确实没有规则”和“模型漏抽/校验失败”。
                # 为避免删除旧规则并制造假 READY，必须交由上传者重试或检查。
                raise RuntimeError("no valid compliance rules were extracted")

            return await publish_regulation_rules(
                uow=self.uow,
                regulation_repository=self.regulation_repository,
                rule_repository=self.rule_repository,
                rule_index_service=self.rule_index_service,
                regulation=regulation,
                rules=rules,
                user_id=user_id,
                expected_started_at=started_at,
                expected_lock_version=expected_lock_version,
            )

        except Exception as exc:
            log_regulation_failure(
                "regulation.rule.build_failed",
                regulation_id=regulation_id,
                error=exc,
            )
            await self._mark_failed(
                regulation_id=regulation_id,
                user_id=user_id,
                expected_started_at=started_at,
                expected_lock_version=expected_lock_version,
            )
            raise

    async def get_rules(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
        offset: int,
        limit: int,
        rule_type: RegulationRuleType | None = None,
    ) -> tuple[list[RegulationRule], int]:
        """返回共享法规或当前用户私有法规的结构化规则。"""
        async with self.uow:
            regulation = await self.regulation_repository.find_accessible_by_id(
                regulation_id=regulation_id,
                user_id=user_id,
            )
            if regulation is None:
                raise BusinessException(
                    REGULATION_NOT_FOUND,
                    "regulation not found",
                )

            if regulation.rule_status != RegulationRuleStatus.READY:
                raise BusinessException(
                    REGULATION_STATUS_INVALID,
                    (f"regulation rules are not ready: {regulation.rule_status.value}"),
                )
            return await self.rule_repository.find_page_by_regulation(
                regulation_id=regulation_id,
                offset=offset,
                limit=limit,
                rule_type=rule_type,
            )

    async def count_accessible_rules(self, *, user_id: UUID) -> int:
        """返回当前用户真正可见、且已完成构建的结构化规则总数。"""

        async with self.uow:
            return await self.rule_repository.count_accessible(user_id=user_id)

    async def _extract_rules(
        self,
        *,
        regulation: Regulation,
        chunks: list[RegulationChunk],
        blocks: list[RegulationParseBlock],
    ) -> list[RegulationRule]:
        """逐 Chunk 提取、验证来源、换算全文偏移并去重。"""
        result: list[RegulationRule] = []
        candidate_count = 0
        invalid_structure_count = 0
        invalid_source_count = 0
        duplicate_count = 0

        for chunk in chunks:
            metadata = chunk.chunk_metadata or {}
            extracted_rules = await self.extractor.extract(
                text=chunk.content,
                context_heading=metadata.get("contextHeading"),
                source_type=regulation.source_type,
                language=regulation.language,
                jurisdiction=regulation.jurisdiction,
            )

            for extracted in extracted_rules:
                candidate_count += 1
                if not self._structured_rule_is_complete(extracted):
                    invalid_structure_count += 1
                    log_regulation_failure(
                        "regulation.rule.invalid_structure",
                        regulation_id=regulation.id,
                        error="IncompleteStructuredRule",
                    )
                    # “包括/如下”等引导语必须携带对应列表，防止后续审核只拿到
                    # 一句没有实际约束内容的残缺规则。
                    continue

                rule = self._to_model(
                    regulation=regulation,
                    chunk=chunk,
                    blocks=blocks,
                    extracted=extracted,
                    rule_index=len(result),
                )
                if rule is None:
                    invalid_source_count += 1
                    log_regulation_failure(
                        "regulation.rule.invalid_source",
                        regulation_id=regulation.id,
                        error="UngroundedRule",
                    )
                    # 模型偶尔会把某个结构化字段改写成原文中不存在的近义词。
                    # 该候选规则不能入库，但不应因此丢弃同一法规中其余已经
                    # 通过来源校验的规则。若最终一条有效规则都没有，上层仍会
                    # 将整个构建任务标记为 FAILED，避免发布空的规则集合。
                    continue

                if self._merge_same_source(result, rule):
                    duplicate_count += 1
                    continue

                rule.rule_index = len(result)
                result.append(rule)

        logger.info(
            "regulation.rule.extraction_completed",
            regulation_id=str(regulation.id),
            chunk_count=len(chunks),
            candidate_count=candidate_count,
            invalid_structure_count=invalid_structure_count,
            invalid_source_count=invalid_source_count,
            duplicate_count=duplicate_count,
            rule_count=len(result),
        )
        return result

    async def _mark_failed(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
        expected_started_at: datetime,
        expected_lock_version: int,
    ) -> None:
        """失败只更新状态和日志，不清理旧规则或其他解析资源。"""
        async with self.uow:
            regulation = await self.regulation_repository.find_by_id_and_user_for_update(
                regulation_id=regulation_id,
                user_id=user_id,
            )
            if (
                regulation is not None
                and regulation.rule_status == RegulationRuleStatus.PROCESSING
                and regulation.rule_started_at == expected_started_at
                and regulation.lock_version == expected_lock_version
            ):
                regulation.rule_status = RegulationRuleStatus.FAILED
                regulation.rule_error = REGULATION_FAILURE_CODES["rule"]
                regulation.rule_completed_at = utc_now()
