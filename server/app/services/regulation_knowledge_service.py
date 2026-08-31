from datetime import datetime, timedelta
from uuid import UUID

from fastapi import Depends

from app.core.config import get_settings
from app.core.error_codes import (
    REGULATION_NOT_FOUND,
    REGULATION_STATUS_INVALID,
)
from app.core.exceptions import BusinessException
from app.core.regulation_failure import REGULATION_FAILURE_CODES
from app.infrastructure.db.unit_of_work import UnitOfWork, get_uow
from app.infrastructure.regulation_rule_vector_store import (
    RegulationRuleVectorStore,
    regulation_rule_vector_store,
)
from app.infrastructure.regulation_vector_store import (
    RegulationVectorStore,
    regulation_vector_store,
)
from app.models.regulation import (
    Regulation,
    RegulationChunkStatus,
    RegulationIndexStatus,
    RegulationRuleStatus,
)
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
from app.services.regulation_chunk_builder import (
    REGULATION_CHUNK_HARD_SIZE as REGULATION_CHUNK_HARD_SIZE,
)
from app.services.regulation_chunk_builder import (
    RegulationChunkBuilder,
)
from app.unit.date import utc_now

settings = get_settings()


class RegulationKnowledgeService(RegulationChunkBuilder):
    """
    将法规 ParseBlock 确定性转换为可检索、可定位的全文 Chunk。

    这条主链不依赖 AI 抽取，因此任何已解析原文都不会因模型漏抽
    而从搜索知识库中丢失。LangExtract 将在独立的规则抽取流程中使用。
    """

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        regulation_repository: RegulationRepository,
        parse_block_repository: RegulationParseBlockRepository,
        chunk_repository: RegulationChunkRepository,
        rule_repository: RegulationRuleRepository,
        vector_store: RegulationVectorStore,
        rule_vector_store: RegulationRuleVectorStore,
    ) -> None:
        self.uow = uow
        self.regulation_repository = regulation_repository
        self.parse_block_repository = parse_block_repository
        self.chunk_repository = chunk_repository
        self.rule_repository = rule_repository
        self.vector_store = vector_store
        self.rule_vector_store = rule_vector_store

    async def build(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
        rebuild: bool = False,
    ) -> Regulation:
        """抢占知识构建任务，并用语义 ParseBlock 替换法规 Chunk。"""
        # started_at 同时是本次构建的 fencing token。维护任务回收超时状态后，
        # 新任务会写入不同时间，旧任务即使恢复也不能提交或标记新任务失败。
        started_at = utc_now()
        stale_before = started_at - timedelta(
            seconds=settings.REGULATION_CHUNK_STALE_SECONDS
        )
        # 第一段短事务通过条件 UPDATE 把 PENDING/FAILED 改为 PROCESSING。
        # 这是并发闸门：同一法规同时只允许一个请求重建全文 Chunk。
        async with self.uow:
            regulation = await self.regulation_repository.claim_for_chunks(
                regulation_id=regulation_id,
                user_id=user_id,
                started_at=started_at,
                stale_before=stale_before,
                allow_ready=rebuild,
            )

            if regulation is None:
                existing = await self.regulation_repository.find_by_id_and_user(
                    regulation_id=regulation_id,
                    user_id=user_id,
                )

                if existing is None:
                    raise BusinessException(
                        REGULATION_NOT_FOUND,
                        "regulation not found",
                    )

                if existing.chunk_status == RegulationChunkStatus.PROCESSING:
                    return existing

                raise BusinessException(
                    REGULATION_STATUS_INVALID,
                    (
                        "regulation chunks cannot be built "
                        f"in status {existing.status.value}/"
                        f"{existing.chunk_status.value}"
                    ),
                )
        expected_lock_version = regulation.lock_version

        try:
            # ParseBlock 是 MinerU 原始结果。读取后立即结束事务，
            # Chunk 的纯内存构建不需要持续占用数据库连接。
            async with self.uow:
                blocks = await self.parse_block_repository.find_by_regulation(regulation_id)

            if not blocks:
                raise RuntimeError("regulation does not contain parse blocks")

            chunks = self._build_chunks(
                regulation_id=regulation_id,
                blocks=blocks,
            )
            if not chunks:
                raise RuntimeError("regulation does not contain semantic chunks")

            # 在触碰 ES 前用短事务校验 fencing token。事务结束后立即释放
            # 行锁和数据库连接，慢速 ES 请求不能占用 PostgreSQL 事务。
            async with self.uow:
                locked = await self.regulation_repository.find_by_id_and_user_for_update(
                    regulation_id=regulation_id,
                    user_id=user_id,
                )

                if locked is None:
                    raise BusinessException(
                        REGULATION_NOT_FOUND,
                        "regulation not found",
                    )

                if (
                    locked.chunk_status != RegulationChunkStatus.PROCESSING
                    or locked.chunk_started_at != started_at
                    or locked.lock_version != expected_lock_version
                ):
                    raise BusinessException(
                        REGULATION_STATUS_INVALID,
                        "regulation chunk state has changed",
                    )

            # Chunk ID 即将整体替换，所有引用旧 Chunk 的结构化规则都已失效。
            # 用空集合执行幂等整体替换，并在删除 PostgreSQL 规则前清空规则
            # 查询副本；否则幽灵规则仍会进入 ES top_k，挤占合法候选名额。
            await self.rule_vector_store.replace_regulation_rules(
                regulation_id=str(regulation_id),
                rules=[],
            )
            # 依赖方规则先清理成功，再以空集合替换来源 Chunk 查询副本。
            # 若规则 ES 替换失败，旧 Chunk 仍保持完整，数据库事实也不变。
            await self.vector_store.replace_regulation_chunks(
                regulation_id=str(regulation_id),
                chunks=[],
            )

            # ES 完成后重新开启短事务并再次校验 fencing token。只有仍属于
            # 当前执行版本的任务才能替换 PostgreSQL 事实数据和提交 READY。
            async with self.uow:
                locked = await self.regulation_repository.find_by_id_and_user_for_update(
                    regulation_id=regulation_id,
                    user_id=user_id,
                )

                if locked is None:
                    raise BusinessException(
                        REGULATION_NOT_FOUND,
                        "regulation not found",
                    )

                if (
                    locked.chunk_status != RegulationChunkStatus.PROCESSING
                    or locked.chunk_started_at != started_at
                    or locked.lock_version != expected_lock_version
                ):
                    raise BusinessException(
                        REGULATION_STATUS_INVALID,
                        "regulation chunk state has changed",
                    )

                # 规则表不依赖数据库级联约束；必须先显式删除引用旧
                # Chunk ID 的规则，再在同一事务中替换 Chunk。
                await self.rule_repository.delete_by_regulation(regulation_id)
                await self.chunk_repository.replace_by_regulation(
                    regulation_id=regulation_id,
                    chunks=chunks,
                )

                # Chunk 替换和 READY 状态在同一事务提交：任一步失败都会回滚，
                # 不会出现状态已完成但规则没有保存的情况。
                locked.chunk_status = RegulationChunkStatus.READY
                # Chunk 来源发生变化后，旧结构化规则不再可靠。
                locked.rule_status = RegulationRuleStatus.PENDING
                locked.rule_error = None
                locked.rule_started_at = None
                locked.rule_completed_at = None
                # Chunk 已被重新生成，旧 ES 向量副本随即失效。
                locked.index_status = RegulationIndexStatus.PENDING
                locked.index_error = None
                locked.index_started_at = None
                locked.index_completed_at = None
                locked.chunk_error = None
                locked.chunk_completed_at = utc_now()

            return locked

        except Exception:
            # 对外只保留稳定失败类别，内部异常内容不得进入持久化字段。
            await self._mark_failed(
                regulation_id=regulation_id,
                user_id=user_id,
                expected_started_at=started_at,
                expected_lock_version=expected_lock_version,
            )
            raise

    async def _mark_failed(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
        expected_started_at: datetime,
        expected_lock_version: int,
    ) -> None:
        """仅当前任务仍持有 fencing token 时写失败状态。"""
        async with self.uow:
            regulation = await self.regulation_repository.find_by_id_and_user_for_update(
                regulation_id=regulation_id,
                user_id=user_id,
            )

            if (
                regulation is not None
                and regulation.chunk_status == RegulationChunkStatus.PROCESSING
                and regulation.chunk_started_at == expected_started_at
                and regulation.lock_version == expected_lock_version
            ):
                regulation.chunk_status = RegulationChunkStatus.FAILED
                regulation.chunk_error = REGULATION_FAILURE_CODES["chunk"]
                regulation.chunk_completed_at = utc_now()


def get_regulation_knowledge_service(
    uow: UnitOfWork = Depends(get_uow),
) -> RegulationKnowledgeService:
    return RegulationKnowledgeService(
        uow=uow,
        regulation_repository=RegulationRepository(uow.session),
        parse_block_repository=RegulationParseBlockRepository(uow.session),
        chunk_repository=RegulationChunkRepository(uow.session),
        rule_repository=RegulationRuleRepository(uow.session),
        vector_store=regulation_vector_store,
        rule_vector_store=regulation_rule_vector_store,
    )
