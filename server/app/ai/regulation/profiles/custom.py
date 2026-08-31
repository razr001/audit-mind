"""结构不固定的中英文自定义规则抽取配置。"""

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

CUSTOM_PROMPT = textwrap.dedent(
    f"""\
    Extract explicit, reviewable rules from user-authored rule documents.
    Do not infer requirements from opinions or examples. A rule may use
    informal wording and may not have an article or section number.

    {COMMON_EXTRACTION_RULES}
    """
)

CUSTOM_ZH_PROFILE = ExtractionProfile(
    name="custom.zh",
    prompt=CUSTOM_PROMPT,
    examples=COMMON_ZH_EXAMPLES
    + (
        make_example(
            text="隐私政策里必须写明账号注销方式。",
            extraction_text="隐私政策里必须写明账号注销方式",
            rule_type="requirement",
            subject="隐私政策",
            action="写明账号注销方式",
        ),
    ),
)

CUSTOM_EN_PROFILE = ExtractionProfile(
    name="custom.en",
    prompt=CUSTOM_PROMPT,
    examples=COMMON_EN_EXAMPLES
    + (
        make_example(
            text=("The privacy notice must explain how to delete an account."),
            extraction_text=("The privacy notice must explain how to delete an account"),
            rule_type="requirement",
            subject="privacy notice",
            action="explain how to delete an account",
        ),
    ),
)
