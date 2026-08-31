"""中国、通用英文及欧盟法律法规的抽取配置。"""

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

LEGAL_PROMPT = textwrap.dedent(
    f"""\
    Extract legally operative provisions for compliance review.
    Focus on duties, prohibitions, permissions, applicability,
    liability, enforcement, and penalties.

    {COMMON_EXTRACTION_RULES}
    """
)

LEGAL_ZH_PROFILE = ExtractionProfile(
    name="legal.zh",
    prompt=LEGAL_PROMPT,
    examples=COMMON_ZH_EXAMPLES
    + (
        make_example(
            text="第七条 应用程序提供者不得生产传播违法信息。",
            extraction_text="应用程序提供者不得生产传播违法信息",
            rule_type="prohibition",
            subject="应用程序提供者",
            action="生产传播违法信息",
            provision_reference="第七条",
        ),
        make_example(
            text=(
                "（六）网上购物类，基本功能服务为“购买商品”，"
                "必要个人信息包括：\n"
                "1.注册用户移动电话号码；\n"
                "2.收货人姓名（名称）、地址、联系电话；\n"
                "3.支付时间、支付金额、支付渠道等支付信息。"
            ),
            extraction_text=(
                "（六）网上购物类，基本功能服务为“购买商品”，"
                "必要个人信息包括：\n"
                "1.注册用户移动电话号码；\n"
                "2.收货人姓名（名称）、地址、联系电话；\n"
                "3.支付时间、支付金额、支付渠道等支付信息。"
            ),
            rule_type="restriction",
            topic="网上购物类应用必要个人信息",
            subject="网上购物类",
            action="必要个人信息包括",
            object="购买商品",
            condition="基本功能服务为“购买商品”",
            requirements=(
                "注册用户移动电话号码",
                "收货人姓名（名称）、地址、联系电话",
                "支付时间、支付金额、支付渠道等支付信息",
            ),
        ),
    ),
)

LEGAL_EN_PROFILE = ExtractionProfile(
    name="legal.en",
    prompt=LEGAL_PROMPT,
    examples=COMMON_EN_EXAMPLES
    + (
        make_example(
            text=(
                "Section 4. A provider must inform affected users "
                "before making a material change to the service."
            ),
            extraction_text=(
                "A provider must inform affected users before making "
                "a material change to the service"
            ),
            rule_type="requirement",
            subject="provider",
            action="inform affected users",
            condition="before making a material change to the service",
            provision_reference="Section 4",
        ),
    ),
)

LEGAL_EU_EN_PROFILE = ExtractionProfile(
    name="legal.eu.en",
    prompt=LEGAL_PROMPT,
    examples=COMMON_EN_EXAMPLES
    + (
        make_example(
            text=(
                "Article 5(1)(b). Personal data shall be collected for "
                "specified, explicit and legitimate purposes."
            ),
            extraction_text=(
                "Personal data shall be collected for specified, explicit and legitimate purposes"
            ),
            rule_type="requirement",
            subject="Personal data",
            action=("collected for specified, explicit and legitimate purposes"),
            provision_reference="Article 5(1)(b)",
        ),
    ),
)
