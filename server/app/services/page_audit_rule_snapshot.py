from uuid import UUID, uuid4

from app.models.finding_rule_reference import FindingRuleReference
from app.models.regulation_rule import RegulationRule


def build_rule_payload(rule: RegulationRule) -> dict:
    """构造提交给审计模型的有限规则上下文。"""
    return {
        "id": str(rule.id),
        "type": rule.rule_type.value,
        "topic": rule.topic,
        "subject": rule.subject,
        "action": rule.action,
        "object": rule.object,
        "condition": rule.condition,
        "timeLimit": rule.time_limit,
        "requirements": rule.requirements,
        "restrictions": rule.restrictions,
        "exceptions": rule.exceptions,
        "consequences": rule.consequences,
        # 模型只使用有限上下文，最终展示仍读取下面保存的完整 source_text。
        "sourceText": rule.source_text[:6000],
    }


def build_rule_reference(
    *, finding_id: UUID, rule: RegulationRule
) -> FindingRuleReference:
    """把命中的数据库规则固化为可追溯、不会随规则更新漂移的来源快照。"""
    summary_parts = [rule.subject, rule.action, rule.object, rule.condition, rule.time_limit]
    summary = "；".join(value for value in summary_parts if value) or rule.source_text[:500]
    return FindingRuleReference(
        id=uuid4(),
        finding_id=finding_id,
        regulation_rule_id=rule.id,
        regulation_id=rule.regulation_id,
        rule_type=rule.rule_type.value,
        topic=rule.topic,
        rule_summary=summary,
        rule_snapshot=build_rule_payload(rule),
        source_filename=rule.source_filename,
        source_content_hash=rule.source_content_hash,
        source_page_start=rule.source_page_start,
        source_page_end=rule.source_page_end,
        source_text=rule.source_text,
    )
