import uuid

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_db_model import BaseDbModel


class Finding(BaseDbModel):
    """审核任务识别出的单项风险或不合规问题。"""

    __tablename__ = "finding"

    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audit_task.id"))

    task_page_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("audit_task_page.id"),
        nullable=True,
        index=True,
    )

    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    level: Mapped[str] = mapped_column(String(20))

    title: Mapped[str] = mapped_column(String(255))

    description: Mapped[str] = mapped_column(Text)

    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)

    # task=relationship(
    #     "AuditTask",
    #     back_populates="findings"
    # )

    # evidences=relationship(
    #     "Evidence",
    #     back_populates="finding",
    #     cascade="all,delete-orphan"
    # )
