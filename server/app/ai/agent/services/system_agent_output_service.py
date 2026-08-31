from collections.abc import AsyncIterator
from typing import Any

from app.ai.agent.runner import read_final_output
from app.ai.agent.schemas import AgentIntent, SystemAgentFinalOutput
from app.ai.regulation_qa.nodes import BLOCK_MESSAGES
from app.ai.regulation_qa.schemas import GuardrailDecision, GuardrailReason
from app.models.assistant import AssistantAction
from app.schemas.assistant import AssistantActionDecisionType
from app.services.regulation_qa_service import RegulationQaService


async def validate_agent_final(
    *,
    regulation_qa_service: RegulationQaService,
    question: str,
    agent_intent: AgentIntent,
    agent_result: dict[str, Any],
    collected_sources: dict[str, dict[str, Any]],
    tool_receipts: list[dict[str, Any]],
    final_output_override: SystemAgentFinalOutput | None = None,
) -> tuple[SystemAgentFinalOutput, list[dict[str, Any]]]:
    final_output = final_output_override or read_final_output(agent_result)
    safe_sources = list(collected_sources.values())
    guardrail_result = await regulation_qa_service.nodes.guardrails.inspect_output(
        question=question,
        result={
            "answer": final_output.answer,
            "answered": final_output.answered,
            "agentIntent": agent_intent.value,
            "sources": safe_sources,
            "executedTools": tool_receipts,
        },
    )
    if guardrail_result.decision == GuardrailDecision.BLOCK:
        return SystemAgentFinalOutput(
            answer=BLOCK_MESSAGES[guardrail_result.reason], answered=False
        ), []
    return final_output, safe_sources


def decision_final_output(
    *,
    action: AssistantAction,
    decision: AssistantActionDecisionType,
    tool_receipt: dict[str, Any] | None,
) -> SystemAgentFinalOutput:
    """Render a write decision only from persisted facts, never model narration."""
    if decision == AssistantActionDecisionType.REJECT:
        return SystemAgentFinalOutput(
            answer=f"已取消操作：{action.display_summary}。系统未执行该写操作。"
        )
    if tool_receipt is None:
        raise RuntimeError("approved action is missing its execution receipt")
    resource_labels = {
        "regulation": "法规知识",
        "audit_task": "审计任务",
        "document": "文档",
    }
    resource_type = str(tool_receipt["resourceType"])
    resource_label = resource_labels.get(resource_type, resource_type)
    resource_id = str(tool_receipt["resourceId"])
    result_code = str(tool_receipt.get("resultCode", "SUCCEEDED"))
    if tool_receipt.get("status") == "PARTIAL":
        answer = (
            f"{action.display_summary}已创建{resource_label}，但后续调度未完成。"
            f"资源 ID：`{resource_id}`；结果码：`{result_code}`。请稍后重试后续处理。"
        )
    else:
        answer = (
            f"{action.display_summary}已执行成功。"
            f"{resource_label} ID：`{resource_id}`；结果码：`{result_code}`。"
        )
    return SystemAgentFinalOutput(answer=answer)


async def blocked_events(reason: GuardrailReason) -> AsyncIterator[dict[str, Any]]:
    answer = BLOCK_MESSAGES.get(reason, BLOCK_MESSAGES[GuardrailReason.OUT_OF_SCOPE])
    async for event in text_events(answer, False, []):
        yield event


async def text_events(
    answer: str, answered: bool, sources: list[dict[str, Any]]
) -> AsyncIterator[dict[str, Any]]:
    for start in range(0, len(answer), 24):
        yield {"type": "text-delta", "data": {"textDelta": answer[start : start + 24]}}
    yield {"type": "sources", "data": {"sources": sources}}
    yield {"type": "verified", "data": {"answered": answered}}
    yield {"type": "done", "data": {"status": "completed"}}


def action_summary(tool_name: str, arguments: dict[str, Any]) -> str:
    labels = {
        "create_text_regulation": "新增法规知识",
        "process_regulation": "处理法规知识",
        "create_markdown_audit": "创建 Markdown 审计任务",
        "create_document_audit": "基于已有文档创建审计任务",
        "retry_audit_task": "重试审计任务",
        "start_document_parse": "启动文档解析",
        "sync_document_parse": "同步文档解析结果",
    }
    target = (
        arguments.get("title")
        or arguments.get("regulation_id")
        or arguments.get("document_id")
        or arguments.get("task_id")
    )
    return f"{labels.get(tool_name, tool_name)}{f'：{target}' if target else ''}"
