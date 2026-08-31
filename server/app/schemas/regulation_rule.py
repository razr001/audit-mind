from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator

from app.models.regulation_rule import RegulationRuleType
from app.schemas.base import ApiSchema


class RegulationRuleResponse(ApiSchema):
    """结构化规则及其可验证的原始文件来源。"""

    id: UUID
    regulation_id: UUID
    rule_index: int = Field(ge=0)
    rule_type: RegulationRuleType
    topic: str | None = Field(max_length=2_000)
    subject: str | None = Field(max_length=2_000)
    action: str | None = Field(
        max_length=2_000,
        description=(
            "Rule action or linking predicate; use sourceText and list fields "
            "when displaying the complete rule"
        ),
    )
    object: str | None = Field(max_length=2_000)
    condition: str | None = Field(max_length=4_000)
    time_limit: str | None = Field(max_length=2_000)
    requirements: list[str] = Field(
        max_length=20,
        description="Mandatory details introduced by the rule action",
    )
    restrictions: list[str] = Field(max_length=20)
    exceptions: list[str] = Field(max_length=20)
    consequences: list[str] = Field(max_length=20)
    # 仅公开来源块 ID，不公开 Chunk、抽取配置和内部 payload。前端据此查询
    # 当前页 ParseBlock 的可信 bbox，模型不能直接提供或伪造高亮坐标。
    source_block_ids: list[UUID] = Field(default_factory=list, max_length=200)
    source_filename: str = Field(min_length=1, max_length=255)
    source_page_start: int | None = Field(ge=1)
    source_page_end: int | None = Field(ge=1)
    source_char_start: int = Field(ge=0)
    source_char_end: int = Field(ge=0)
    source_text: str = Field(min_length=1, max_length=20_000)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_public_rule(self):
        for items in (
            self.requirements,
            self.restrictions,
            self.exceptions,
            self.consequences,
        ):
            if any(len(item) > 2_000 for item in items):
                raise ValueError("regulation rule list item is too long")
        if (self.source_page_start is None) != (self.source_page_end is None):
            raise ValueError("regulation rule source page range is incomplete")
        if (
            self.source_page_start is not None
            and self.source_page_end is not None
            and self.source_page_end < self.source_page_start
        ):
            raise ValueError("regulation rule source page range is invalid")
        if self.source_char_end <= self.source_char_start:
            raise ValueError("regulation rule source character range is invalid")
        return self
