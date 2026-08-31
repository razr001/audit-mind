"""跨知识源复用的结构化规则 few-shot 样本。

这些样本覆盖的是规则的语言结构，而不是某一部具体法规。业务 Profile
仍可追加自己的领域样本。这样换法规、标准、合同或内部制度时，模型已经
见过多规则段落、期限、例外、授权、责任、处罚、完整清单和无规则背景。
"""

from app.ai.regulation.profiles.base import (
    make_example,
    make_extraction,
    make_multi_example,
)

COMMON_ZH_EXAMPLES = (
    make_multi_example(
        text="处理者应当记录审批结果。处理者不得删除审计日志。",
        extractions=(
            make_extraction(
                extraction_text="处理者应当记录审批结果",
                rule_type="requirement",
                topic="审批记录",
                subject="处理者",
                action="记录审批结果",
            ),
            make_extraction(
                extraction_text="处理者不得删除审计日志",
                rule_type="prohibition",
                topic="审计日志",
                subject="处理者",
                action="删除审计日志",
            ),
        ),
    ),
    make_example(
        text="发生安全事件时，责任部门应当在二十四小时内提交报告。",
        extraction_text="发生安全事件时，责任部门应当在二十四小时内提交报告",
        rule_type="time_limit",
        topic="安全事件报告",
        subject="责任部门",
        action="提交报告",
        condition="发生安全事件时",
        time_limit="二十四小时内",
    ),
    make_multi_example(
        text="法律另有规定的，不适用本条限制。经本人书面授权，可以向指定机构提供信息。",
        extractions=(
            make_extraction(
                extraction_text="法律另有规定的，不适用本条限制",
                rule_type="exception",
                topic="限制例外",
                condition="法律另有规定的",
                action="不适用本条限制",
                exceptions=("不适用本条限制",),
            ),
            make_extraction(
                extraction_text="经本人书面授权，可以向指定机构提供信息",
                rule_type="permission",
                topic="信息提供授权",
                action="向指定机构提供信息",
                condition="经本人书面授权",
            ),
        ),
    ),
    make_example(
        text="部门负责人负责复核处理记录并签字确认。",
        extraction_text="部门负责人负责复核处理记录并签字确认",
        rule_type="responsibility",
        topic="记录复核责任",
        subject="部门负责人",
        action="复核处理记录并签字确认",
    ),
    make_multi_example(
        text=(
            "本规范适用于提供在线交易服务的平台。"
            "平台宜每季度开展一次权限复核。"
            "账号查询范围仅限本人数据。"
        ),
        extractions=(
            make_extraction(
                extraction_text="本规范适用于提供在线交易服务的平台",
                rule_type="applicability",
                topic="适用范围",
                action="适用于提供在线交易服务的平台",
                object="提供在线交易服务的平台",
            ),
            make_extraction(
                extraction_text="平台宜每季度开展一次权限复核",
                rule_type="recommendation",
                topic="权限复核建议",
                subject="平台",
                action="开展一次权限复核",
                time_limit="每季度",
            ),
            make_extraction(
                extraction_text="账号查询范围仅限本人数据",
                rule_type="restriction",
                topic="账号查询范围",
                object="账号查询范围",
                restrictions=("仅限本人数据",),
            ),
        ),
    ),
    make_multi_example(
        text="违反本规定的，由主管机关责令限期改正。情节严重的，处十万元罚款。",
        extractions=(
            make_extraction(
                extraction_text="违反本规定的，由主管机关责令限期改正",
                rule_type="penalty",
                topic="违规整改",
                subject="主管机关",
                action="责令限期改正",
                condition="违反本规定的",
                consequences=("责令限期改正",),
            ),
            make_extraction(
                extraction_text="情节严重的，处十万元罚款",
                rule_type="penalty",
                topic="严重违规处罚",
                action="处十万元罚款",
                condition="情节严重的",
                consequences=("处十万元罚款",),
            ),
        ),
    ),
    make_example(
        text="访问生产系统必须满足以下条件：\n1.完成身份验证；\n2.取得负责人批准。",
        extraction_text="访问生产系统必须满足以下条件：\n1.完成身份验证；\n2.取得负责人批准。",
        rule_type="requirement",
        topic="生产系统访问",
        action="必须满足以下条件",
        object="访问生产系统",
        requirements=("完成身份验证", "取得负责人批准"),
    ),
)

COMMON_EN_EXAMPLES = (
    make_multi_example(
        text=(
            "The operator must record the approval result. "
            "The operator must not delete audit logs."
        ),
        extractions=(
            make_extraction(
                extraction_text="The operator must record the approval result",
                rule_type="requirement",
                topic="approval records",
                subject="operator",
                action="record the approval result",
            ),
            make_extraction(
                extraction_text="The operator must not delete audit logs",
                rule_type="prohibition",
                topic="audit logs",
                subject="operator",
                action="delete audit logs",
            ),
        ),
    ),
    make_example(
        text=(
            "When a security incident occurs, the responsible team must submit "
            "a report within twenty-four hours."
        ),
        extraction_text=(
            "When a security incident occurs, the responsible team must submit "
            "a report within twenty-four hours"
        ),
        rule_type="time_limit",
        topic="security incident reporting",
        subject="responsible team",
        action="submit a report",
        condition="When a security incident occurs",
        time_limit="within twenty-four hours",
    ),
    make_multi_example(
        text=(
            "This restriction does not apply where the law provides otherwise. "
            "With written authorization, information may be provided to the named agency."
        ),
        extractions=(
            make_extraction(
                extraction_text=(
                    "This restriction does not apply where the law provides otherwise"
                ),
                rule_type="exception",
                topic="exception to restriction",
                action="does not apply",
                condition="where the law provides otherwise",
                exceptions=("does not apply",),
            ),
            make_extraction(
                extraction_text=(
                    "With written authorization, information may be provided to the named agency"
                ),
                rule_type="permission",
                topic="authorized disclosure",
                action="information may be provided to the named agency",
                condition="With written authorization",
            ),
        ),
    ),
    make_example(
        text="The department head is responsible for reviewing and signing the record.",
        extraction_text=(
            "The department head is responsible for reviewing and signing the record"
        ),
        rule_type="responsibility",
        topic="record review responsibility",
        subject="department head",
        action="reviewing and signing the record",
    ),
    make_multi_example(
        text=(
            "This standard applies to platforms that provide online transaction services. "
            "A platform should review access permissions quarterly. "
            "Account searches are limited to the account owner's data."
        ),
        extractions=(
            make_extraction(
                extraction_text=(
                    "This standard applies to platforms that provide online transaction services"
                ),
                rule_type="applicability",
                topic="scope of application",
                action="applies to platforms that provide online transaction services",
                object="platforms that provide online transaction services",
            ),
            make_extraction(
                extraction_text="A platform should review access permissions quarterly",
                rule_type="recommendation",
                topic="access review recommendation",
                subject="platform",
                action="review access permissions",
                time_limit="quarterly",
            ),
            make_extraction(
                extraction_text=(
                    "Account searches are limited to the account owner's data"
                ),
                rule_type="restriction",
                topic="account search scope",
                object="Account searches",
                restrictions=("limited to the account owner's data",),
            ),
        ),
    ),
    make_multi_example(
        text=(
            "A violation may result in an order to remedy the breach. "
            "A serious violation is subject to a fine of USD 100,000."
        ),
        extractions=(
            make_extraction(
                extraction_text="A violation may result in an order to remedy the breach",
                rule_type="penalty",
                topic="remedial order",
                condition="A violation",
                consequences=("an order to remedy the breach",),
            ),
            make_extraction(
                extraction_text="A serious violation is subject to a fine of USD 100,000",
                rule_type="penalty",
                topic="serious violation penalty",
                condition="A serious violation",
                consequences=("a fine of USD 100,000",),
            ),
        ),
    ),
    make_example(
        text=(
            "Production access requires all of the following:\n"
            "1. Complete identity verification;\n"
            "2. Obtain manager approval."
        ),
        extraction_text=(
            "Production access requires all of the following:\n"
            "1. Complete identity verification;\n"
            "2. Obtain manager approval."
        ),
        rule_type="requirement",
        topic="production access",
        action="requires all of the following",
        object="Production access",
        requirements=("Complete identity verification", "Obtain manager approval"),
    ),
)
