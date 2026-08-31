from uuid import UUID

from app.ai.agent.capability_router import is_rule_count_question
from app.services.regulation_rule_orchestrator import RegulationRuleService


async def answer_system_agent_fast_path(
    question: str,
    user_id: UUID,
    rule_service: RegulationRuleService,
) -> str | None:
    """直接处理必须读取真实业务数据、但无需模型规划的简单统计问题。"""

    if is_rule_count_question(question):
        total = await rule_service.count_accessible_rules(user_id=user_id)
        return f"当前你可访问的结构化法规规则共有 {total} 条。"
    return None
