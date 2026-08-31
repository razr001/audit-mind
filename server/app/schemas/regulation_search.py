from datetime import date
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from app.models.regulation import (
    KnowledgeCategory,
    KnowledgeVisibility,
    RegulationSourceType,
)
from app.schemas.base import ApiSchema


class RegulationSearchItem(ApiSchema):
    """法规混合检索返回的单条全文 Chunk 及其原文定位。"""

    chunk_id: UUID
    regulation_id: UUID
    title: str = Field(min_length=1, max_length=1000)
    authority: str | None = Field(max_length=255)
    effective_date: date | None
    source_type: RegulationSourceType
    category: KnowledgeCategory
    visibility: KnowledgeVisibility
    language: str = Field(min_length=1, max_length=50)
    jurisdiction: str = Field(min_length=1, max_length=100)
    chunk_index: int = Field(ge=0)
    article_number: str | None = Field(max_length=100)
    chapter: str | None = Field(max_length=2000)
    page_number: int | None = Field(ge=1, le=1_000_000)
    page_start: int | None = Field(default=None, ge=1, le=1_000_000)
    page_end: int | None = Field(default=None, ge=1, le=1_000_000)
    content: str = Field(min_length=1, max_length=20_000)
    rule_type: str | None = Field(max_length=100)
    subject: str | None = Field(max_length=2000)
    action: str | None = Field(max_length=2000)
    condition: str | None = Field(max_length=4000)
    exception: str | None = Field(max_length=4000)
    consequence: str | None = Field(max_length=4000)
    score: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_source_range(self) -> Self:
        """Keep optional page ranges complete and consistent with the source page."""
        if (self.page_start is None) != (self.page_end is None):
            raise ValueError("page_start and page_end must be provided together")
        if self.page_start is not None and self.page_end is not None:
            if self.page_end < self.page_start:
                raise ValueError("page_end must not precede page_start")
            if self.page_number is not None and not (
                self.page_start <= self.page_number <= self.page_end
            ):
                raise ValueError("page_number must be within the source range")
        return self
