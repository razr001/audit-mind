import re
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.regulation_qa_limits import MAX_REGULATION_ANSWER_SOURCES
from app.core.text_validation import is_safe_readable_text


class RegulationCitationOutput(BaseModel):
    """模型选择 Chunk 内的可信证据片段；原文由服务端补全。"""

    chunk_id: UUID = Field(
        description="Chunk ID exactly as supplied in the context",
    )
    evidence_ids: list[str] = Field(
        min_length=1,
        max_length=8,
        description="Evidence IDs exactly as supplied inside the cited chunk",
    )

    @field_validator("evidence_ids")
    @classmethod
    def evidence_ids_must_be_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("evidence IDs must be unique")
        return value

    @model_validator(mode="after")
    def evidence_ids_must_belong_to_chunk(self) -> "RegulationCitationOutput":
        pattern = re.compile(rf"{re.escape(str(self.chunk_id))}:e[1-9]\d*")
        if any(pattern.fullmatch(item) is None for item in self.evidence_ids):
            raise ValueError("evidence ID must belong to the cited chunk")
        return self


class RegulationAnswerOutput(BaseModel):
    """语言模型的内部结构化输出，最终来源字段由服务端补全。"""

    has_sufficient_evidence: bool
    answer: str = Field(min_length=1)
    citations: list[RegulationCitationOutput] = Field(
        default_factory=list,
        max_length=MAX_REGULATION_ANSWER_SOURCES,
    )

    @field_validator("answer")
    @classmethod
    def answer_must_contain_readable_text(cls, value: str) -> str:
        """Reject blank model output without altering legitimate answer spacing."""
        if not is_safe_readable_text(value):
            raise ValueError("answer must contain visible text")
        return value


class GuardrailDecision(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


class GuardrailReason(StrEnum):
    ALLOWED = "ALLOWED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    PROMPT_INJECTION = "PROMPT_INJECTION"
    SYSTEM_PROMPT_EXTRACTION = "SYSTEM_PROMPT_EXTRACTION"
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
    UNAUTHORIZED_DATA_ACCESS = "UNAUTHORIZED_DATA_ACCESS"
    HARMFUL_REQUEST = "HARMFUL_REQUEST"
    UNSAFE_OUTPUT = "UNSAFE_OUTPUT"


class GuardrailOutput(BaseModel):
    """安全模型只返回受控决策，不生成直接展示给用户的拒绝文案。"""

    decision: GuardrailDecision
    reason: GuardrailReason

    @model_validator(mode="after")
    def decision_and_reason_must_agree(self) -> "GuardrailOutput":
        if self.decision == GuardrailDecision.ALLOW:
            if self.reason != GuardrailReason.ALLOWED:
                raise ValueError("allowed guardrail decision requires ALLOWED reason")
        elif self.reason == GuardrailReason.ALLOWED:
            raise ValueError("blocked guardrail decision requires a blocking reason")
        return self


class RegulationQueryIntent(StrEnum):
    REGULATION_QA = "REGULATION_QA"
    SUMMARIZE = "SUMMARIZE"
    COMPARE = "COMPARE"
    COMPLIANCE_GUIDANCE = "COMPLIANCE_GUIDANCE"


class QueryUnderstandingOutput(BaseModel):
    """将会话问题转换为语义完整但不添加事实的检索输入。"""

    standalone_question: str = Field(min_length=1, max_length=1000)
    search_query: str = Field(min_length=1, max_length=1500)
    intent: RegulationQueryIntent
    needs_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=500)

    @field_validator("standalone_question", "search_query")
    @classmethod
    def query_text_must_be_safe(cls, value: str) -> str:
        value = value.strip()
        if not is_safe_readable_text(value):
            raise ValueError("query understanding output must contain safe visible text")
        return value

    @field_validator("clarification_question")
    @classmethod
    def clarification_must_be_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not is_safe_readable_text(value):
            raise ValueError("clarification question must contain safe visible text")
        return value

    @model_validator(mode="after")
    def clarification_fields_must_agree(self) -> "QueryUnderstandingOutput":
        if self.needs_clarification and self.clarification_question is None:
            raise ValueError("clarification question is required when clarification is needed")
        if not self.needs_clarification and self.clarification_question is not None:
            raise ValueError("clarification question must be null when clarification is not needed")
        return self


class RetrievedContextGuardrailOutput(BaseModel):
    """列出检索上下文中试图控制模型行为的 Chunk。"""

    unsafe_chunk_ids: list[UUID] = Field(default_factory=list, max_length=20)

    @field_validator("unsafe_chunk_ids")
    @classmethod
    def unsafe_ids_must_be_unique(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("unsafe chunk IDs must be unique")
        return value
