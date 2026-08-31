from datetime import datetime, timedelta
from uuid import UUID

from fastapi import Depends

from app.ai.embedding import EmbeddingService, get_embedding_service
from app.core.config import get_settings
from app.core.error_codes import (
    REGULATION_NOT_FOUND,
    REGULATION_STATUS_INVALID,
)
from app.core.exceptions import BusinessException
from app.core.regulation_failure import REGULATION_FAILURE_CODES, log_regulation_failure
from app.infrastructure.db.unit_of_work import UnitOfWork, get_uow
from app.infrastructure.regulation_vector_store import (
    RegulationVectorStore,
    regulation_vector_store,
)
from app.models.regulation import (
    Regulation,
    RegulationChunkStatus,
    RegulationIndexStatus,
    RegulationStatus,
)
from app.repositories.regulation_chunk_repository import (
    RegulationChunkRepository,
)
from app.repositories.regulation_repository import RegulationRepository
from app.services.regulation_index_document_builder import RegulationIndexDocumentBuilder
from app.unit.date import utc_now

settings = get_settings()


class RegulationIndexService(RegulationIndexDocumentBuilder):
    """将法规结构化 Chunk 向量化并同步到 Elasticsearch。"""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        regulation_repository: RegulationRepository,
        chunk_repository: RegulationChunkRepository,
        embedding: EmbeddingService,
        vector_store: RegulationVectorStore,
    ) -> None:
        self.uow = uow
        self.regulation_repository = regulation_repository
        self.chunk_repository = chunk_repository
        self.embedding = embedding
        self.vector_store = vector_store

    async def index(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
    ) -> Regulation:
        """抢占任务、生成向量、替换 ES 副本并更新状态。"""

        # started_at 同时作为本次任务的 fencing token。超时任务即使后来
        # 恢复，也不能覆盖重新抢占任务已经写入的数据库状态。
        started_at = utc_now()
        stale_before = started_at - timedelta(seconds=settings.REGULATION_INDEX_STALE_SECONDS)

        # 第一段短事务只负责抢占任务。
        async with self.uow:
            regulation = await self.regulation_repository.claim_for_index(
                regulation_id=regulation_id,
                user_id=user_id,
                started_at=started_at,
                stale_before=stale_before,
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

                # 重复请求已经完成时，直接返回当前结果。
                if (
                    existing.enabled
                    and existing.status == RegulationStatus.READY
                    and existing.chunk_status == RegulationChunkStatus.READY
                    and existing.index_status == RegulationIndexStatus.READY
                ):
                    return existing

                raise BusinessException(
                    REGULATION_STATUS_INVALID,
                    (
                        "regulation cannot be indexed in status "
                        f"{existing.status.value}/"
                        f"{existing.chunk_status.value}/"
                        f"{existing.index_status.value}"
                    ),
                )
        expected_lock_version = regulation.lock_version

        try:
            # 读取 Chunk 后立即结束数据库事务，Embedding 请求期间
            # 不占用数据库连接。
            async with self.uow:
                chunks = await self.chunk_repository.find_by_regulation(regulation_id)

            if not chunks:
                raise RuntimeError("regulation does not contain knowledge chunks")

            index_chunks = await self._build_index_documents(
                embedding=self.embedding,
                regulation=regulation,
                chunks=chunks,
            )

            # Embedding 可能耗时很久。写 ES 前再次校验 fencing，避免已经被
            # stale takeover 的旧执行者覆盖新任务生成的查询副本。
            await self._assert_claim_owned(
                regulation_id=regulation_id,
                user_id=user_id,
                expected_started_at=started_at,
                expected_lock_version=expected_lock_version,
            )
            await self.vector_store.replace_regulation_chunks(
                regulation_id=str(regulation_id),
                chunks=index_chunks,
            )

        except Exception as exc:
            await self._mark_failed(
                regulation_id=regulation_id,
                user_id=user_id,
                expected_started_at=started_at,
                expected_lock_version=expected_lock_version,
            )
            log_regulation_failure(
                "regulation.index.build_failed", regulation_id=regulation_id, error=exc
            )
            raise

        # ES 成功后用短事务更新数据库状态。
        try:
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
                    locked.index_status != RegulationIndexStatus.PROCESSING
                    or locked.index_started_at != started_at
                    or locked.lock_version != expected_lock_version
                ):
                    raise BusinessException(
                        REGULATION_STATUS_INVALID,
                        "regulation index state has changed",
                    )

                locked.index_status = RegulationIndexStatus.READY
                locked.index_error = None
                locked.index_completed_at = utc_now()

        except Exception as exc:
            # ES 已成功但数据库提交失败时只记录日志。
            # ES 是可从 PostgreSQL 重建的查询副本。
            log_regulation_failure(
                "regulation.index.database_finalize_failed",
                regulation_id=regulation_id,
                error=exc,
            )
            raise

        return locked

    async def _assert_claim_owned(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
        expected_started_at: datetime,
        expected_lock_version: int,
    ) -> None:
        """在不可事务化的 ES 写入前执行一次短事务 fencing 校验。"""
        async with self.uow:
            regulation = await self.regulation_repository.find_by_id_and_user_for_update(
                regulation_id=regulation_id,
                user_id=user_id,
            )
            if (
                regulation is None
                or regulation.index_status != RegulationIndexStatus.PROCESSING
                or regulation.index_started_at != expected_started_at
                or regulation.lock_version != expected_lock_version
            ):
                raise BusinessException(
                    REGULATION_STATUS_INVALID,
                    "regulation index execution was superseded",
                )

    async def _mark_failed(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
        expected_started_at: datetime,
        expected_lock_version: int,
    ) -> None:
        """仅允许当前 fencing token 对应的任务写入失败状态。"""

        async with self.uow:
            regulation = await self.regulation_repository.find_by_id_and_user_for_update(
                regulation_id=regulation_id,
                user_id=user_id,
            )

            if (
                regulation is not None
                and regulation.index_status == RegulationIndexStatus.PROCESSING
                and regulation.index_started_at == expected_started_at
                and regulation.lock_version == expected_lock_version
            ):
                regulation.index_status = RegulationIndexStatus.FAILED
                regulation.index_error = REGULATION_FAILURE_CODES["index"]
                regulation.index_completed_at = utc_now()


def get_regulation_index_service(
    uow: UnitOfWork = Depends(get_uow),
    embedding: EmbeddingService = Depends(get_embedding_service),
) -> RegulationIndexService:
    return RegulationIndexService(
        uow=uow,
        regulation_repository=RegulationRepository(uow.session),
        chunk_repository=RegulationChunkRepository(uow.session),
        embedding=embedding,
        vector_store=regulation_vector_store,
    )
