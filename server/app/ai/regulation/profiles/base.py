from dataclasses import dataclass

import langextract as lx

from app.models.regulation_rule import RegulationRuleType

EXAMPLE_SCALAR_FIELDS = (
    "topic",
    "subject",
    "action",
    "object",
    "condition",
    "time_limit",
    "provision_reference",
    "section_path",
)
EXAMPLE_LIST_FIELDS = (
    "requirements",
    "restrictions",
    "exceptions",
    "consequences",
)
GROUNDED_EXAMPLE_FIELDS = (
    "subject",
    "action",
    "object",
    "condition",
    "time_limit",
    *EXAMPLE_LIST_FIELDS,
)

COMMON_EXTRACTION_RULES = """
Return extraction_class as compliance_rule.
The extraction_text must be copied exactly from the source text.
Never translate, summarize, rewrite, or invent source text.
Ignore titles, table-of-contents entries, publication notices,
and descriptive background that do not express a reviewable rule.
Return rules in their source order.

Each extraction must represent one atomic, independently reviewable rule.
A source passage containing multiple independent rules must produce multiple
extractions in source order. Do not merge unrelated duties merely because they
appear in the same paragraph. Do not return the same rule more than once.
A heading followed by a list or table is one complete rule when the heading
defines the meaning of the listed items. Include the heading and every related
list item in extraction_text; never extract only the heading.

Return these attributes for every extraction:
- rule_type: REQUIREMENT, PROHIBITION, RESTRICTION, TIME_LIMIT,
  PERMISSION, EXCEPTION, RESPONSIBILITY, PENALTY, APPLICABILITY,
  or RECOMMENDATION. Choose the type that represents the provision's primary
  legal or operational effect; do not duplicate one provision under many types.
- topic: the compliance subject matter addressed by the rule.
- subject: the governed person, organization, system, or party.
- action: the required, prohibited, permitted, or recommended conduct.
- object: the data, document, system, person, or thing affected by the action.
- condition: when or under what circumstances the rule applies.
- time_limit: the exact deadline, duration, frequency, or date expression.
- requirements: all mandatory items contained in this rule, in source order.
- restrictions: all limits or boundaries contained in this rule, in source order.
- exceptions: all exceptions or exemptions, in source order.
- consequences: all penalties, remedies, escalations, or stated results.
- provision_reference: the source identifier, if present.
- section_path: the source heading hierarchy, if known.

Every value in subject, action, object, condition, time_limit, requirements,
restrictions, exceptions, and consequences must be copied verbatim from
extraction_text. Topic may be a concise label. Use an empty string for a
missing scalar attribute and an empty list for a missing list attribute. Do not
truncate enumerated items. Preserve the language used by the source.

Use APPLICABILITY only for an explicit scope statement identifying who, what,
where, or when the document or provision applies. Use RECOMMENDATION only when
the source explicitly uses non-binding language such as "should", "recommended",
"宜", or "建议". Do not convert recommendations into mandatory requirements.
""".strip()


@dataclass(frozen=True)
class ExtractionProfile:
    """某类知识源的提示词、示例和 LangExtract 分段参数。"""

    name: str
    prompt: str
    examples: tuple[lx.data.ExampleData, ...]
    max_char_buffer: int = 4000
    extraction_passes: int = 1

    def __post_init__(self) -> None:
        """在服务启动时尽早发现示例字段漂移和非原文内容。"""
        validate_profile(self)


def make_example(
    *,
    text: str,
    extraction_text: str,
    rule_type: str,
    topic: str = "",
    subject: str = "",
    action: str = "",
    object: str = "",
    condition: str = "",
    time_limit: str = "",
    requirements: tuple[str, ...] = (),
    restrictions: tuple[str, ...] = (),
    exceptions: tuple[str, ...] = (),
    consequences: tuple[str, ...] = (),
    provision_reference: str = "",
    section_path: str = "",
) -> lx.data.ExampleData:
    """用统一属性集合构建 few-shot 示例，避免各 Profile 字段漂移。"""
    return make_multi_example(
        text=text,
        extractions=(
            make_extraction(
                extraction_text=extraction_text,
                rule_type=rule_type,
                topic=topic,
                subject=subject,
                action=action,
                object=object,
                condition=condition,
                time_limit=time_limit,
                requirements=requirements,
                restrictions=restrictions,
                exceptions=exceptions,
                consequences=consequences,
                provision_reference=provision_reference,
                section_path=section_path,
            ),
        ),
    )


def make_extraction(
    *,
    extraction_text: str,
    rule_type: str,
    topic: str = "",
    subject: str = "",
    action: str = "",
    object: str = "",
    condition: str = "",
    time_limit: str = "",
    requirements: tuple[str, ...] = (),
    restrictions: tuple[str, ...] = (),
    exceptions: tuple[str, ...] = (),
    consequences: tuple[str, ...] = (),
    provision_reference: str = "",
    section_path: str = "",
) -> lx.data.Extraction:
    """构建一条可复用的规则示例，供单规则和多规则文档共同使用。"""
    return lx.data.Extraction(
        extraction_class="compliance_rule",
        extraction_text=extraction_text,
        attributes={
            "rule_type": rule_type.upper(),
            "topic": topic,
            "subject": subject,
            "action": action,
            "object": object,
            "condition": condition,
            "time_limit": time_limit,
            "requirements": list(requirements),
            "restrictions": list(restrictions),
            "exceptions": list(exceptions),
            "consequences": list(consequences),
            "provision_reference": provision_reference,
            "section_path": section_path,
        },
    )


def make_multi_example(
    *,
    text: str,
    extractions: tuple[lx.data.Extraction, ...],
) -> lx.data.ExampleData:
    """构建可含多条原子规则的文档示例，并保留规则出现顺序。"""
    return lx.data.ExampleData(
        text=text,
        extractions=list(extractions),
    )


def validate_profile(profile: ExtractionProfile) -> None:
    """验证 Profile 的 few-shot 样本符合生产抽取契约。"""
    if not profile.name.strip() or not profile.prompt.strip():
        raise ValueError("extraction profile name and prompt must not be empty")
    if not profile.examples:
        raise ValueError(f"extraction profile has no examples: {profile.name}")
    if profile.max_char_buffer <= 0 or profile.extraction_passes <= 0:
        raise ValueError(f"invalid extraction parameters: {profile.name}")

    allowed_types = {member.value for member in RegulationRuleType}
    required_attributes = {
        "rule_type",
        *EXAMPLE_SCALAR_FIELDS,
        *EXAMPLE_LIST_FIELDS,
    }
    for example_index, example in enumerate(profile.examples):
        if not example.extractions:
            # LangExtract 1.6 的 Prompt 对齐器不接受空 extraction 示例。
            raise ValueError(f"empty extraction example is not supported: {profile.name}")
        for extraction_index, extraction in enumerate(example.extractions):
            location = f"{profile.name}[{example_index}].extractions[{extraction_index}]"
            if extraction.extraction_class != "compliance_rule":
                raise ValueError(f"invalid extraction class: {location}")
            if extraction.extraction_text not in example.text:
                raise ValueError(f"extraction text is not copied from source: {location}")

            attributes = extraction.attributes or {}
            if set(attributes) != required_attributes:
                raise ValueError(f"example attributes do not match schema: {location}")
            if attributes["rule_type"] not in allowed_types:
                raise ValueError(f"invalid rule type: {location}")

            normalized_source = "".join(extraction.extraction_text.split())
            for field_name in GROUNDED_EXAMPLE_FIELDS:
                value = attributes[field_name]
                values = value if isinstance(value, list) else [value]
                if any(
                    item and "".join(item.split()) not in normalized_source
                    for item in values
                ):
                    raise ValueError(
                        f"example attribute is not grounded: {location}.{field_name}"
                    )
