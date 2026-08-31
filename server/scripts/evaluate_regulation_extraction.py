"""使用独立留出样本评估法规规则抽取质量。

Profile 的 few-shot 单元测试只能验证配置结构，不能证明真实模型在新文档上的
泛化能力。本脚本会调用当前 ``AI_MODEL``，用未出现在 Profile 示例中的规则片段
计算精确率、召回率和关键字段正确率。修改模型、Prompt、Profile 或 LangExtract
版本后，应先运行本脚本，再决定是否重新构建生产规则。

运行：

    uv run python scripts/evaluate_regulation_extraction.py

只运行某类样本：

    uv run python scripts/evaluate_regulation_extraction.py --case legal-zh

阈值不达标时脚本返回非零退出码，可用于 CI 或发布前检查。评测会产生真实模型
调用费用，但不会连接数据库，也不会修改法规、规则或 Elasticsearch 数据。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ai.regulation.extractor import ComplianceRuleExtractor  # noqa: E402
from app.ai.regulation.schemas import ExtractedComplianceRule  # noqa: E402
from app.models.regulation import RegulationSourceType  # noqa: E402
from app.models.regulation_rule import RegulationRuleType  # noqa: E402


@dataclass(frozen=True)
class ExpectedRule:
    """使用唯一原文锚点匹配规则，并可校验关键结构字段。"""

    rule_type: RegulationRuleType
    anchor: str
    fields: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class EvaluationCase:
    name: str
    source_type: RegulationSourceType
    language: str
    jurisdiction: str
    text: str
    expected: tuple[ExpectedRule, ...]


CASES = (
    EvaluationCase(
        name="legal-zh",
        source_type=RegulationSourceType.REGULATION,
        language="zh",
        jurisdiction="CN",
        text=(
            "第十二条 经营者应当建立用户投诉处理机制。"
            "收到投诉后，应当在十五个工作日内反馈处理结果。"
            "未经用户同意，不得公开用户身份信息。"
        ),
        expected=(
            ExpectedRule(RegulationRuleType.REQUIREMENT, "建立用户投诉处理机制"),
            ExpectedRule(
                RegulationRuleType.TIME_LIMIT,
                "十五个工作日内反馈处理结果",
                (("time_limit", "十五个工作日内"),),
            ),
            ExpectedRule(RegulationRuleType.PROHIBITION, "不得公开用户身份信息"),
        ),
    ),
    EvaluationCase(
        name="legal-list-zh",
        source_type=RegulationSourceType.LAW,
        language="zh",
        jurisdiction="CN",
        text=(
            "数据出境应当具备下列条件：\n"
            "1.完成安全评估；\n"
            "2.与接收方约定数据保护责任；\n"
            "3.保存出境活动记录。"
        ),
        expected=(
            ExpectedRule(
                RegulationRuleType.REQUIREMENT,
                "数据出境应当具备下列条件",
            ),
        ),
    ),
    EvaluationCase(
        name="contract-zh",
        source_type=RegulationSourceType.CONTRACT,
        language="zh",
        jurisdiction="CN",
        text=(
            "甲方应于验收通过后十日内支付服务费。"
            "乙方未经甲方书面同意不得转包。"
            "乙方逾期交付的，每日支付合同金额千分之一的违约金。"
        ),
        expected=(
            ExpectedRule(RegulationRuleType.TIME_LIMIT, "十日内支付服务费"),
            ExpectedRule(RegulationRuleType.PROHIBITION, "不得转包"),
            ExpectedRule(RegulationRuleType.PENALTY, "支付合同金额千分之一的违约金"),
        ),
    ),
    EvaluationCase(
        name="internal-zh",
        source_type=RegulationSourceType.INTERNAL_POLICY,
        language="zh",
        jurisdiction="CN",
        text=(
            "财务人员发起付款后，复核人员必须核对收款账户。"
            "同一人员不得同时承担付款发起和复核职责。"
            "值班人员负责在发现异常付款时立即上报财务负责人。"
        ),
        expected=(
            ExpectedRule(RegulationRuleType.REQUIREMENT, "必须核对收款账户"),
            ExpectedRule(RegulationRuleType.PROHIBITION, "不得同时承担"),
            ExpectedRule(RegulationRuleType.RESPONSIBILITY, "立即上报财务负责人"),
        ),
    ),
    EvaluationCase(
        name="standard-zh",
        source_type=RegulationSourceType.INDUSTRY_STANDARD,
        language="zh",
        jurisdiction="CN",
        text=(
            "备份文件应采用不低于256位的加密算法。"
            "关键系统宜每半年开展一次恢复演练。"
            "本标准适用于处理重要数据的信息系统。"
        ),
        expected=(
            ExpectedRule(RegulationRuleType.REQUIREMENT, "不低于256位"),
            ExpectedRule(RegulationRuleType.RECOMMENDATION, "宜每半年开展一次恢复演练"),
            ExpectedRule(RegulationRuleType.APPLICABILITY, "适用于处理重要数据的信息系统"),
        ),
    ),
    EvaluationCase(
        name="platform-en",
        source_type=RegulationSourceType.PLATFORM_POLICY,
        language="en",
        jurisdiction="US",
        text=(
            "Developers must disclose all categories of collected data. "
            "Apps may request location access only while the related feature is in use. "
            "Repeated violations may result in removal from the store."
        ),
        expected=(
            ExpectedRule(RegulationRuleType.REQUIREMENT, "disclose all categories"),
            ExpectedRule(RegulationRuleType.RESTRICTION, "only while the related feature is in use"),
            ExpectedRule(RegulationRuleType.PENALTY, "removal from the store"),
        ),
    ),
    EvaluationCase(
        name="custom-zh",
        source_type=RegulationSourceType.CUSTOM_RULE,
        language="zh",
        jurisdiction="CN",
        text=(
            "上线前要让法务看一遍隐私政策。"
            "没有工单就不能改生产配置。"
            "紧急故障除外，但事后两小时内要补工单。"
        ),
        expected=(
            ExpectedRule(RegulationRuleType.REQUIREMENT, "上线前要让法务看一遍"),
            ExpectedRule(RegulationRuleType.PROHIBITION, "没有工单就不能改生产配置"),
            ExpectedRule(RegulationRuleType.EXCEPTION, "紧急故障除外"),
            ExpectedRule(RegulationRuleType.TIME_LIMIT, "事后两小时内要补工单"),
        ),
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", dest="cases", help="仅运行指定样本")
    parser.add_argument("--min-precision", type=float, default=0.80)
    parser.add_argument("--min-recall", type=float, default=0.80)
    parser.add_argument("--min-field-accuracy", type=float, default=0.90)
    parser.add_argument("--verbose", action="store_true", help="打印每条模型规则的结构字段")
    return parser.parse_args()


def _normalize(value: str) -> str:
    return "".join(value.split()).lower()


def _matches(expected: ExpectedRule, actual: ExtractedComplianceRule) -> bool:
    return actual.rule_type == expected.rule_type and _normalize(expected.anchor) in _normalize(
        actual.content
    )


def _field_value(rule: ExtractedComplianceRule, name: str) -> str | None:
    value = getattr(rule, name)
    if isinstance(value, tuple):
        return "\n".join(value)
    return value


async def evaluate_case(
    extractor: ComplianceRuleExtractor,
    case: EvaluationCase,
    *,
    verbose: bool = False,
) -> tuple[int, int, int, int, int, int]:
    actual = await extractor.extract(
        text=case.text,
        source_type=case.source_type,
        language=case.language,
        jurisdiction=case.jurisdiction,
    )
    if verbose:
        for rule in actual:
            print(
                f"  ACTUAL type={rule.rule_type} action={rule.action!r} "
                f"requirements={rule.requirements!r} exceptions={rule.exceptions!r} "
                f"content={rule.content!r}"
            )
    matched_actual: set[int] = set()
    matched_expected = 0
    checked_fields = 0
    correct_fields = 0

    for expected in case.expected:
        match_index = next(
            (
                index
                for index, rule in enumerate(actual)
                if index not in matched_actual and _matches(expected, rule)
            ),
            None,
        )
        if match_index is None:
            print(f"  MISSING {expected.rule_type.value}: {expected.anchor}")
            continue

        matched_actual.add(match_index)
        matched_expected += 1
        matched_rule = actual[match_index]
        for field_name, expected_value in expected.fields:
            checked_fields += 1
            if _field_value(matched_rule, field_name) == expected_value:
                correct_fields += 1
            else:
                print(
                    f"  FIELD {field_name}: expected={expected_value!r} "
                    f"actual={_field_value(matched_rule, field_name)!r}"
                )

    for index, rule in enumerate(actual):
        if index not in matched_actual:
            print(f"  UNEXPECTED {rule.rule_type}: {rule.content}")

    print(f"{case.name}: expected={len(case.expected)} actual={len(actual)} matched={matched_expected}")
    return matched_expected, len(case.expected), len(matched_actual), len(actual), correct_fields, checked_fields


async def async_main() -> int:
    args = parse_args()
    selected = [case for case in CASES if not args.cases or case.name in args.cases]
    unknown = set(args.cases or ()) - {case.name for case in CASES}
    if unknown:
        raise SystemExit(f"unknown evaluation cases: {', '.join(sorted(unknown))}")

    extractor = ComplianceRuleExtractor()
    totals = [0, 0, 0, 0, 0, 0]
    for case in selected:
        result = await evaluate_case(extractor, case, verbose=args.verbose)
        totals = [left + right for left, right in zip(totals, result, strict=True)]

    precision = totals[2] / totals[3] if totals[3] else 1.0
    recall = totals[0] / totals[1] if totals[1] else 1.0
    field_accuracy = totals[4] / totals[5] if totals[5] else 1.0
    print(
        f"TOTAL precision={precision:.2%} recall={recall:.2%} "
        f"field_accuracy={field_accuracy:.2%}"
    )
    return int(
        precision < args.min_precision
        or recall < args.min_recall
        or field_accuracy < args.min_field_accuracy
    )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
