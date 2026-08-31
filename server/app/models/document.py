import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship,
)

from app.models.base_db_model import BaseDbModel


class DocumentStatus(str, enum.Enum):
    """文档从上传到 MinerU 解析完成的状态。"""

    UPLOADED = "UPLOADED"
    PARSING = "PARSING"
    READY = "READY"
    FAILED = "FAILED"


class DocumentSourceType(str, enum.Enum):
    """原文载体类型；Markdown 同时覆盖普通纯文本输入。"""

    PDF = "PDF"
    MARKDOWN = "MARKDOWN"


class Document(BaseDbModel):
    """用户上传的原始文档及解析、索引任务的聚合根。"""

    __tablename__ = "document"
    __table_args__ = (
        Index("ix_document_user_created_id", "user_id", "created_at", "id"),
        Index("ix_document_user_filename_id", "user_id", "original_filename", "id"),
        Index("ix_document_user_size_id", "user_id", "file_size", "id"),
        Index("ix_document_user_status_id", "user_id", "status", "id"),
    )

    user_id: Mapped[UUID] = mapped_column(nullable=False, index=True)

    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)

    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)

    content_type: Mapped[str] = mapped_column(String(128), nullable=False)

    # 使用 VARCHAR 而非数据库枚举，避免以后增加来源类型时必须修改
    # PostgreSQL enum；允许值仍由业务模型和接口校验。
    source_type: Mapped[DocumentSourceType] = mapped_column(
        Enum(
            DocumentSourceType,
            native_enum=False,
            create_constraint=False,
            length=32,
        ),
        nullable=False,
        default=DocumentSourceType.PDF,
        server_default=DocumentSourceType.PDF.value,
    )

    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)

    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus), nullable=False, default=DocumentStatus.UPLOADED
    )

    # 每次同步 MinerU 结果都会递增版本。Redis 租约失效后，旧请求
    # 必须同时匹配该版本才能写入 READY 或 FAILED。
    lock_version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )

    pages = relationship("DocumentPage", back_populates="document", cascade="all, delete-orphan")

    parse_blocks = relationship(
        "DocumentParseBlock", back_populates="document", cascade="all, delete-orphan"
    )

    audit_tasks = relationship("AuditTask", back_populates="document", cascade="all, delete-orphan")

    # MinerU 返回的 task_id
    parse_task_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )
    # 解析失败的安全错误描述
    parse_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    # 开始解析时间
    parse_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # 成功或失败的结束时间
    parse_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
