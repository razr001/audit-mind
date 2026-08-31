from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, field_serializer

from app.core.document_failure import public_document_failure
from app.models.document import DocumentSourceType, DocumentStatus
from app.schemas.base import ApiSchema


class DocumentResponse(ApiSchema):
    """文档详情及解析、索引状态。"""

    id: UUID
    original_filename: str
    content_type: str
    file_size: int
    source_type: DocumentSourceType = DocumentSourceType.PDF
    status: DocumentStatus
    created_at: datetime
    updated_at: datetime

    # 创建MinerU解释结果
    parse_error: str | None
    parse_started_at: datetime | None
    parse_completed_at: datetime | None

    @field_serializer("parse_error")
    def serialize_parse_error(self, value: str | None) -> str | None:
        return public_document_failure("parse", value)

    model_config = ConfigDict(
        from_attributes=True,
    )


class DocumentDownloadResponse(ApiSchema):
    """短期有效的 MinIO 下载地址及有效秒数。"""

    url: str
    expires_in: int
