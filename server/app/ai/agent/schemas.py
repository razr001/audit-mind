from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class AgentIntent(StrEnum):
    REGULATION_QA = "REGULATION_QA"
    DRAFT_LEGAL_DOCUMENT = "DRAFT_LEGAL_DOCUMENT"
    REVIEW_LEGAL_DOCUMENT = "REVIEW_LEGAL_DOCUMENT"
    SYSTEM_READ = "SYSTEM_READ"
    SYSTEM_WRITE = "SYSTEM_WRITE"
    SYSTEM_DELETE = "SYSTEM_DELETE"
    UNSUPPORTED = "UNSUPPORTED"


class ToolExecutionReceipt(BaseModel):
    tool_call_id: str
    tool_name: str
    resource_type: str | None = None
    resource_id: UUID | None = None
    result_code: str


class AgentToolResult(BaseModel):
    status: Literal["SUCCEEDED", "FAILED", "REJECTED", "PENDING"]
    code: str
    summary: str = Field(max_length=1000)
    data: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False
    receipt: ToolExecutionReceipt | None = None


class SystemAgentFinalOutput(BaseModel):
    answer: str = Field(min_length=1)
    answered: bool = True
    sources: list[dict[str, Any]] = Field(default_factory=list)
