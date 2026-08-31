import uuid

from sqlalchemy import JSON, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_db_model import BaseDbModel


class DocumentPage(BaseDbModel):
    """按页保存解析文本和定位信息，供前端原文预览与证据定位使用。"""

    __tablename__ = "document_page"

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id"), nullable=False)

    page_number: Mapped[int] = mapped_column(Integer, nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)

    bbox: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    document = relationship("Document", back_populates="pages")
