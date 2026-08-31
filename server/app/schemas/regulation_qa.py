from uuid import UUID

from pydantic import Field, field_validator

from app.core.regulation_qa_limits import MAX_REGULATION_ANSWER_SOURCES
from app.core.text_validation import (
    contains_visible_text,
    has_safe_source_text_characters,
    is_safe_readable_text,
)
from app.models.regulation import KnowledgeCategory, RegulationSourceType
from app.schemas.base import ApiSchema


class RegulationQuestionRequest(ApiSchema):
    """法规问答请求；过滤条件与全文搜索接口保持一致。"""

    question: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=10)
    category: KnowledgeCategory | None = None
    source_type: RegulationSourceType | None = None
    jurisdiction: str | None = Field(default=None, max_length=100)


class RegulationAnswerSource(ApiSchema):
    """由服务端从检索 Chunk 补全的可核对法规依据。"""

    chunk_id: UUID
    regulation_id: UUID
    title: str = Field(min_length=1, max_length=1000)
    page_number: int | None
    page_start: int | None = None
    page_end: int | None = None
    quote: str = Field(min_length=1, max_length=20000)

    @field_validator("title")
    @classmethod
    def title_must_be_safe_source_text(cls, value: str) -> str:
        if not contains_visible_text(value) or not has_safe_source_text_characters(
            value,
            multiline=False,
        ):
            raise ValueError("title must contain safe source text")
        return value

    @field_validator("quote")
    @classmethod
    def quote_must_be_safe_source_text(cls, value: str) -> str:
        if not contains_visible_text(value) or not has_safe_source_text_characters(
            value,
            multiline=True,
        ):
            raise ValueError("quote must contain safe source text")
        return value


class RegulationAnswerResponse(ApiSchema):
    """法规问答结果；answered=False 表示现有知识不足以回答。"""

    answered: bool
    answer: str = Field(min_length=1)
    sources: list[RegulationAnswerSource] = Field(
        default_factory=list,
        max_length=MAX_REGULATION_ANSWER_SOURCES,
    )

    @field_validator("answer")
    @classmethod
    def answer_must_contain_readable_text(cls, value: str) -> str:
        """Keep the public contract from serializing an unreadable blank answer."""
        if not is_safe_readable_text(value):
            raise ValueError("answer must contain visible text")
        return value
