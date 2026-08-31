import uuid

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_db_model import BaseDbModel


class OperationLog(BaseDbModel):
    """用户写操作的不可变快照，便于按请求和业务对象追溯变更。"""

    __tablename__ = "operation_log"

    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    # 使用普通字符串而非数据库枚举，新增业务操作时无需修改数据库类型。
    operation_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    target_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    before_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
