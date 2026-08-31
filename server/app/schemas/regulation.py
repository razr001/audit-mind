from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, Field, HttpUrl, TypeAdapter, field_serializer, model_validator

from app.core.config import get_settings
from app.core.regulation_block_limits import (
    REGULATION_BLOCK_CONTENT_LIMIT,
    REGULATION_METADATA_ITEM_LIMIT,
    REGULATION_METADATA_ITEMS_PER_FIELD_LIMIT,
)
from app.core.regulation_failure import public_regulation_failure
from app.core.text_validation import contains_control_character, require_safe_readable_text
from app.models.regulation import (
    PUBLIC_SOURCE_TYPES,
    KnowledgeCategory,
    KnowledgeVisibility,
    RegulationChunkStatus,
    RegulationIndexStatus,
    RegulationRuleStatus,
    RegulationSourceType,
    RegulationStatus,
)
from app.schemas.base import ApiSchema

settings = get_settings()


class RegulationUploadForm(ApiSchema):
    """multipart 上传中除文件之外的法规元数据。"""

    title: str = Field(
        min_length=1,
        max_length=255,
    )
    source_type: RegulationSourceType = RegulationSourceType.REGULATION
    visibility: KnowledgeVisibility = KnowledgeVisibility.SHARED
    language: str = Field(
        default="auto",
        min_length=2,
        max_length=20,
        pattern=r"^(?:auto|[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*)$",
    )
    document_number: str | None = Field(
        default=None,
        max_length=100,
    )
    authority: str | None = Field(
        default=None,
        max_length=255,
    )
    jurisdiction: str = Field(
        default="CN",
        min_length=1,
        max_length=100,
    )
    effective_date: date | None = None
    expiration_date: date | None = None
    version: str | None = Field(
        default=None,
        max_length=50,
    )
    source_url: str | None = Field(
        default=None,
        max_length=1000,
    )

    @model_validator(mode="after")
    def validate_form(self):
        """校验跨字段规则，并在进入 Service 前完成基础文本清理。"""
        for field_name in (
            "title",
            "language",
            "jurisdiction",
            "document_number",
            "authority",
            "version",
            "source_url",
        ):
            value = getattr(self, field_name)
            if value is not None and contains_control_character(value):
                raise ValueError(f"{field_name} must not contain control characters")
        for field_name in ("document_number", "authority", "version", "source_url"):
            value = getattr(self, field_name)
            setattr(self, field_name, value.strip() or None if value else None)

        if (
            self.effective_date is not None
            and self.expiration_date is not None
            and self.expiration_date < self.effective_date
        ):
            raise ValueError("expiration_date cannot be earlier than effective_date")

        self.title = self.title.strip()
        self.jurisdiction = self.jurisdiction.strip()
        self.language = self.language.strip()
        if not self.title:
            raise ValueError("title must not be empty")

        if not self.jurisdiction:
            raise ValueError("jurisdiction must not be empty")

        if (
            self.source_type in PUBLIC_SOURCE_TYPES
            and self.visibility == KnowledgeVisibility.PRIVATE
        ):
            raise ValueError("public knowledge cannot be private")

        if self.source_url is not None:
            if "#" in self.source_url:
                raise ValueError("source_url must not contain a fragment")
            parsed_url = TypeAdapter(HttpUrl).validate_python(self.source_url)
            if parsed_url.username is not None or parsed_url.password is not None:
                raise ValueError("source_url must not contain credentials")
            normalized_url = str(parsed_url)
            if len(normalized_url) > 1000:
                raise ValueError("normalized source_url must not exceed 1000 characters")
            self.source_url = normalized_url

        return self


class RegulationTextCreateRequest(RegulationUploadForm):
    """直接录入的 Markdown/纯文本知识及其来源元数据。"""

    content: Annotated[str, AfterValidator(require_safe_readable_text)] = Field(
        min_length=1,
        max_length=settings.REGULATION_MAX_TEXT_LENGTH,
        description="Markdown or plain text source preserved as submitted",
    )


class RegulationResponse(ApiSchema):
    """上传者可见的完整法规响应，包含哈希和失败原因等管理字段。"""

    id: UUID
    title: str
    source_type: RegulationSourceType
    category: KnowledgeCategory
    visibility: KnowledgeVisibility
    language: str
    document_number: str | None
    authority: str | None
    jurisdiction: str
    effective_date: date | None
    expiration_date: date | None
    version: str | None
    source_url: str | None
    original_filename: str
    content_type: str
    file_size: int
    content_hash: str
    uploaded_by: UUID
    enabled: bool
    status: RegulationStatus
    parse_error: str | None
    parse_started_at: datetime | None
    parse_completed_at: datetime | None
    chunk_status: RegulationChunkStatus
    chunk_error: str | None
    chunk_started_at: datetime | None
    chunk_completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    index_status: RegulationIndexStatus
    index_error: str | None
    index_started_at: datetime | None
    index_completed_at: datetime | None
    rule_status: RegulationRuleStatus
    rule_error: str | None
    rule_started_at: datetime | None
    rule_completed_at: datetime | None

    @field_serializer("parse_error")
    def serialize_parse_error(self, value: str | None) -> str | None:
        return public_regulation_failure("parse", value)

    @field_serializer("chunk_error")
    def serialize_chunk_error(self, value: str | None) -> str | None:
        return public_regulation_failure("chunk", value)

    @field_serializer("index_error")
    def serialize_index_error(self, value: str | None) -> str | None:
        return public_regulation_failure("index", value)

    @field_serializer("rule_error")
    def serialize_rule_error(self, value: str | None) -> str | None:
        return public_regulation_failure("rule", value)


class RegulationUploadListResponse(ApiSchema):
    """Minimal uploader list projection without owner IDs or file fingerprints."""

    id: UUID
    title: str
    source_type: RegulationSourceType
    category: KnowledgeCategory
    original_filename: str
    file_size: int
    enabled: bool
    status: RegulationStatus
    parse_error: str | None
    parse_started_at: datetime | None
    parse_completed_at: datetime | None
    chunk_status: RegulationChunkStatus
    chunk_error: str | None
    chunk_started_at: datetime | None
    chunk_completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    index_status: RegulationIndexStatus
    index_error: str | None
    index_started_at: datetime | None
    index_completed_at: datetime | None
    rule_status: RegulationRuleStatus
    rule_error: str | None
    rule_started_at: datetime | None
    rule_completed_at: datetime | None

    @field_serializer("parse_error")
    def serialize_parse_error(self, value: str | None) -> str | None:
        return public_regulation_failure("parse", value)

    @field_serializer("chunk_error")
    def serialize_chunk_error(self, value: str | None) -> str | None:
        return public_regulation_failure("chunk", value)

    @field_serializer("index_error")
    def serialize_index_error(self, value: str | None) -> str | None:
        return public_regulation_failure("index", value)

    @field_serializer("rule_error")
    def serialize_rule_error(self, value: str | None) -> str | None:
        return public_regulation_failure("rule", value)


class RegulationUploadResponse(ApiSchema):
    """Minimal upload acknowledgement; internal hashes and owner IDs stay server-side."""

    id: UUID
    title: str
    category: KnowledgeCategory
    visibility: KnowledgeVisibility
    original_filename: str
    status: RegulationStatus


class RegulationPublicResponse(ApiSchema):
    """普通查询使用的脱敏响应，不暴露上传者、哈希及失败详情。"""

    id: UUID
    title: str
    source_type: RegulationSourceType
    category: KnowledgeCategory
    visibility: KnowledgeVisibility
    language: str
    document_number: str | None
    authority: str | None
    jurisdiction: str
    effective_date: date | None
    expiration_date: date | None
    version: str | None
    source_url: str | None
    original_filename: str
    content_type: str
    file_size: int
    enabled: bool
    status: RegulationStatus
    parse_started_at: datetime | None
    parse_completed_at: datetime | None
    chunk_status: RegulationChunkStatus
    chunk_started_at: datetime | None
    chunk_completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    index_status: RegulationIndexStatus
    index_started_at: datetime | None
    index_completed_at: datetime | None
    rule_status: RegulationRuleStatus
    rule_started_at: datetime | None
    rule_completed_at: datetime | None
    can_manage: bool = False


class RegulationDetailResponse(RegulationPublicResponse):
    """Access-safe detail with sanitized pipeline failures and owner capability."""

    can_manage: bool = False
    page_count: int = Field(ge=0)
    parse_error: str | None
    chunk_error: str | None
    index_error: str | None
    rule_error: str | None

    @field_serializer("parse_error")
    def serialize_parse_error(self, value: str | None) -> str | None:
        return public_regulation_failure("parse", value)

    @field_serializer("chunk_error")
    def serialize_chunk_error(self, value: str | None) -> str | None:
        return public_regulation_failure("chunk", value)

    @field_serializer("index_error")
    def serialize_index_error(self, value: str | None) -> str | None:
        return public_regulation_failure("index", value)

    @field_serializer("rule_error")
    def serialize_rule_error(self, value: str | None) -> str | None:
        return public_regulation_failure("rule", value)


class RegulationAssetDownloadResponse(ApiSchema):
    """经过知识访问校验后返回的 MinerU 局部图片短期地址。"""

    block_id: UUID
    url: str
    expires_in: int
    content_type: str


class RegulationSourceDownloadResponse(ApiSchema):
    """法规原文件短期地址及原文阅读器需要的安全展示元数据。"""

    regulation_id: UUID
    url: str
    expires_in: int
    original_filename: str
    content_type: str
    page_count: int = Field(ge=0)


class RegulationParseAssetMetadataResponse(ApiSchema):
    """局部图片的公开展示属性，不包含 MinIO 对象定位信息。"""

    content_type: str
    file_size: int = Field(ge=0, le=20 * 1024 * 1024)


class RegulationVisualAnalysisResponse(ApiSchema):
    """视觉模型补充的一句话图片描述。"""

    description: str = Field(max_length=4_000)


class RegulationParseBlockMetadataResponse(ApiSchema):
    """视觉块元数据；嵌套模型确保所有字段递归转换成 camelCase。"""

    image_caption: list[str] = Field(
        default_factory=list, max_length=REGULATION_METADATA_ITEMS_PER_FIELD_LIMIT
    )
    image_footnote: list[str] = Field(
        default_factory=list, max_length=REGULATION_METADATA_ITEMS_PER_FIELD_LIMIT
    )
    table_caption: list[str] = Field(
        default_factory=list, max_length=REGULATION_METADATA_ITEMS_PER_FIELD_LIMIT
    )
    table_footnote: list[str] = Field(
        default_factory=list, max_length=REGULATION_METADATA_ITEMS_PER_FIELD_LIMIT
    )
    chart_caption: list[str] = Field(
        default_factory=list, max_length=REGULATION_METADATA_ITEMS_PER_FIELD_LIMIT
    )
    chart_footnote: list[str] = Field(
        default_factory=list, max_length=REGULATION_METADATA_ITEMS_PER_FIELD_LIMIT
    )
    sub_type: str | None = Field(default=None, max_length=100)
    asset: RegulationParseAssetMetadataResponse | None = None
    ai_visual_analysis: RegulationVisualAnalysisResponse | None = None

    @model_validator(mode="after")
    def validate_metadata_item_lengths(self):
        for items in (
            self.image_caption,
            self.image_footnote,
            self.table_caption,
            self.table_footnote,
            self.chart_caption,
            self.chart_footnote,
        ):
            if any(len(item) > REGULATION_METADATA_ITEM_LIMIT for item in items):
                raise ValueError("regulation block metadata item is too long")
        return self


class RegulationParseBlockResponse(ApiSchema):
    """前端按页渲染法规原文和 MinerU 局部图片所需的数据。"""

    id: UUID
    block_index: int
    block_type: str
    content: str = Field(max_length=REGULATION_BLOCK_CONTENT_LIMIT)
    page_number: int | None
    bbox: list | None
    text_level: int | None
    char_start: int
    char_end: int
    block_metadata: RegulationParseBlockMetadataResponse | None
