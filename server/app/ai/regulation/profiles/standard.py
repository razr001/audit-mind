"""行业标准和技术规范的中英文规则抽取配置。"""

import textwrap

from app.ai.regulation.profiles.base import (
    COMMON_EXTRACTION_RULES,
    ExtractionProfile,
    make_example,
)
from app.ai.regulation.profiles.example_catalog import (
    COMMON_EN_EXAMPLES,
    COMMON_ZH_EXAMPLES,
)

STANDARD_PROMPT = textwrap.dedent(
    f"""\
    Extract auditable requirements from technical or industry standards.
    Distinguish mandatory requirements from recommendations. Preserve
    thresholds, measurements, time limits, and technical conditions.

    {COMMON_EXTRACTION_RULES}
    """
)

STANDARD_ZH_PROFILE = ExtractionProfile(
    name="standard.zh",
    prompt=STANDARD_PROMPT,
    examples=COMMON_ZH_EXAMPLES
    + (
        make_example(
            text="日志记录应至少保存六个月。",
            extraction_text="日志记录应至少保存六个月",
            rule_type="time_limit",
            subject="",
            action="日志记录应至少保存六个月",
            time_limit="至少保存六个月",
            requirements=("日志记录",),
        ),
    ),
)

STANDARD_EN_PROFILE = ExtractionProfile(
    name="standard.en",
    prompt=STANDARD_PROMPT,
    examples=COMMON_EN_EXAMPLES
    + (
        make_example(
            text="Audit logs must be retained for at least six months.",
            extraction_text=("Audit logs must be retained for at least six months"),
            rule_type="time_limit",
            subject="",
            action="Audit logs must be retained",
            condition="for at least six months",
            time_limit="for at least six months",
            requirements=("Audit logs",),
        ),
    ),
)
