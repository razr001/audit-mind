from uuid import UUID

from fastapi import Depends, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.error_codes import DOCUMENT_NOT_FOUND, FILE_REQUIRED, FILE_TOO_LARGE
from app.core.exceptions import BusinessException
from app.core.text_validation import contains_control_character
from app.core.upload_file_validation import (
    get_supported_file_type,
    validate_file_content,
)
from app.infrastructure.db.session import get_db
from app.infrastructure.db.unit_of_work import UnitOfWork, get_uow
from app.models.document import Document, DocumentSourceType
from app.repositories.document_repository import (
    DocumentRepository,
    DocumentSortField,
    SortOrder,
    get_document_repository,
)
from app.services.document_storage_service import DocumentStorageService, get_document_storage
from app.services.markdown_document_parse_builder import MarkdownDocumentParseBuilder


class DocumentService:
    """编排用户文档上传、查询和下载，不直接实现对象存储细节。"""

    def __init__(
        self,
        session: AsyncSession,
        uow: UnitOfWork,
        repository: DocumentRepository,
        storage: DocumentStorageService,
    ):

        self.session = session
        self.repository = repository
        self.uow = uow
        self.storage = storage

    async def upload_document(self, file: UploadFile, user_id: UUID) -> Document:
        """校验文件、写入 MinIO，再在数据库创建文档记录。"""
        document = await self.prepare_uploaded_document(file=file, user_id=user_id)
        # 上传后的对象不做异常补偿删除。数据库提交结果可能不明确，保留对象
        # 比误删已经被成功提交的数据更安全；孤立对象由运维按需清理。
        async with self.uow:
            return await self.repository.save(document)

    async def prepare_uploaded_document(
        self,
        *,
        file: UploadFile,
        user_id: UUID,
    ) -> Document:
        """校验并上传文件，但不写数据库，供上层组合原子事务。"""
        if not file.filename:
            raise BusinessException(FILE_REQUIRED, "filename is required")
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
        file_size = await self._validate_file_size(
            file,
            suffix=suffix,
        )
        storage_key = await self.storage.upload(
            file=file,
            file_size=file_size,
            content_type=content_type,
        )
        document = Document(
            user_id=user_id,
            original_filename=file.filename,
            storage_key=storage_key,
            content_type=content_type,
            file_size=file_size,
        )

        return document

    async def create_markdown_document(
        self,
        *,
        title: str,
        content: str,
        user_id: UUID,
    ) -> Document:
        """规范化 Markdown 原文并保存；普通纯文本天然是合法 Markdown。"""
        document = await self.prepare_markdown_document(
            title=title,
            content=content,
            user_id=user_id,
        )
        async with self.uow:
            return await self.repository.save(document)

    async def prepare_markdown_document(
        self,
        *,
        title: str,
        content: str,
        user_id: UUID,
    ) -> Document:
        """上传 Markdown 原文但暂不落库，供审计任务与文档一起提交。"""
        normalized_title = title.strip()
        if not normalized_title:
            raise BusinessException(FILE_REQUIRED, "title is required")
        if len(normalized_title) > 252:
            raise BusinessException(FILE_REQUIRED, "title must not exceed 252 characters")
        if contains_control_character(normalized_title):
            raise BusinessException(FILE_REQUIRED, "title must not contain control characters")

        normalized_content = MarkdownDocumentParseBuilder.normalize_source(content)
        if not normalized_content.strip():
            raise BusinessException(FILE_REQUIRED, "markdown content is required")
        encoded_content = normalized_content.encode("utf-8")
        if len(encoded_content) > get_settings().AUDIT_MARKDOWN_MAX_BYTES:
            raise BusinessException(FILE_TOO_LARGE, "markdown content is too large")

        filename = (
            normalized_title
            if normalized_title.lower().endswith(".md")
            else f"{normalized_title}.md"
        )
        storage_key = await self.storage.upload_bytes(
            content=encoded_content,
            suffix=".md",
            content_type="text/markdown; charset=utf-8",
        )
        document = Document(
            user_id=user_id,
            original_filename=filename,
            storage_key=storage_key,
            content_type="text/markdown; charset=utf-8",
            source_type=DocumentSourceType.MARKDOWN,
            file_size=len(encoded_content),
        )
        return document

    @staticmethod
    async def _validate_file_size(
        file: UploadFile,
        *,
        suffix: str,
    ) -> int:
        """单次顺序扫描同时检查大小并保留首块用于内容特征校验。"""
        settings = get_settings()
        total_size = 0
        first_chunk: bytes | None = None
        await file.seek(0)
        try:
            while chunk := await file.read(1024 * 1024):
                if first_chunk is None:
                    first_chunk = chunk
                total_size += len(chunk)
                if total_size > settings.DOCUMENT_MAX_FILE_SIZE:
                    raise BusinessException(
                        FILE_TOO_LARGE,
                        "document file is too large",
                    )
        finally:
            # 后续内容校验和 MinIO 上传都需要从文件开头重新读取。
            await file.seek(0)

        if total_size == 0 or first_chunk is None:
            raise BusinessException(FILE_REQUIRED, "document file is empty")

        await validate_file_content(
            suffix=suffix,
            first_chunk=first_chunk,
            file=file,
        )
        await file.seek(0)
        return total_size

    async def get_document(self, document_id: UUID, user_id: UUID):
        """获取当前用户自己的文档，不向调用方泄露其他用户的数据。"""
        document = await self.repository.find_by_id_and_user(document_id, user_id=user_id)
        if document is None:
            raise BusinessException(
                DOCUMENT_NOT_FOUND,
                "document not found",
            )
        return document

    async def get_document_list(
        self,
        user_id: UUID,
        offset: int,
        limit: int,
        *,
        sort_by: DocumentSortField = "createdAt",
        sort_order: SortOrder = "desc",
    ) -> tuple[list[Document], int]:
        return await self.repository.find_page_by_user(
            user_id=user_id,
            offset=offset,
            limit=limit,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    async def create_download_url(
        self,
        document_id: UUID,
        user_id: UUID,
    ) -> tuple[str, int]:
        """鉴权后生成短期有效的对象存储下载地址。"""
        document = await self.get_document(
            document_id=document_id,
            user_id=user_id,
        )

        expires_in = 1800

        url = await self.storage.create_download_url(
            object_name=document.storage_key,
            expires_in=expires_in,
        )

        return url, expires_in


def get_document_service(
    session: AsyncSession = Depends(get_db),
    uow: UnitOfWork = Depends(get_uow),
    repository: DocumentRepository = Depends(get_document_repository),
    storage: DocumentStorageService = Depends(get_document_storage),
):
    return DocumentService(session, uow, repository, storage)
