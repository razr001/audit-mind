import asyncio
import hashlib
import re
from uuid import UUID, uuid4

from fastapi import Depends
from sqlalchemy.exc import IntegrityError

from app.ai.agent.services.agent_tool_fence import require_running_agent_tool_call
from app.core.config import get_settings
from app.core.error_codes import (
    FILE_REQUIRED,
    FILE_TOO_LARGE,
    REGULATION_ALREADY_EXISTS,
)
from app.core.exceptions import BusinessException
from app.core.language_detection import (
    default_language_for_jurisdiction,
    detect_content_language,
)
from app.core.text_validation import is_safe_readable_text
from app.infrastructure.db.unit_of_work import UnitOfWork, get_uow
from app.models.regulation import Regulation, RegulationStatus, get_knowledge_category
from app.models.regulation_parse_block import RegulationParseBlock
from app.repositories.operation_log_repository import OperationLogRepository
from app.repositories.regulation_parse_block_repository import RegulationParseBlockRepository
from app.repositories.regulation_repository import RegulationRepository
from app.schemas.regulation import RegulationTextCreateRequest
from app.services.operation_audit_service import OperationAuditService
from app.services.regulation_storage_service import RegulationStorageService
from app.unit.date import utc_now

settings = get_settings()


class RegulationTextService:
    """把用户录入的 Markdown 原文转换成统一法规流水线的输入。"""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        repository: RegulationRepository,
        parse_block_repository: RegulationParseBlockRepository,
        storage: RegulationStorageService,
        operation_audit: OperationAuditService,
    ) -> None:
        self.uow = uow
        self.repository = repository
        self.parse_block_repository = parse_block_repository
        self.storage = storage
        self.operation_audit = operation_audit

    async def create(
        self,
        *,
        request: RegulationTextCreateRequest,
        user_id: UUID,
        request_id: str | None = None,
        agent_tool_call_id: UUID | None = None,
    ) -> Regulation:
        """保存原文和单一 ParseBlock；后续分块仍由现有知识服务负责。"""
        content = request.content
        if not is_safe_readable_text(content):
            raise BusinessException(
                FILE_REQUIRED,
                "knowledge content contains unsafe control characters or no visible text",
            )
        if len(content) > settings.REGULATION_MAX_TEXT_LENGTH:
            raise BusinessException(
                FILE_TOO_LARGE,
                f"knowledge content exceeds {settings.REGULATION_MAX_TEXT_LENGTH} characters",
            )

        encoded = content.encode("utf-8")
        content_hash = hashlib.sha256(encoded).hexdigest()
        async with self.uow:
            existing = await self.repository.find_duplicate_by_content_hash(
                content_hash=content_hash,
                visibility=request.visibility,
                user_id=user_id,
            )
            if existing is not None:
                raise BusinessException(
                    REGULATION_ALREADY_EXISTS,
                    "regulation content already exists",
                )

        storage_key = await self.storage.upload_text(data=encoded)
        language = request.language
        if language.lower() == "auto":
            language = await asyncio.to_thread(
                detect_content_language,
                [content],
                fallback=default_language_for_jurisdiction(request.jurisdiction),
            )
        regulation, block = self._build_models(
            request=request,
            language=language,
            user_id=user_id,
            storage_key=storage_key,
            content_hash=content_hash,
            file_size=len(encoded),
            agent_tool_call_id=agent_tool_call_id,
        )
        try:
            async with self.uow:
                if agent_tool_call_id is not None:
                    await require_running_agent_tool_call(
                        self.uow.session,
                        agent_tool_call_id,
                    )
                await self.repository.save(regulation)
                await self.parse_block_repository.replace_by_regulation(
                    regulation_id=regulation.id,
                    blocks=[block],
                )
                await self.operation_audit.record_regulation_created(
                    regulation=regulation,
                    user_id=user_id,
                    request_id=request_id,
                    operation_type="REGULATION_TEXT_CREATED",
                )
            return regulation
        except IntegrityError as exc:
            raise BusinessException(
                REGULATION_ALREADY_EXISTS,
                "regulation content already exists",
            ) from exc

    @staticmethod
    def _build_models(
        *,
        request: RegulationTextCreateRequest,
        language: str,
        user_id: UUID,
        storage_key: str,
        content_hash: str,
        file_size: int,
        agent_tool_call_id: UUID | None,
    ) -> tuple[Regulation, RegulationParseBlock]:
        now = utc_now()
        regulation_id = uuid4()
        safe_title = re.sub(r'[<>:"/\\|?*]+', "_", request.title).strip(" .")
        regulation = Regulation(
            id=regulation_id,
            agent_tool_call_id=agent_tool_call_id,
            title=request.title,
            source_type=request.source_type,
            category=get_knowledge_category(request.source_type),
            visibility=request.visibility,
            language=language,
            document_number=request.document_number,
            authority=request.authority,
            jurisdiction=request.jurisdiction,
            effective_date=request.effective_date,
            expiration_date=request.expiration_date,
            version=request.version,
            source_url=(str(request.source_url) if request.source_url is not None else None),
            storage_key=storage_key,
            original_filename=f"{safe_title[:246] or 'knowledge'}.md",
            content_type="text/markdown; charset=utf-8",
            file_size=file_size,
            content_hash=content_hash,
            uploaded_by=user_id,
            status=RegulationStatus.READY,
            parse_started_at=now,
            parse_completed_at=now,
        )
        block = RegulationParseBlock(
            regulation_id=regulation_id,
            block_index=0,
            block_type="text",
            content=request.content,
            page_number=None,
            bbox=None,
            text_level=None,
            char_start=0,
            char_end=len(request.content),
            block_metadata={"sourceFormat": "markdown"},
        )
        return regulation, block

def get_regulation_text_service(
    uow: UnitOfWork = Depends(get_uow),
) -> RegulationTextService:
    return RegulationTextService(
        uow=uow,
        repository=RegulationRepository(uow.session),
        parse_block_repository=RegulationParseBlockRepository(uow.session),
        storage=RegulationStorageService(),
        operation_audit=OperationAuditService(
            repository=OperationLogRepository(uow.session),
        ),
    )
