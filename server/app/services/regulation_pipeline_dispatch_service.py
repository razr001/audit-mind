from uuid import UUID

from app.models.regulation import (
    Regulation,
    RegulationChunkStatus,
    RegulationIndexStatus,
    RegulationRuleStatus,
    RegulationStatus,
)
from app.tasks.regulation_dispatcher import enqueue_regulation_pipeline


async def schedule_regulation_pipeline(
    *,
    regulation: Regulation,
    user_id: UUID,
    request_id: str,
) -> bool:
    """幂等派发未完成的法规流水线；返回本次是否实际创建了队列消息。"""

    complete = (
        regulation.status == RegulationStatus.READY
        and regulation.chunk_status == RegulationChunkStatus.READY
        and regulation.index_status == RegulationIndexStatus.READY
        and regulation.rule_status == RegulationRuleStatus.READY
    )
    if complete:
        return False
    await enqueue_regulation_pipeline(
        regulation_id=regulation.id,
        user_id=user_id,
        request_id=request_id,
    )
    return True
