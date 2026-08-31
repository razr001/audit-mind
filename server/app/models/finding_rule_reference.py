import uuid

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_db_model import BaseDbModel


class FindingRuleReference(BaseDbModel):
    """审计发现引用的规则快照，保证历史结果可复现。"""

    __tablename__ = "finding_rule_reference"

    finding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("finding.id"),
        nullable=False,
        index=True,
    )
    regulation_rule_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    regulation_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    rule_type: Mapped[str] = mapped_column(String(50), nullable=False)
    topic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rule_summary: Mapped[str] = mapped_column(Text, nullable=False)
    rule_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
