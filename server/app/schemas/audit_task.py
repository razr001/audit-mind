from datetime import date, datetime
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator

from app.core.audit_failure import public_audit_failure
from app.models.audit_task import AuditStage, AuditStatus
from app.models.document import DocumentSourceType
from app.models.regulation import KnowledgeCategory
from app.models.regulation_rule import RegulationRuleType
from app.schemas.base import ApiSchema


class AuditTaskProgressResponse(ApiSchema):
    """逐页审计任务的状态、进度和规则范围。"""

    id: UUID
    document_id: UUID
    status: AuditStatus
    created_at: datetime
    updated_at: datetime
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    @field_validator("error", mode="before")
    @classmethod
    def redact_internal_error(cls, value: object) -> object:
        """Only expose the small allow-list of user-safe failure summaries."""
        if value is None or isinstance(value, str):
            return public_audit_failure(value)
        return value
    stage: AuditStage
    total_pages: int
    completed_pages: int
    finding_count: int
    rule_scope: dict
    audit_as_of: date
    document_filename: str
    document_source_type: DocumentSourceType = DocumentSourceType.PDF


class AuditRuleScope(ApiSchema):
    """可选审计规则范围；空列表表示使用当前用户可访问的全部有效规则。"""

    model_config = ConfigDict(extra="forbid")

    regulation_ids: list[UUID] = Field(default_factory=list, max_length=100)
    categories: list[KnowledgeCategory] = Field(default_factory=list, max_length=10)
    jurisdictions: list[str] = Field(default_factory=list, max_length=20)
    rule_types: list[RegulationRuleType] = Field(default_factory=list, max_length=20)

    @field_validator("jurisdictions")
    @classmethod
    def validate_jurisdictions(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = value.strip()
            if not item or len(item) > 100:
                raise ValueError("jurisdiction must contain 1 to 100 characters")
            if item not in normalized:
                normalized.append(item)
        return normalized
