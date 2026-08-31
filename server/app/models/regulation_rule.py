import enum
import uuid

from sqlalchemy import JSON, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_db_model import BaseDbModel


class RegulationRuleType(str, enum.Enum):
    """审核阶段能够统一处理的原子规则类型。"""

    REQUIREMENT = "REQUIREMENT"
    PROHIBITION = "PROHIBITION"
    RESTRICTION = "RESTRICTION"
    TIME_LIMIT = "TIME_LIMIT"
    PERMISSION = "PERMISSION"
    EXCEPTION = "EXCEPTION"
    RESPONSIBILITY = "RESPONSIBILITY"
    PENALTY = "PENALTY"
    APPLICABILITY = "APPLICABILITY"
    RECOMMENDATION = "RECOMMENDATION"


class RegulationRule(BaseDbModel):
    """LangExtract 从法规 Chunk 中提取并通过来源校验的原子规则。"""

    __tablename__ = "regulation_rule"

    regulation_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        index=True,
    )

    source_chunk_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        index=True,
    )

    # 一条规则可能跨越多个 MinerU ParseBlock，按原文顺序保存其 ID。
    source_block_ids: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    rule_index: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_type: Mapped[RegulationRuleType] = mapped_column(
        Enum(
            RegulationRuleType,
            native_enum=False,
            create_constraint=False,
            length=30,
        ),
        nullable=False,
        index=True,
    )

    topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str | None] = mapped_column(Text, nullable=True)
    object: Mapped[str | None] = mapped_column(Text, nullable=True)
    condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_limit: Mapped[str | None] = mapped_column(Text, nullable=True)

    requirements: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    restrictions: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    exceptions: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    consequences: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    # 保留完整模型结果，为未来不同国家、行业或公司规则扩展字段。
    payload: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )

    # 来源快照保证原法规改名或重新上传后，历史审核仍可复现。
    source_filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    source_content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    source_page_start: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    source_page_end: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    source_char_start: Mapped[int] = mapped_column(
        # 原始 ParseBlock 规范全文中的外包区间；跨页规则的精确不连续
        # 区间保存在 payload.sourceSegments。
        Integer,
        nullable=False,
    )
    source_char_end: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    source_text: Mapped[str] = mapped_column(Text, nullable=False)

    extractor_profile: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    extractor_version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
