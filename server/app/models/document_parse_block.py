import uuid

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_db_model import BaseDbModel


class DocumentParseBlock(BaseDbModel):
    """MinerU 文档块，是 PDF 高亮和审计证据的唯一事实来源。"""

    __tablename__ = "document_parse_block"
    __table_args__ = (
        Index("ix_document_parse_block_document_index", "document_id", "block_index"),
        Index("ix_document_parse_block_document_page", "document_id", "page_number"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document.id"), nullable=False, index=True
    )
    block_index: Mapped[int] = mapped_column(Integer, nullable=False)
    block_type: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # MinerU content_list 使用 0..1000 的页面标准化坐标。
    bbox: Mapped[list | None] = mapped_column(JSON, nullable=True)
    text_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    block_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    document = relationship("Document", back_populates="parse_blocks")
