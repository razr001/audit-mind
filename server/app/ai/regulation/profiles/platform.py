"""应用商店等平台政策的中英文规则抽取配置。"""

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

PLATFORM_PROMPT = textwrap.dedent(
    f"""\
    Extract reviewable platform publication and operation policies.
    Focus on submission requirements, disclosures, prohibited behavior,
    technical requirements, remediation, suspension, and removal.

    {COMMON_EXTRACTION_RULES}
    """
)

PLATFORM_ZH_PROFILE = ExtractionProfile(
    name="platform.zh",
    prompt=PLATFORM_PROMPT,
    examples=COMMON_ZH_EXAMPLES
    + (
        make_example(
            text="应用收集个人信息前，应当向用户展示隐私政策。",
            extraction_text=("应用收集个人信息前，应当向用户展示隐私政策"),
            rule_type="requirement",
            subject="应用",
            action="向用户展示隐私政策",
            condition="收集个人信息前",
        ),
    ),
)

PLATFORM_EN_PROFILE = ExtractionProfile(
    name="platform.en",
    prompt=PLATFORM_PROMPT,
    examples=COMMON_EN_EXAMPLES
    + (
        make_example(
            text=("Apps must provide a privacy policy before collecting personal data."),
            extraction_text=("Apps must provide a privacy policy before collecting personal data"),
            rule_type="requirement",
            subject="Apps",
            action="provide a privacy policy",
            condition="before collecting personal data",
        ),
    ),
)
