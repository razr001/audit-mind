"""中英文公司内部制度与流程规则抽取配置。"""

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

INTERNAL_PROMPT = textwrap.dedent(
    f"""\
    Extract auditable rules from internal policies and procedures.
    Focus on roles, approvals, segregation of duties, required records,
    escalation, deadlines, and prohibited internal actions.

    {COMMON_EXTRACTION_RULES}
    """
)

INTERNAL_ZH_PROFILE = ExtractionProfile(
    name="internal.zh",
    prompt=INTERNAL_PROMPT,
    examples=COMMON_ZH_EXAMPLES
    + (
        make_example(
            text=("生产环境访问规范：员工使用生产数据库前，必须提交工单并获得部门负责人批准。"),
            extraction_text=("员工使用生产数据库前，必须提交工单并获得部门负责人批准"),
            rule_type="requirement",
            subject="员工",
            action="提交工单并获得部门负责人批准",
            condition="使用生产数据库前",
            section_path="生产环境访问规范",
        ),
    ),
)

INTERNAL_EN_PROFILE = ExtractionProfile(
    name="internal.en",
    prompt=INTERNAL_PROMPT,
    examples=COMMON_EN_EXAMPLES
    + (
        make_example(
            text=(
                "Employees must obtain manager approval before accessing the production database."
            ),
            extraction_text=(
                "Employees must obtain manager approval before accessing the production database"
            ),
            rule_type="requirement",
            subject="Employees",
            action="obtain manager approval",
            condition="before accessing the production database",
        ),
    ),
)
