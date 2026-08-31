from dataclasses import dataclass, field

from app.models.regulation_rule import RegulationRuleType


@dataclass(frozen=True)
class ExtractedComplianceRule:
    """LangExtract 输出经过校验后的内部不可变数据对象。"""

    # char_start/char_end 指向送入模型的完整规范原文，而不是模型改写文本。
    content: str
    char_start: int
    char_end: int

    rule_type: RegulationRuleType | None = None
    topic: str | None = None
    subject: str | None = None
    action: str | None = None
    object: str | None = None
    condition: str | None = None
    time_limit: str | None = None
    requirements: tuple[str, ...] = ()
    restrictions: tuple[str, ...] = ()
    exceptions: tuple[str, ...] = ()
    consequences: tuple[str, ...] = ()

    provision_reference: str | None = None
    section_path: str | None = None
    profile_name: str | None = None

    attributes: dict[str, str | list[str]] = field(
        default_factory=dict,
    )
