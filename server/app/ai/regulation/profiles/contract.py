"""中英文合同条款的规则抽取提示词与 few-shot 示例。"""

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

CONTRACT_PROMPT = textwrap.dedent(
    f"""\
    Extract contractual obligations and reviewable clauses.
    Focus on the obligated party, required performance, conditions,
    exceptions, deadlines, remedies, termination, and breach consequences.

    {COMMON_EXTRACTION_RULES}
    """
)

CONTRACT_ZH_PROFILE = ExtractionProfile(
    name="contract.zh",
    prompt=CONTRACT_PROMPT,
    examples=COMMON_ZH_EXAMPLES
    + (
        make_example(
            text="乙方应在收到通知后五个工作日内完成整改。",
            extraction_text="乙方应在收到通知后五个工作日内完成整改",
            rule_type="time_limit",
            subject="乙方",
            action="完成整改",
            condition="收到通知后",
            time_limit="五个工作日内",
            requirements=("完成整改",),
        ),
    ),
)

CONTRACT_EN_PROFILE = ExtractionProfile(
    name="contract.en",
    prompt=CONTRACT_PROMPT,
    examples=COMMON_EN_EXAMPLES
    + (
        make_example(
            text=(
                "The Supplier must remedy the breach within five business "
                "days after receiving notice."
            ),
            extraction_text=(
                "The Supplier must remedy the breach within five business "
                "days after receiving notice"
            ),
            rule_type="time_limit",
            subject="Supplier",
            action="remedy the breach",
            condition="after receiving notice",
            time_limit="within five business days",
            requirements=("remedy the breach",),
        ),
    ),
)
