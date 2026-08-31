import uuid

from sqlalchemy import (
    JSON,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_db_model import BaseDbModel


class RegulationChunk(BaseDbModel):
    """由有语义的 ParseBlock 确定性生成的法规检索块。"""

    __tablename__ = "regulation_chunk"
    __table_args__ = (
        UniqueConstraint(
            "regulation_id",
            "chunk_index",
            name="uq_regulation_chunk_index",
        ),
    )

    regulation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "regulation.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    article_number: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )

    chapter: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    page_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    char_start: Mapped[int | None] = mapped_column(
        # 偏移量基于排除页眉、页脚和页码后的“语义全文”。
        # 原始全文位置通过 chunk_metadata.sourceSegments 映射。
        Integer,
        nullable=True,
    )

    char_end: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    chunk_metadata: Mapped[dict | None] = mapped_column(
        # 保存原始 Block、页码及语义 Chunk 到 ParseBlock 的区间映射。
        JSON,
        nullable=True,
    )

    regulation = relationship(
        "Regulation",
        back_populates="chunks",
    )
