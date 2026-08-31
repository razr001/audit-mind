from datetime import timedelta
from uuid import UUID

import aiohttp
from fastapi import Depends

from app.core.config import get_settings
from app.core.document_failure import (
    DOCUMENT_FAILURE_CODES,
    log_document_failure,
)
from app.core.error_codes import (
    DOCUMENT_NOT_FOUND,
    DOCUMENT_STATUS_INVALID,
)
from app.core.exceptions import BusinessException
from app.core.logger import logger
from app.infrastructure.db.unit_of_work import UnitOfWork, get_uow
from app.infrastructure.mineru_client import (
    MinerUClient,
    MinerUTransientError,
    mineru_client,
)
from app.infrastructure.redis_lock import acquire_redis_lease
from app.models.document import Document, DocumentStatus
from app.repositories.document_page_repository import (
    DocumentPageRepository,
    get_document_page_repository,
)
from app.repositories.document_parse_block_repository import (
    DocumentParseBlockRepository,
    get_document_parse_block_repository,
)
from app.repositories.document_repository import (
    DocumentRepository,
    get_document_repository,
)
from app.services.document_parse_builder import DocumentParseBuilder
from app.services.document_storage_service import (
    DocumentStorageService,
    get_document_storage,
)
from app.unit.date import utc_now

settings = get_settings()


class DocumentParseService(DocumentParseBuilder):
    """编排文档从 MinIO 流式提交 MinerU，以及解析结果的幂等落库。"""

    def __init__(
        self,
        uow: UnitOfWork,
        repository: DocumentRepository,
        storage: DocumentStorageService,
        mineru: MinerUClient,
        page_repository: DocumentPageRepository,
        parse_block_repository: DocumentParseBlockRepository,
    ) -> None:
        self.uow = uow
        self.repository = repository
        self.storage = storage
        self.mineru = mineru
        self.page_repository = page_repository
        self.parse_block_repository = parse_block_repository

    async def start_parse(
        self,
        *,
        document_id: UUID,
        user_id: UUID,
    ) -> Document:
        """抢占解析权并创建 MinerU 任务，不在网络请求期间持有事务。"""
        async with self.uow:
            # 保证原子性
            document = await self.repository.claim_for_parse(
                document_id=document_id,
                user_id=user_id,
                started_at=utc_now(),
                stale_before=utc_now()
                - timedelta(seconds=settings.DOCUMENT_PARSE_STALE_SECONDS),
            )

            if document is None:
                existing = await self.repository.find_by_id_and_user(
                    document_id=document_id,
                    user_id=user_id,
                )

                if existing is None:
                    raise BusinessException(
                        DOCUMENT_NOT_FOUND,
                        "document not found",
                    )

                raise BusinessException(
                    DOCUMENT_STATUS_INVALID,
                    (f"document cannot be parsed in status {existing.status.value}"),
                )
        expected_lock_version = document.lock_version

        try:
            content_stream = self.storage.stream(
                document.storage_key,
            )
            # 创建MinerU task，这是一个异步任务，需要轮询sync_parse_result查询最终结果
            task_id = await self.mineru.create_task(
                filename=document.original_filename,
                content=content_stream,
                content_type=document.content_type,
                content_length=document.file_size,
                backend=settings.MINERU_BACKEND,
                server_url=settings.MINERU_SERVER_URL,
                effort=settings.MINERU_EFFORT,
                parse_method=settings.MINERU_PARSE_METHOD,
                formula_enable=settings.MINERU_FORMULA_ENABLE,
                table_enable=settings.MINERU_TABLE_ENABLE,
                image_analysis=settings.MINERU_IMAGE_ANALYSIS,
            )
        except Exception as exc:
            # 创建解释任务失败
            log_document_failure(
                "document_mineru_task_creation_failed",
                document_id=document_id,
                error=exc,
            )
            failed = await self._mark_parse_failed(
                document_id=document_id,
                user_id=user_id,
                expected_lock_version=expected_lock_version,
            )
            return failed or document

        try:
            async with self.uow:
                locked = await self.repository.find_by_id_and_user_for_update(
                    document_id=document_id,
                    user_id=user_id,
                )
                if (
                    locked is None
                    or locked.status != DocumentStatus.PARSING
                    or locked.lock_version != expected_lock_version
                ):
                    raise BusinessException(
                        DOCUMENT_STATUS_INVALID,
                        "document parse execution was superseded",
                    )
                # 只有领取解析权的版本可以登记它创建的 MinerU 任务。
                locked.parse_task_id = task_id
            return locked
        except BusinessException:
            raise
        except Exception:
            await self._mark_parse_failed(
                document_id=document_id,
                user_id=user_id,
                expected_lock_version=expected_lock_version,
            )
            raise

    async def sync_parse_result(
        self,
        *,
        document_id: UUID,
        user_id: UUID,
    ) -> Document:
        """串行化同一文档的结果同步，避免失败请求覆盖成功结果。"""
        lock_key = f"lock:document:parse-sync:{document_id}"
        async with acquire_redis_lease(
            key=lock_key,
            ttl_seconds=settings.DOCUMENT_PARSE_SYNC_LOCK_TTL_SECONDS,
        ) as acquired:
            if not acquired:
                async with self.uow:
                    document = await self.repository.find_by_id_and_user(
                        document_id=document_id,
                        user_id=user_id,
                    )
                    if document is None:
                        raise BusinessException(
                            DOCUMENT_NOT_FOUND,
                            "document not found",
                        )
                    return document

            return await self._sync_parse_result(
                document_id=document_id,
                user_id=user_id,
            )

    async def _sync_parse_result(
        self,
        *,
        document_id: UUID,
        user_id: UUID,
    ) -> Document:
        """轮询 MinerU 状态，并在完成后原子替换 Page、ParseBlock 和文档状态。"""
        async with self.uow:
            document = await self.repository.find_by_id_and_user_for_update(
                document_id=document_id,
                user_id=user_id,
            )

            if document is None:
                raise BusinessException(
                    DOCUMENT_NOT_FOUND,
                    "document not found",
                )

            if document.status == DocumentStatus.READY:
                return document

            if document.status != DocumentStatus.PARSING or not document.parse_task_id:
                raise BusinessException(
                    DOCUMENT_STATUS_INVALID,
                    "document is not parsing",
                )

            # 数据库版本是 Redis 租约的 fencing token；旧请求只有版本仍匹配
            # 时才能提交 FAILED 或 READY。
            document.lock_version += 1
            sync_lock_version = document.lock_version

        task_id = document.parse_task_id
        try:
            task = await self.mineru.get_task(task_id)
        except (TimeoutError, aiohttp.ClientError, MinerUTransientError) as exc:
            # 查询超时、连接重置或临时网关错误不代表 MinerU 任务失败。
            # 保持 PARSING，下一轮轮询继续使用相同 task_id，避免重新上传。
            logger.warning(
                "document_mineru_task_query_transient_failure",
                document_id=str(document_id),
                error_type=type(exc).__name__,
            )
            return document
        except Exception as exc:
            log_document_failure(
                "document_mineru_task_query_failed",
                document_id=document_id,
                error=exc,
            )
            failed_document = await self._mark_parse_failed(
                document_id=document_id,
                user_id=user_id,
                task_id=task_id,
                expected_lock_version=sync_lock_version,
            )
            return failed_document or document

        mineru_status = task.get("status")
        if mineru_status in {"pending", "processing"}:
            return document

        if mineru_status == "failed":
            log_document_failure(
                "document_mineru_task_failed",
                document_id=document_id,
                error="ExternalFailure",
            )
            failed_document = await self._mark_parse_failed(
                document_id=document_id,
                user_id=user_id,
                task_id=task_id,
                expected_lock_version=sync_lock_version,
            )
            return failed_document or document

        if mineru_status != "completed":
            log_document_failure(
                "document_mineru_unknown_task_status",
                document_id=document_id,
                error="ProtocolFailure",
            )
            failed_document = await self._mark_parse_failed(
                document_id=document_id,
                user_id=user_id,
                task_id=task_id,
                expected_lock_version=sync_lock_version,
            )
            return failed_document or document

        try:
            result = await self.mineru.get_task_result(task_id)
            parse_output = self.build(
                document_id=document.id,
                result=result,
            )

            if not parse_output.pages:
                raise RuntimeError("MinerU completed without parseable page content")
        except (TimeoutError, aiohttp.ClientError, MinerUTransientError) as exc:
            logger.warning(
                "document_mineru_result_download_transient_failure",
                document_id=str(document_id),
                error_type=type(exc).__name__,
            )
            return document
        except Exception as exc:
            log_document_failure(
                "document_mineru_result_processing_failed",
                document_id=document_id,
                error=exc,
            )
            failed_document = await self._mark_parse_failed(
                document_id=document_id,
                user_id=user_id,
                task_id=task_id,
                expected_lock_version=sync_lock_version,
            )
            return failed_document or document

        async with self.uow:
            # 行锁保证document只有一个线程可以修改
            locked_document = await self.repository.find_by_id_and_user_for_update(
                document_id=document_id,
                user_id=user_id,
            )

            if locked_document is None:
                raise BusinessException(
                    DOCUMENT_NOT_FOUND,
                    "document not found",
                )

            if locked_document.status == DocumentStatus.READY:
                return locked_document

            if (
                locked_document.status != DocumentStatus.PARSING
                or locked_document.parse_task_id != task_id
                or locked_document.lock_version != sync_lock_version
            ):
                return locked_document

            await self.page_repository.replace_by_document(
                document_id=document_id,
                pages=parse_output.pages,
            )

            await self.parse_block_repository.replace_by_document(
                document_id=document_id,
                blocks=parse_output.blocks,
            )

            locked_document.status = DocumentStatus.READY
            locked_document.parse_error = None
            locked_document.parse_completed_at = utc_now()

        return locked_document

    async def _mark_parse_failed(
        self,
        *,
        document_id: UUID,
        user_id: UUID,
        task_id: str | None = None,
        expected_lock_version: int | None = None,
    ) -> Document | None:
        async with self.uow:
            document = await self.repository.find_by_id_and_user_for_update(
                document_id=document_id,
                user_id=user_id,
            )
            if document is None or document.status != DocumentStatus.PARSING:
                return document
            if task_id is not None and document.parse_task_id != task_id:
                return document
            if expected_lock_version is not None and document.lock_version != expected_lock_version:
                return document

            document.status = DocumentStatus.FAILED
            document.parse_error = DOCUMENT_FAILURE_CODES["parse"]
            document.parse_completed_at = utc_now()
            return document


def get_document_parse_service(
    uow: UnitOfWork = Depends(get_uow),
    repository: DocumentRepository = Depends(
        get_document_repository,
    ),
    storage: DocumentStorageService = Depends(
        get_document_storage,
    ),
    page_repository: DocumentPageRepository = Depends(
        get_document_page_repository,
    ),
    parse_block_repository: DocumentParseBlockRepository = Depends(
        get_document_parse_block_repository,
    ),
) -> DocumentParseService:
    return DocumentParseService(
        uow=uow,
        repository=repository,
        storage=storage,
        mineru=mineru_client,
        page_repository=page_repository,
        parse_block_repository=parse_block_repository,
    )
