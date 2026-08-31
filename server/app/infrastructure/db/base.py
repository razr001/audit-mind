from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有 SQLAlchemy 声明式模型共享的元数据根类。"""

    pass
