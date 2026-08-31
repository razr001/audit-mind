import uuid

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_db_model import BaseDbModel


class RegulationParseBlock(BaseDbModel):
    """MinerU 返回的原始内容块，是知识抽取和前端定位的事实来源。"""

    __tablename__ = "regulation_parse_block"
    __table_args__ = (
        UniqueConstraint(
            "regulation_id",
            "block_index",
            name="uq_regulation_parse_block_index",
        ),
    )

    regulation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("regulation.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    block_index: Mapped[int] = mapped_column(Integer, nullable=False)

    block_type: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bbox: Mapped[list | None] = mapped_column(JSON, nullable=True)
    text_level: Mapped[int | None] = mapped_column(Integer, nullable=True)

    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    # 上述偏移量对应按 block_index 排序并用两个换行符拼接的规范原文。

    block_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    regulation = relationship(
        "Regulation",
        back_populates="parse_blocks",
    )
