import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_db_model import BaseDbModel


class RegulationIndexStatus(str, enum.Enum):
    """法规知识 Chunk 同步到向量索引的状态。"""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"


"""
通用知识库模型
"""


class RegulationSourceType(str, enum.Enum):
    """知识来源的业务类型，用于选择对应的 LangExtract 抽取配置。"""

    LAW = "LAW"
    REGULATION = "REGULATION"
    INDUSTRY_STANDARD = "INDUSTRY_STANDARD"
    PLATFORM_POLICY = "PLATFORM_POLICY"
    INTERNAL_POLICY = "INTERNAL_POLICY"
    CONTRACT = "CONTRACT"
    CUSTOM_RULE = "CUSTOM_RULE"


class KnowledgeCategory(str, enum.Enum):
    """面向查询端的粗粒度分类：公共知识或公司规则。"""

    PUBLIC_KNOWLEDGE = "PUBLIC_KNOWLEDGE"
    COMPANY_RULE = "COMPANY_RULE"


class KnowledgeVisibility(str, enum.Enum):
    """知识的访问范围；PRIVATE 只允许上传者访问。"""

    SHARED = "SHARED"
    PRIVATE = "PRIVATE"


PUBLIC_SOURCE_TYPES = frozenset(
    {
        RegulationSourceType.LAW,
        RegulationSourceType.REGULATION,
        RegulationSourceType.INDUSTRY_STANDARD,
        RegulationSourceType.PLATFORM_POLICY,
    }
)


def get_knowledge_category(
    source_type: RegulationSourceType,
) -> KnowledgeCategory:
    """由来源类型统一推导分类，避免客户端直接决定业务分类。"""
    if source_type in PUBLIC_SOURCE_TYPES:
        return KnowledgeCategory.PUBLIC_KNOWLEDGE

    return KnowledgeCategory.COMPANY_RULE


class RegulationStatus(str, enum.Enum):
    """法规原始文件的上传和 MinerU 解析状态。"""

    UPLOADED = "UPLOADED"
    PARSING = "PARSING"
    READY = "READY"
    FAILED = "FAILED"
    # 删除意图必须先持久化。ES 或数据库删除中断时保留该状态，后续删除
    # 请求可以继续执行，而法规不会再被误认为可用知识。
    DELETING = "DELETING"


class RegulationChunkStatus(str, enum.Enum):
    """ParseBlock 确定性生成法规全文 Chunk 的状态。"""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"


class RegulationRuleStatus(str, enum.Enum):
    """LangExtract 生成结构化合规规则的状态。"""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"


class Regulation(BaseDbModel):
    """法规、平台政策或公司规则的统一知识源模型。"""

    __tablename__ = "regulation"
    __table_args__ = (
        Index(
            "uq_regulation_shared_content_hash",
            "content_hash",
            unique=True,
            postgresql_where=text("visibility = 'SHARED'"),
        ),
        Index(
            "uq_regulation_private_user_content_hash",
            "uploaded_by",
            "content_hash",
            unique=True,
            postgresql_where=text("visibility = 'PRIVATE'"),
        ),
    )

    agent_tool_call_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assistant_tool_call.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )

    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    source_type: Mapped[RegulationSourceType] = mapped_column(
        Enum(
            RegulationSourceType,
            native_enum=False,
            create_constraint=False,
            length=50,
        ),
        nullable=False,
        default=RegulationSourceType.REGULATION,
        index=True,
    )

    category: Mapped[KnowledgeCategory] = mapped_column(
        Enum(
            KnowledgeCategory,
            native_enum=False,
            create_constraint=False,
            length=50,
        ),
        nullable=False,
        default=KnowledgeCategory.PUBLIC_KNOWLEDGE,
        index=True,
    )

    visibility: Mapped[KnowledgeVisibility] = mapped_column(
        Enum(
            KnowledgeVisibility,
            native_enum=False,
            create_constraint=False,
            length=20,
        ),
        nullable=False,
        default=KnowledgeVisibility.SHARED,
        index=True,
    )

    language: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="auto",
        index=True,
    )

    document_number: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )

    authority: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    jurisdiction: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="CN",
    )

    effective_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        index=True,
    )

    expiration_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        index=True,
    )

    version: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    source_url: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    storage_key: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        unique=True,
    )

    original_filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    content_type: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    file_size: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        index=True,
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    status: Mapped[RegulationStatus] = mapped_column(
        Enum(
            RegulationStatus,
            native_enum=False,
            create_constraint=False,
            length=20,
        ),
        nullable=False,
        default=RegulationStatus.UPLOADED,
    )

    # 每个解析、分块、索引、规则构建或删除执行者在领取工作时都会递增版本。
    # Redis 租约失效或任务被维护程序接管后，旧执行者因版本落后不能再写状态。
    lock_version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )

    parse_task_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )

    parse_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    parse_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    parse_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    chunk_status: Mapped[RegulationChunkStatus] = mapped_column(
        # 与文件解析状态分开管理：ParseBlock 就绪后，全文 Chunk 仍可能
        # 尚未构建或构建失败。
        Enum(
            RegulationChunkStatus,
            native_enum=False,
            create_constraint=False,
            length=20,
        ),
        nullable=False,
        default=RegulationChunkStatus.PENDING,
    )

    chunk_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    chunk_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    chunk_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    chunks = relationship(
        "RegulationChunk",
        back_populates="regulation",
        cascade="all, delete-orphan",
    )

    parse_blocks = relationship(
        "RegulationParseBlock",
        back_populates="regulation",
        cascade="all, delete-orphan",
    )

    index_status: Mapped[RegulationIndexStatus] = mapped_column(
        Enum(
            RegulationIndexStatus,
            native_enum=False,
            create_constraint=False,
            length=20,
        ),
        nullable=False,
        default=RegulationIndexStatus.PENDING,
        server_default=RegulationIndexStatus.PENDING.value,
    )

    index_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    index_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    index_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    rule_status: Mapped[RegulationRuleStatus] = mapped_column(
        Enum(
            RegulationRuleStatus,
            native_enum=False,
            create_constraint=False,
            length=20,
        ),
        nullable=False,
        default=RegulationRuleStatus.PENDING,
        server_default=RegulationRuleStatus.PENDING.value,
    )

    rule_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    rule_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    rule_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
