from datetime import timedelta
from uuid import UUID

from app.core.config import get_settings
from app.core.document_failure import DOCUMENT_FAILURE_CODES, log_document_failure
from app.core.error_codes import DOCUMENT_NOT_FOUND, DOCUMENT_STATUS_INVALID
from app.core.exceptions import BusinessException
from app.core.logger import logger
from app.infrastructure.db.unit_of_work import UnitOfWork
from app.models.document import Document, DocumentStatus
from app.repositories.document_page_repository import DocumentPageRepository
from app.repositories.document_parse_block_repository import DocumentParseBlockRepository
from app.repositories.document_repository import DocumentRepository
from app.services.document_storage_service import DocumentStorageService
from app.services.markdown_document_parse_builder import MarkdownDocumentParseBuilder
from app.unit.date import utc_now

settings = get_settings()


class MarkdownDocumentParseService:
    """在后台把 MinIO 中的 UTF-8 Markdown 转换为块和逻辑审计单元。"""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        repository: DocumentRepository,
        storage: DocumentStorageService,
        page_repository: DocumentPageRepository,
        parse_block_repository: DocumentParseBlockRepository,
    ) -> None:
        self.uow = uow
        self.repository = repository
        self.storage = storage
        self.page_repository = page_repository
        self.parse_block_repository = parse_block_repository

    async def parse(self, *, document_id: UUID, user_id: UUID) -> Document:
        """原子领取后在事务外读 MinIO，最后一次性提交完整解析结果。"""
        logger.info(
            "document.markdown_parse_started",
            document_id=str(document_id),
            stage="PARSING",
        )
        async with self.uow:
            document = await self.repository.claim_for_parse(
                document_id=document_id,
                user_id=user_id,
                started_at=utc_now(),
                stale_before=utc_now()
                - timedelta(seconds=settings.DOCUMENT_PARSE_STALE_SECONDS),
            )
            if document is None:
                existing = await self.repository.find_by_id_and_user(document_id, user_id)
                if existing is None:
                    raise BusinessException(DOCUMENT_NOT_FOUND, "document not found")
                if existing.status == DocumentStatus.READY:
                    return existing
                raise BusinessException(
                    DOCUMENT_STATUS_INVALID,
                    f"document cannot be parsed in status {existing.status.value}",
                )
        expected_lock_version = document.lock_version

        try:
            payload = bytearray()
            async for chunk in self.storage.stream(document.storage_key):
                payload.extend(chunk)
            source = bytes(payload).decode("utf-8", errors="strict")
            parse_output = MarkdownDocumentParseBuilder.build(
                document_id=document.id,
                source=source,
            )
            if not parse_output.pages:
                raise RuntimeError("Markdown has no auditable content")
        except Exception as exc:
            log_document_failure(
                "document.markdown_parse_failed",
                document_id=document_id,
                error=exc,
            )
            return await self._mark_failed(
                document_id=document_id,
                user_id=user_id,
                expected_lock_version=expected_lock_version,
            )

        async with self.uow:
            locked = await self.repository.find_by_id_and_user_for_update(document_id, user_id)
            if locked is None:
                raise BusinessException(DOCUMENT_NOT_FOUND, "document not found")
            if (
                locked.status != DocumentStatus.PARSING
                or locked.lock_version != expected_lock_version
            ):
                return locked
            await self.page_repository.replace_by_document(
                document_id=document_id,
                pages=parse_output.pages,
            )
            await self.parse_block_repository.replace_by_document(
                document_id=document_id,
                blocks=parse_output.blocks,
            )
            locked.status = DocumentStatus.READY
            locked.parse_error = None
            locked.parse_completed_at = utc_now()
            logger.info(
                "document.markdown_parse_completed",
                document_id=str(document_id),
                stage="PARSING",
                block_count=len(parse_output.blocks),
                audit_unit_count=len(parse_output.pages),
            )
            return locked

    async def _mark_failed(
        self,
        *,
        document_id: UUID,
        user_id: UUID,
        expected_lock_version: int,
    ) -> Document:
        async with self.uow:
            document = await self.repository.find_by_id_and_user_for_update(document_id, user_id)
            if document is None:
                raise BusinessException(DOCUMENT_NOT_FOUND, "document not found")
            if (
                document.status == DocumentStatus.PARSING
                and document.lock_version == expected_lock_version
            ):
                document.status = DocumentStatus.FAILED
                document.parse_error = DOCUMENT_FAILURE_CODES["parse"]
                document.parse_completed_at = utc_now()
            return document
