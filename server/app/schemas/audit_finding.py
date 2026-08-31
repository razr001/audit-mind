from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.models.audit_task_page import AuditTaskPageStatus
from app.schemas.base import ApiSchema


class AuditEvidenceResponse(ApiSchema):
    id: UUID
    document_block_id: UUID | None
    page_number: int
    quote: str
    bbox: list[float] | None
    char_start: int | None = None
    char_end: int | None = None


class FindingRuleReferenceResponse(ApiSchema):
    id: UUID
    regulation_rule_id: UUID
    regulation_id: UUID
    rule_type: str
    topic: str | None
    rule_summary: str
    source_filename: str
    source_content_hash: str
    source_page_start: int | None
    source_page_end: int | None
    source_text: str


class AuditFindingResponse(ApiSchema):
    id: UUID
    page_number: int | None
    level: str
    title: str
    description: str
    recommendation: str | None
    evidences: list[AuditEvidenceResponse] = Field(default_factory=list)
    rule_references: list[FindingRuleReferenceResponse] = Field(default_factory=list)


class AuditTaskPageResponse(ApiSchema):
    id: UUID
    task_id: UUID
    page_number: int
    status: AuditTaskPageStatus
    attempt_count: int
    finding_count: int
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    # 仅 Markdown 来源返回；PDF 仍由 PDF.js 加载原文件。
    content: str | None = None
    content_start: int | None = None
    findings: list[AuditFindingResponse] = Field(default_factory=list)
