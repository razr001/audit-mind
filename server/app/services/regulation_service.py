import asyncio
import hashlib
from typing import BinaryIO
from uuid import UUID

from fastapi import Depends, UploadFile
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.error_codes import (
    FILE_REQUIRED,
    FILE_TOO_LARGE,
    REGULATION_ALREADY_EXISTS,
)
from app.core.exceptions import BusinessException
from app.core.text_validation import contains_control_character
from app.core.upload_file_validation import (
    get_supported_file_type,
    validate_file_content,
)
from app.infrastructure.db.unit_of_work import (
    UnitOfWork,
    get_uow,
)
from app.models.regulation import (
    KnowledgeCategory,
    Regulation,
    RegulationSourceType,
    get_knowledge_category,
)
from app.repositories.operation_log_repository import OperationLogRepository
from app.repositories.regulation_repository import (
    RegulationRepository,
)
from app.schemas.regulation import RegulationUploadForm
from app.services.operation_audit_service import OperationAuditService
from app.services.regulation_storage_service import (
    RegulationStorageService,
)

settings = get_settings()
HASH_CHUNK_SIZE = 1024 * 1024


def _calculate_stream_hash(stream: BinaryIO) -> tuple[str, int, bytes | None]:
    """在线程池内扫描上传流，避免大文件哈希阻塞 FastAPI 事件循环。"""

    digest = hashlib.sha256()
    total_size = 0
    first_chunk: bytes | None = None
    while chunk := stream.read(HASH_CHUNK_SIZE):
        if first_chunk is None:
            first_chunk = chunk
        total_size += len(chunk)
        if total_size > settings.REGULATION_MAX_FILE_SIZE:
            raise BusinessException(
                FILE_TOO_LARGE,
                f"regulation file exceeds {settings.REGULATION_MAX_FILE_SIZE} bytes",
            )
        digest.update(chunk)
    return digest.hexdigest(), total_size, first_chunk

class RegulationService:
    """处理知识源上传、内容去重和访问范围查询。"""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        repository: RegulationRepository,
        storage: RegulationStorageService,
        operation_audit: OperationAuditService | None = None,
    ) -> None:
        self.uow = uow
        self.repository = repository
        self.storage = storage
        self.operation_audit = operation_audit

    async def upload(
        self,
        *,
        file: UploadFile,
        form: RegulationUploadForm,
        user_id: UUID,
        request_id: str | None = None,
    ) -> Regulation:
        """校验并计算内容哈希，上传 MinIO 后创建法规数据库记录。"""
        if not file.filename:
            raise BusinessException(
                FILE_REQUIRED,
                "filename is required",
            )

        if len(file.filename) > 255:
            raise BusinessException(
                FILE_REQUIRED,
                "filename must not exceed 255 characters",
            )
        if contains_control_character(file.filename):
            raise BusinessException(
                FILE_REQUIRED,
                "filename must not contain control characters",
            )

        suffix, content_type = get_supported_file_type(file.filename)

        content_hash, file_size = await self._calculate_hash_and_size(file=file, suffix=suffix)

        # 先做友好的业务层去重，避免正常重复上传直接暴露数据库异常。
        async with self.uow:
            existing = await self.repository.find_duplicate_by_content_hash(
                content_hash=content_hash,
                visibility=form.visibility,
                user_id=user_id,
            )

            if existing is not None:
                raise BusinessException(
                    REGULATION_ALREADY_EXISTS,
                    "regulation file already exists",
                )

        storage_key = await self.storage.upload(
            file=file,
            file_size=file_size,
            content_type=content_type,
        )

        regulation = Regulation(
            title=form.title.strip(),
            source_type=form.source_type,
            category=get_knowledge_category(form.source_type),
            visibility=form.visibility,
            language=form.language.strip(),
            document_number=self._clean_optional(form.document_number),
            authority=self._clean_optional(form.authority),
            jurisdiction=form.jurisdiction.strip(),
            effective_date=form.effective_date,
            expiration_date=form.expiration_date,
            version=self._clean_optional(form.version),
            source_url=(str(form.source_url) if form.source_url is not None else None),
            storage_key=storage_key,
            original_filename=file.filename,
            content_type=content_type,
            file_size=file_size,
            content_hash=content_hash,
            uploaded_by=user_id,
        )

        # 并发请求可能同时通过上面的查询，因此仍捕获最终写入阶段的
        # IntegrityError。事务异常不删除已上传对象，避免提交结果不明确时误删。
        try:
            async with self.uow:
                await self.repository.save(regulation)
                if self.operation_audit is not None:
                    await self.operation_audit.record_regulation_created(
                        regulation=regulation,
                        user_id=user_id,
                        request_id=request_id,
                        operation_type="REGULATION_CREATED",
                    )
            return regulation
        except IntegrityError as exc:
            raise BusinessException(
                REGULATION_ALREADY_EXISTS,
                "regulation file already exists",
            ) from exc

    async def get_accessible_page(
        self,
        *,
        user_id: UUID,
        offset: int,
        limit: int,
        category: KnowledgeCategory | None = None,
        source_type: RegulationSourceType | None = None,
    ) -> tuple[list[Regulation], int]:
        return await self.repository.find_accessible_page(
            user_id=user_id,
            offset=offset,
            limit=limit,
            category=category,
            source_type=source_type,
        )

    async def get_uploaded_page(
        self,
        *,
        user_id: UUID,
        offset: int,
        limit: int,
        category: KnowledgeCategory | None = None,
    ) -> tuple[list[Regulation], int]:
        """返回上传者管理列表，使前端能够展示解析和知识化失败原因。"""
        return await self.repository.find_uploaded_page(
            user_id=user_id,
            offset=offset,
            limit=limit,
            category=category,
        )

    async def _calculate_hash_and_size(
        self,
        *,
        file: UploadFile,
        suffix: str,
    ) -> tuple[str, int]:
        """一次流式扫描完成 SHA-256、大小限制及内容真实性校验。"""
        await file.seek(0)
        try:
            content_hash, total_size, first_chunk = await asyncio.to_thread(
                _calculate_stream_hash,
                file.file,
            )
        finally:
            await file.seek(0)

        if total_size == 0 or first_chunk is None:
            raise BusinessException(
                FILE_REQUIRED,
                "regulation file is empty",
            )

        await validate_file_content(
            suffix=suffix,
            first_chunk=first_chunk,
            file=file,
        )
        await file.seek(0)

        return content_hash, total_size

    @staticmethod
    def _clean_optional(
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        cleaned = value.strip()
        return cleaned or None


def get_regulation_service(
    uow: UnitOfWork = Depends(get_uow),
) -> RegulationService:
    return RegulationService(
        uow=uow,
        repository=RegulationRepository(uow.session),
        storage=RegulationStorageService(),
        operation_audit=OperationAuditService(
            repository=OperationLogRepository(uow.session),
        ),
    )
