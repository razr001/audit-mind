from uuid import UUID

from app.core.logger import logger
from app.models.audit_task import AuditStage


def log_page_audit_event(
    event: str,
    *,
    task_id: UUID,
    page_number: int,
    **fields: object,
) -> None:
    """统一补齐逐页审计日志的检索维度。"""
    logger.info(
        event,
        task_id=str(task_id),
        page_number=page_number,
        stage=AuditStage.AUDITING.value,
        **fields,
    )
