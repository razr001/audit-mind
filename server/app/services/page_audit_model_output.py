from typing import Protocol
from uuid import UUID

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import BaseMessage, HumanMessage

from app.ai.page_audit.schemas import PageAuditOutput
from app.core.logger import logger
from app.models.audit_task import AuditStage


class StructuredPageAuditModel(Protocol):
    async def ainvoke(self, input: list[BaseMessage]) -> PageAuditOutput: ...


async def invoke_page_audit_model(
    *,
    model: StructuredPageAuditModel,
    messages: list[BaseMessage],
    task_id: UUID,
    page_number: int,
    batch_index: int,
) -> PageAuditOutput:
    """执行结构化审计；供应商偶发漏字段时仅纠正重试一次。"""
    try:
        result = await model.ainvoke(messages)
    except OutputParserException as exc:
        # 第二次仍不合法时异常继续上抛，本页失败且不会写入残缺结果。
        logger.warning(
            "audit.page.model_output_retry",
            task_id=str(task_id),
            page_number=page_number,
            batch_index=batch_index,
            stage=AuditStage.AUDITING.value,
            error_type=type(exc).__name__,
        )
        result = await model.ainvoke(
            [
                *messages,
                HumanMessage(
                    content=(
                        "请重新生成结果，并严格遵守系统消息中的 JSON 字段要求。"
                        "每条 finding 必须包含 level、title、reason、recommendation、"
                        "evidence_block_ids 和 rule_ids；不要省略任何字段。"
                        "内部 ID 只能出现在对应 ID 字段，不能写进展示文字。"
                    )
                ),
            ]
        )
    if not isinstance(result, PageAuditOutput):
        raise RuntimeError("page audit model returned an invalid result")
    return result
