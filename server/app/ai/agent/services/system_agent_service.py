import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any
from uuid import UUID, uuid4

from langchain_core.tools import BaseTool

from app.ai.agent import capability_router
from app.ai.agent.context import AgentRuntimeContext
from app.ai.agent.errors import ApprovedArgumentsMismatch
from app.ai.agent.schemas import AgentIntent
from app.ai.agent.services import system_agent_invocation, system_agent_progress
from app.ai.agent.services.agent_tool_execution_service import (
    AgentToolExecutionService,
    tool_call_receipt,
)
from app.ai.agent.services.assistant_action_service import AssistantActionService
from app.ai.agent.services.audit_command_service import AuditCommandService
from app.ai.agent.services.document_drafting_service import DocumentDraftingService
from app.ai.agent.services.regulation_command_service import RegulationCommandService
from app.ai.agent.services.system_agent_approval_service import approval_events
from app.ai.agent.services.system_agent_checkpoint import delete_agent_checkpoint
from app.ai.agent.services.system_agent_fast_path import answer_system_agent_fast_path
from app.ai.agent.services.system_agent_output_service import (
    blocked_events,
    decision_final_output,
    text_events,
    validate_agent_final,
)
from app.ai.agent.services.system_agent_read_tools import build_read_tools
from app.ai.agent.services.system_agent_resume_recovery import (
    recover_failed_resume,
    recover_interrupted_resume,
)
from app.ai.agent.services.system_agent_state_service import SystemAgentStateService
from app.ai.agent.services.system_agent_write_tools import build_write_tools
from app.ai.agent.tool_registry import select_tools
from app.ai.regulation_qa.schemas import GuardrailDecision
from app.core.asyncio_utils import await_cancellation_safe
from app.core.config import get_settings
from app.infrastructure.db.unit_of_work import UnitOfWork
from app.infrastructure.redis_lock import RedisLease, RedisLeaseLostError
from app.models.assistant import (
    AssistantAction,
    AssistantAgentRun,
    AssistantAgentRunStatus,
    AssistantToolCallStatus,
)
from app.schemas.assistant import AssistantActionDecisionType
from app.services.audit_workflow_service import AuditWorkflowService
from app.services.document_parse_service import DocumentParseService
from app.services.document_service import DocumentService
from app.services.regulation_asset_service import RegulationAssetService
from app.services.regulation_detail_service import RegulationDetailService
from app.services.regulation_qa_service import RegulationQaService
from app.services.regulation_rule_orchestrator import RegulationRuleService
from app.services.regulation_service import RegulationService

settings = get_settings()


class SystemAgentService:
    """System Agent 的总编排入口。
    这里负责一次对话请求的完整生命周期：输入护栏、意图识别、工具选择、
    模型执行、写操作暂停审批，以及审批后的恢复执行。具体业务仍由现有
    Regulation/Audit Service 完成，Agent 只负责编排，不重复实现业务规则。
    """

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        regulation_qa_service: RegulationQaService,
        regulation_service: RegulationService,
        regulation_detail_service: RegulationDetailService,
        regulation_asset_service: RegulationAssetService,
        regulation_rule_service: RegulationRuleService,
        document_service: DocumentService,
        document_parse_service: DocumentParseService,
        audit_service: AuditWorkflowService,
        action_service: AssistantActionService,
        audit_command_service: AuditCommandService,
        regulation_command_service: RegulationCommandService,
        tool_execution_service: AgentToolExecutionService,
    ) -> None:
        self.state_service = SystemAgentStateService(
            unit_of_work=uow,
            action_repository=action_service.repository,
        )
        self.regulation_qa_service = regulation_qa_service
        self.regulation_service = regulation_service
        self.regulation_detail_service = regulation_detail_service
        self.regulation_asset_service = regulation_asset_service
        self.regulation_rule_service = regulation_rule_service
        self.document_service = document_service
        self.document_parse_service = document_parse_service
        self.audit_service = audit_service
        self.action_service = action_service
        self.audit_command_service = audit_command_service
        self.regulation_command_service = regulation_command_service
        self.tool_execution_service = tool_execution_service
        self.drafting_service = DocumentDraftingService(regulation_qa_service)
        self._delete_checkpoint = delete_agent_checkpoint

    async def stream(
        self,
        *,
        user_id: UUID,
        question: str,
        history: list[dict[str, str]],
        conversation_id: UUID,
        assistant_message_id: UUID,
        request_id: str | None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """处理一条新的用户消息，并以 SSE 事件形式返回执行进度。"""

        yield {"type": "phase", "data": {"phase": "guarding"}}
        guardrail_result = await self.regulation_qa_service.nodes.guardrails.inspect_user_input(
            question=question,
            history=history,
        )
        if guardrail_result.decision == GuardrailDecision.BLOCK:
            async for event in blocked_events(guardrail_result.reason):
                yield event
            return
        fast_answer = await answer_system_agent_fast_path(question, user_id, self.regulation_rule_service)
        if fast_answer is not None:
            async for event in text_events(fast_answer, True, []):
                yield event
            return
        agent_intent = capability_router.classify_agent_intent(question)

        agent_run = AssistantAgentRun(
            conversation_id=conversation_id,
            assistant_message_id=assistant_message_id,
            user_id=user_id,
            thread_id=f"assistant:{conversation_id}:{uuid4()}",
            status=AssistantAgentRunStatus.RUNNING,
            intent=agent_intent.value,
        )
        await self.state_service.save_run(agent_run)

        runtime_context = AgentRuntimeContext(
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=agent_run.id,
            request_id=request_id,
        )
        sources: dict[str, dict] = {}
        receipts: list[dict[str, Any]] = []
        available_tools = select_tools(
            agent_intent,
            self._build_tools(runtime_context, history, sources, receipts),
        )
        if not available_tools and agent_intent.value.startswith("SYSTEM_"):
            await self._delete_checkpoint(agent_run.thread_id)
            async for event in text_events(
                "该操作尚未在系统 Agent 中开放；当前不会执行任何系统变更。", False, []
            ):
                yield event
            return

        yield {"type": "phase", "data": {"phase": "agent-running"}}
        try:
            # thread_id 是 LangGraph checkpoint 的恢复键。审批后必须用同一个键恢复，
            # 否则会变成一次全新的模型执行，丢失原工具调用上下文。
            agent_result = None
            async for progress in system_agent_progress.run_agent_with_progress(
                lambda: system_agent_invocation.create_initial_invocation(
                    tools=available_tools,
                    messages=[*history, {"role": "user", "content": question}],
                    context=runtime_context,
                    thread_id=agent_run.thread_id,
                )
            ):
                if isinstance(progress, system_agent_progress.CompletedAgentInvocation):
                    agent_result = progress.result
                else:
                    yield progress
            if agent_result is None:
                raise RuntimeError("agent invocation produced no result")
            await self.state_service.record_usage(agent_run, agent_result)
            interruptions = agent_result.get("__interrupt__", ())
            if interruptions:
                # 写工具被 HumanInTheLoopMiddleware 暂停。这里只持久化待审批动作，
                # 不执行实际写操作，随后把 confirmation-required 返回给前端。
                async for event in approval_events(
                    agent_run=agent_run,
                    agent_result=agent_result,
                    interruption=interruptions[0],
                    available_tools=available_tools,
                    action_service=self.action_service,
                    state_service=self.state_service,
                ):
                    yield event
                return
            # 即使模型已经给出答案，仍要做最终输出校验和来源清洗。
            final_output, safe_sources = await validate_agent_final(
                regulation_qa_service=self.regulation_qa_service,
                question=question,
                agent_intent=agent_intent,
                agent_result=agent_result,
                collected_sources=sources,
                tool_receipts=receipts,
            )
        except (asyncio.CancelledError, GeneratorExit):
            await await_cancellation_safe(self.state_service.interrupt_run(agent_run))
            await await_cancellation_safe(self._delete_checkpoint(agent_run.thread_id))
            raise
        except Exception:
            await self.state_service.finish_run(
                agent_run, AssistantAgentRunStatus.FAILED, "AGENT_RUN_FAILED"
            )
            await self._delete_checkpoint(agent_run.thread_id)
            raise
        await self._delete_checkpoint(agent_run.thread_id)
        async for event in text_events(final_output.answer, final_output.answered, safe_sources):
            yield event

    async def resume_stream(
        self,
        *,
        action: AssistantAction,
        decision: AssistantActionDecisionType,
        lease: RedisLease | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """根据用户的批准/拒绝决定，从原 checkpoint 恢复暂停的 Agent。"""

        agent_run = await self.state_service.get_run(run_id=action.run_id, user_id=action.user_id)
        if agent_run.status != AssistantAgentRunStatus.WAITING_APPROVAL:
            raise RuntimeError("assistant agent run is not waiting for approval")
        runtime_context = AgentRuntimeContext(
            user_id=agent_run.user_id,
            conversation_id=agent_run.conversation_id,
            run_id=agent_run.id,
            request_id=f"agent-resume-{agent_run.id}",
        )
        sources: dict[str, dict] = {}
        receipts: list[dict[str, Any]] = []
        available_tools = select_tools(
            AgentIntent(agent_run.intent),
            self._build_tools(runtime_context, [], sources, receipts),
        )
        if decision == AssistantActionDecisionType.APPROVE:
            # 先把 Action 原子地推进到 EXECUTING，写工具只接受这个状态。
            action = await self.action_service.begin_execution(
                action_id=action.id,
                user_id=action.user_id,
            )
        await self.state_service.set_running(agent_run)
        try:
            yield {"type": "phase", "data": {"phase": "executing-approved-action"}}
            agent_result = None
            async for progress in system_agent_progress.run_agent_with_progress(
                lambda: system_agent_invocation.create_resume_invocation(
                    tools=available_tools,
                    decision=decision,
                    context=runtime_context,
                    thread_id=agent_run.thread_id,
                    lease=lease,
                ),
                timeout_seconds=settings.ASSISTANT_TURN_TIMEOUT_SECONDS,
            ):
                if isinstance(progress, system_agent_progress.CompletedAgentInvocation):
                    agent_result = progress.result
                else:
                    yield progress
            if agent_result is None:
                raise RuntimeError("agent resume produced no result")
            await self.state_service.record_usage(agent_run, agent_result)
            if agent_result.get("__interrupt__"):
                raise RuntimeError("one agent run cannot request a second write action")
            if decision == AssistantActionDecisionType.APPROVE and not receipts:
                # 客户端断线可能发生在工具成功落库之后、结果返回之前。此时从数据库
                # 恢复执行凭证，避免把已经成功的写操作再次执行一遍。
                persisted_call = await self.tool_execution_service.find_call(
                    run_id=agent_run.id,
                    tool_call_id=action.tool_call_id,
                )
                if (
                    persisted_call is not None
                    and persisted_call.status == AssistantToolCallStatus.SUCCEEDED
                ):
                    if (
                        persisted_call.tool_name != action.tool_name
                        or persisted_call.arguments_hash != action.arguments_hash
                    ):
                        await self.action_service.mark_reconciliation_required(
                            action_id=action.id,
                            user_id=action.user_id,
                        )
                        await self.state_service.finish_run(
                            agent_run,
                            AssistantAgentRunStatus.FAILED,
                            "APPROVED_ARGUMENTS_MISMATCH",
                        )
                        raise ApprovedArgumentsMismatch(
                            "persisted tool receipt does not match the approved action"
                        )
                    receipts.append(tool_call_receipt(persisted_call))
            tool_receipt = None
            if decision == AssistantActionDecisionType.APPROVE:
                if len(receipts) != 1:
                    raise RuntimeError("approved action did not produce exactly one tool receipt")
                tool_receipt = receipts[0]
            trusted_final_output = decision_final_output(
                action=action,
                decision=decision,
                tool_receipt=tool_receipt,
            )
            final_output, safe_sources = await validate_agent_final(
                regulation_qa_service=self.regulation_qa_service,
                question="恢复经过用户决定的系统操作",
                agent_intent=AgentIntent(agent_run.intent),
                agent_result=agent_result,
                collected_sources=sources,
                tool_receipts=receipts,
                final_output_override=trusted_final_output,
            )
            await self.state_service.commit_resume_result(
                agent_run=agent_run,
                action=action,
                final_output=final_output,
                safe_sources=safe_sources,
                tool_receipt=tool_receipt,
            )
            await self._delete_checkpoint(agent_run.thread_id)
        except ApprovedArgumentsMismatch:
            await self._delete_checkpoint(agent_run.thread_id)
            raise
        except (asyncio.CancelledError, GeneratorExit, TimeoutError, RedisLeaseLostError):
            try:
                await await_cancellation_safe(
                    recover_interrupted_resume(
                        decision=decision,
                        agent_run=agent_run,
                        action=action,
                        tool_execution_service=self.tool_execution_service,
                        action_service=self.action_service,
                        state_service=self.state_service,
                    )
                )
            finally:
                # 中断事实已持久化，checkpoint 不再有合法恢复入口。
                await await_cancellation_safe(self._delete_checkpoint(agent_run.thread_id))
            raise
        except Exception:
            try:
                await recover_failed_resume(
                    decision=decision,
                    agent_run=agent_run,
                    action=action,
                    tool_execution_service=self.tool_execution_service,
                    action_service=self.action_service,
                    state_service=self.state_service,
                )
            finally:
                await self._delete_checkpoint(agent_run.thread_id)
            raise
        yield {
            "type": "final-result",
            "data": {
                "answer": final_output.answer,
                "answered": final_output.answered,
                "sources": safe_sources,
            },
        }

    def _build_tools(
        self,
        runtime_context: AgentRuntimeContext,
        history: list[dict[str, str]],
        collected_sources: dict[str, dict[str, Any]],
        tool_receipts: list[dict[str, Any]],
    ) -> list[BaseTool]:
        read_tools = build_read_tools(
            runtime_context=runtime_context,
            history=history,
            collected_sources=collected_sources,
            max_chars=settings.ASSISTANT_AGENT_TOOL_RESULT_MAX_CHARACTERS,
            regulation_qa_service=self.regulation_qa_service,
            drafting_service=self.drafting_service,
            regulation_service=self.regulation_service,
            regulation_detail_service=self.regulation_detail_service,
            regulation_asset_service=self.regulation_asset_service,
            regulation_rule_service=self.regulation_rule_service,
            document_service=self.document_service,
            audit_service=self.audit_service,
        )
        write_tools = build_write_tools(
            runtime_context=runtime_context,
            tool_receipts=tool_receipts,
            audit_command_service=self.audit_command_service,
            regulation_command_service=self.regulation_command_service,
            document_parse_service=self.document_parse_service,
            tool_execution_service=self.tool_execution_service,
        )
        return [*read_tools, *write_tools]
    async def get_run(self, *, run_id: UUID, user_id: UUID) -> AssistantAgentRun:
        return await self.state_service.get_run(run_id=run_id, user_id=user_id)
