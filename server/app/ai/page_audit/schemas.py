from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PageAuditFindingOutput(BaseModel):
    """模型只能选择服务端提供的文档块和法规规则 ID。"""

    model_config = ConfigDict(extra="forbid")

    level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    title: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=4000)
    recommendation: str | None = Field(default=None, max_length=4000)
    evidence_block_ids: list[UUID] = Field(min_length=1, max_length=20)
    rule_ids: list[UUID] = Field(min_length=1, max_length=20)

    @field_validator("title", "reason", "recommendation")
    @classmethod
    def reject_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("audit finding text must not be blank")
        return value


class PageAuditOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[PageAuditFindingOutput] = Field(default_factory=list, max_length=50)
