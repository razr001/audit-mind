from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator

from app.core.text_validation import contains_control_character
from app.models.assistant import AssistantMessageRole, AssistantMessageStatus
from app.schemas.base import ApiSchema
from app.schemas.regulation_qa import RegulationAnswerSource


class AssistantConversationUpdate(ApiSchema):
    title: str = Field(min_length=1, max_length=100)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = value.strip()
        if not value or contains_control_character(value):
            raise ValueError("title must contain safe visible text")
        return value


class AssistantConversationResponse(ApiSchema):
    id: UUID
    title: str
    last_message_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AssistantMessageResponse(ApiSchema):
    id: UUID
    conversation_id: UUID
    role: AssistantMessageRole
    content: str
    status: AssistantMessageStatus
    answered: bool | None
    sources: list[RegulationAnswerSource] = Field(default_factory=list)
    created_at: datetime


class AssistantMessageRequest(ApiSchema):
    question: str = Field(min_length=1, max_length=1000)

    @field_validator("question")
    @classmethod
    def clean_question(cls, value: str) -> str:
        value = value.strip()
        if not value or contains_control_character(value):
            raise ValueError("question must contain safe visible text")
        return value


class AssistantActionDecisionType(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class AssistantActionDecisionRequest(ApiSchema):
    version: int = Field(ge=1)
    decision: AssistantActionDecisionType
    arguments_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class AssistantActionReconciliationOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class AssistantActionReconciliationRequest(ApiSchema):
    version: int = Field(ge=1)
    outcome: AssistantActionReconciliationOutcome
    resource_id: UUID | None = None
    note: str = Field(min_length=1, max_length=2000)

    @field_validator("note")
    @classmethod
    def clean_note(cls, value: str) -> str:
        value = value.strip()
        if not value or contains_control_character(value):
            raise ValueError("note must contain safe visible text")
        return value


class AssistantActionResponse(ApiSchema):
    id: UUID
    conversation_id: UUID
    tool_name: str
    display_summary: str
    arguments: dict[str, Any]
    arguments_hash: str
    status: str
    version: int
    expires_at: datetime
    resource_type: str | None
    resource_id: UUID | None
    reconciled_at: datetime | None
    reconciled_by: UUID | None
    reconciliation_note: str | None
