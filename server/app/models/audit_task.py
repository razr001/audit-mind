import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_db_model import BaseDbModel


class AuditStatus(str, enum.Enum):
    """审核任务的执行状态。"""

    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL_FAILED = "PARTIAL_FAILED"
    FAILED = "FAILED"


class AuditStage(str, enum.Enum):
    """审计流水线当前所处的步骤；失败时保留最后执行的步骤。"""

    UPLOADING = "UPLOADING"
    PARSING = "PARSING"
    INDEXING = "INDEXING"
    AUDITING = "AUDITING"
    COMPLETED = "COMPLETED"


class AuditTask(BaseDbModel):
    """针对一个用户文档发起的一次审核执行记录。"""

    __tablename__ = "audit_task"
    __table_args__ = (
        # XXL-JOB 按执行状态、阶段和最后活动时间筛选超时任务。
        Index("ix_audit_task_timeout_scan", "status", "stage", "updated_at"),
    )

    agent_tool_call_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assistant_tool_call.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document.id"),
        nullable=False,
        index=True,
    )

    status: Mapped[AuditStatus] = mapped_column(
        Enum(AuditStatus), nullable=False, default=AuditStatus.CREATED
    )

    # Redis 只负责减少重复执行；每个后台执行者领取任务时递增此版本，
    # 后续状态写入必须匹配该值，防止租约失效后的旧执行者覆盖新结果。
    lock_version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )

    stage: Mapped[AuditStage] = mapped_column(
        Enum(AuditStage), nullable=False, default=AuditStage.UPLOADING
    )

    total_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    finding_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 保存创建任务时最终采用的规则范围。后续修改系统默认配置时，历史任务
    # 仍然可以解释“当时为什么检索到这些规则”。
    rule_scope: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    audit_as_of: Mapped[date] = mapped_column(Date, nullable=False, default=date.today)

    # findings=relationship(
    #     "Finding",
    #     back_populates="task",
    #     cascade="all,delete-orphan"
    # )

    document = relationship("Document", back_populates="audit_tasks")

    @property
    def document_filename(self) -> str:
        """为新任务列表提供文件名；查询端必须预加载 document 关系。"""
        return self.document.original_filename

    @property
    def document_source_type(self):
        """供工作台区分物理 PDF 页和 Markdown 逻辑段。"""
        return self.document.source_type

    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
