import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_db_model import BaseDbModel


class AuditTaskPageStatus(str, enum.Enum):
    """单页审计状态，用于部分失败后只重试未完成页面。"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AuditTaskPage(BaseDbModel):
    """一次审计任务中某一页的独立执行记录。"""

    __tablename__ = "audit_task_page"
    __table_args__ = (
        Index("ix_audit_task_page_task_page", "task_id", "page_number"),
        Index("ix_audit_task_page_task_status", "task_id", "status"),
        # 从全量页面中按状态和开始时间寻找超时执行，再回到所属任务。
        Index("ix_audit_task_page_timeout_scan", "status", "started_at", "task_id"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("audit_task.id"),
        nullable=False,
        index=True,
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[AuditTaskPageStatus] = mapped_column(
        Enum(AuditTaskPageStatus),
        nullable=False,
        default=AuditTaskPageStatus.PENDING,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    finding_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
