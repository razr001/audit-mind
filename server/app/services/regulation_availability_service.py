from app.core.audit_failure import AUDIT_RULES_MAINTAINING_MESSAGE
from app.core.error_codes import REGULATION_RULES_MAINTAINING
from app.core.exceptions import BusinessException
from app.infrastructure.regulation_pipeline_lock import (
    is_regulation_rule_index_maintenance_active,
)


async def require_regulation_rules_available() -> None:
    """审计开始前拒绝读取正在维护的规则集。"""
    if await is_regulation_rule_index_maintenance_active():
        raise BusinessException(
            REGULATION_RULES_MAINTAINING,
            AUDIT_RULES_MAINTAINING_MESSAGE,
        )
