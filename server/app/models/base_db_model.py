import uuid

from sqlalchemy import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base
from app.infrastructure.db.mixins import TimestampMixin


class BaseDbModel(Base, TimestampMixin):
    """项目业务实体基类，统一 UUID 主键和时间戳。"""

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
