from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.unit.date import utc_now


class TimestampMixin:
    """为业务表统一提供带时区的创建时间和更新时间。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utc_now, nullable=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=utc_now,
        # ORM 发出 UPDATE 时自动刷新；直接在数据库执行 SQL 时需自行处理。
        onupdate=utc_now,
        nullable=False,
    )
