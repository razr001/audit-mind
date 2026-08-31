import uuid

from sqlalchemy import JSON, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_db_model import BaseDbModel


class Evidence(BaseDbModel):
    """Finding 对应的原文证据及页码、坐标定位。"""

    __tablename__ = "evidence"

    finding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("finding.id"))

    # 第二阶段建立 DocumentParseBlock 后再补模型关系。这里先保留稳定的
    # UUID 引用，避免证据只能依赖一段容易被模型改写的 quote。
    document_block_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)

    page_number: Mapped[int] = mapped_column(Integer)

    quote: Mapped[str] = mapped_column(Text)

    bbox: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Markdown 没有 PDF bbox。保存模型实际审计片段在规范化全文中的精确
    # 字符区间，前端不需要用 quote 搜索并猜测重复文本的位置。
    char_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
