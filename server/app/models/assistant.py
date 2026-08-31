import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_db_model import BaseDbModel


class AssistantMessageRole(str, enum.Enum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"


class AssistantMessageStatus(str, enum.Enum):
    GENERATING = "GENERATING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class AssistantAgentRunStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"


class AssistantActionStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    FAILED = "FAILED"


class AssistantActionRisk(str, enum.Enum):
    WRITE = "WRITE"
    DELETE = "DELETE"
    ADMIN = "ADMIN"


class AssistantToolCallStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"


class AssistantConversation(BaseDbModel):
    __tablename__ = "assistant_conversation"
    __table_args__ = (
        Index(
            "ix_assistant_conversation_user_last_message_id",
            "user_id",
            "last_message_at",
            "id",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    messages = relationship(
        "AssistantMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
    )


class AssistantMessage(BaseDbModel):
    __tablename__ = "assistant_message"
    __table_args__ = (
        Index("ix_assistant_message_conversation_created_id", "conversation_id", "created_at", "id"),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("assistant_conversation.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[AssistantMessageRole] = mapped_column(
        Enum(AssistantMessageRole, name="assistant_message_role"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[AssistantMessageStatus] = mapped_column(
        Enum(AssistantMessageStatus, name="assistant_message_status"), nullable=False
    )
    answered: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sources: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    conversation = relationship("AssistantConversation", back_populates="messages")


class AssistantAgentRun(BaseDbModel):
    """一次 Agent 执行；通过 assistant_message_id 绑定承载结果的 AI 消息。"""

    __tablename__ = "assistant_agent_run"
    __table_args__ = (
        Index("ix_assistant_agent_run_conversation_created", "conversation_id", "created_at"),
        Index("ix_assistant_agent_run_user_status", "user_id", "status"),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("assistant_conversation.id", ondelete="CASCADE"),
        nullable=False,
    )
    assistant_message_id: Mapped[UUID] = mapped_column(
        # Agent 暂停、完成或失败时，都通过这个外键更新聊天界面的同一条 AI 消息。
        ForeignKey("assistant_message.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    thread_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    status: Mapped[AssistantAgentRunStatus] = mapped_column(
        Enum(AssistantAgentRunStatus, name="assistant_agent_run_status"),
        nullable=False,
    )
    intent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AssistantAction(BaseDbModel):
    """用户需要批准的一次写操作，保存批准时看到的完整参数和摘要。"""

    __tablename__ = "assistant_action"
    __table_args__ = (
        UniqueConstraint("run_id", "tool_call_id", name="uq_assistant_action_run_tool_call"),
        Index("ix_assistant_action_user_status_expires", "user_id", "status", "expires_at"),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("assistant_agent_run.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("assistant_conversation.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    tool_call_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    risk_level: Mapped[AssistantActionRisk] = mapped_column(
        Enum(AssistantActionRisk, name="assistant_action_risk"), nullable=False
    )
    arguments: Mapped[dict] = mapped_column(JSON, nullable=False)
    arguments_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    display_summary: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[AssistantActionStatus] = mapped_column(
        Enum(AssistantActionStatus, name="assistant_action_status"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[UUID | None] = mapped_column(nullable=True)
    result_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reconciled_by: Mapped[UUID | None] = mapped_column(nullable=True)
    reconciliation_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class AssistantToolCall(BaseDbModel):
    """写工具的实际执行凭证；它与审批 Action 分开记录客观副作用事实。"""

    __tablename__ = "assistant_tool_call"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_assistant_tool_call_idempotency"),
        UniqueConstraint("run_id", "tool_call_id", name="uq_assistant_tool_call_run_call"),
        Index("ix_assistant_tool_call_run_created", "run_id", "created_at"),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("assistant_agent_run.id", ondelete="CASCADE"), nullable=False
    )
    tool_call_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    arguments_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[AssistantToolCallStatus] = mapped_column(
        Enum(AssistantToolCallStatus, name="assistant_tool_call_status"), nullable=False
    )
    result_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[UUID | None] = mapped_column(nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
