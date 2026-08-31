from sqlalchemy import Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_db_model import BaseDbModel


class User(BaseDbModel):
    """最小用户实体；密码只保存 Argon2 哈希。"""

    __tablename__ = "app_user"
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # Service 会把用户名规范为小写；数据库仍以 lower(username) 兜底，
    # 防止脚本、迁移或未来的新写入路径产生仅大小写不同的重复账号。
    __table_args__ = (
        Index("ux_app_user_username_lower", func.lower(username), unique=True),
    )
