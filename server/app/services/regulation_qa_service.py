import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Coroutine, Mapping
from typing import Any, cast
from uuid import UUID

from fastapi import Depends
from langchain_core.documents.compressor import BaseDocumentCompressor
from langchain_core.language_models import BaseChatModel

from app.ai.model import get_chat_model, get_guard_model, get_query_rewrite_model
from app.ai.regulation_qa.guardrails import RegulationQaGuardrails
from app.ai.regulation_qa.nodes import RegulationQaNodes
from app.ai.regulation_qa.query_understanding import RegulationQueryUnderstanding
from app.ai.regulation_qa.schemas import GuardrailDecision
from app.ai.regulation_qa.state import RegulationQaState
from app.ai.reranking.factory import get_reranker
from app.core.config import get_settings
from app.models.regulation import KnowledgeCategory, RegulationSourceType
from app.schemas.regulation_qa import RegulationAnswerResponse
from app.services.regulation_search_service import (
    RegulationSearchService,
    get_regulation_search_service,
)

STREAM_HEARTBEAT_SECONDS = 15.0
NodeUpdate = Mapping[str, Any]

settings = get_settings()


class RegulationQaService:
    """执行法规问答图，并将纯字典图状态转换为 API Schema。"""

    def __init__(
        self,
        *,
        search_service: RegulationSearchService,
        model: BaseChatModel,
        guardrails: RegulationQaGuardrails,
        query_understanding: RegulationQueryUnderstanding,
        reranker: BaseDocumentCompressor | None = None,
        rerank_candidate_count: int = 30,
    ) -> None:
        self.rerank_enabled = reranker is not None
        self.search_service = search_service
        self.nodes = RegulationQaNodes(
            search_service=search_service,
            model=model,
            guardrails=guardrails,
            query_understanding=query_understanding,
            reranker=reranker,
            rerank_candidate_count=rerank_candidate_count,
        )

    async def ask(
        self,
        *,
        user_id: UUID,
        question: str,
        top_k: int,
        category: KnowledgeCategory | None = None,
        source_type: RegulationSourceType | None = None,
        jurisdiction: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> RegulationAnswerResponse:
        """回答问题；最终结果必须已经通过服务端引用校验节点。"""
        state = self._build_input(
            user_id=user_id,
            question=question,
            top_k=top_k,
            category=category,
            source_type=source_type,
            jurisdiction=jurisdiction,
            history=history,
        )
        async for _ in self._stream_pipeline_updates(state, emit_heartbeats=False):
            pass
        if "result" not in state:
            raise RuntimeError("regulation QA pipeline did not return a result")
        return RegulationAnswerResponse.model_validate(state["result"])

    async def stream(
        self,
        *,
        user_id: UUID,
        question: str,
        top_k: int,
        category: KnowledgeCategory | None = None,
        source_type: RegulationSourceType | None = None,
        jurisdiction: str | None = None,
        history: list[dict[str, str]] | None = None,
        conversation_id: UUID | None = None,
        assistant_message_id: UUID | None = None,
        request_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """发送图执行阶段，并仅在引用和输出安全检查完成后发送答案。"""
        yield {"type": "phase", "data": {"phase": "guarding"}}
        validated_result: dict | None = None

        async for update in self._stream_pipeline_updates(
            self._build_input(
                user_id=user_id,
                question=question,
                top_k=top_k,
                category=category,
                source_type=source_type,
                jurisdiction=jurisdiction,
                history=history,
            ),
        ):
            if update is None:
                yield {"type": "heartbeat", "data": {}}
                continue
            guard_update = update.get("guard_user_input")
            if isinstance(guard_update, dict) and guard_update.get("guardrail_decision") == "ALLOW":
                yield {"type": "phase", "data": {"phase": "understanding"}}
            understanding_update = update.get("understand_query")
            if isinstance(understanding_update, dict):
                if understanding_update.get("needs_clarification"):
                    # 澄清问题也是模型生成的文本，进入输出护栏前同步更新
                    # 前端阶段，避免长时间停留在“正在理解问题意图”。
                    yield {"type": "phase", "data": {"phase": "screening-output"}}
                else:
                    yield {"type": "phase", "data": {"phase": "retrieving"}}
            if "retrieve_context" in update:
                phase = "reranking" if self.rerank_enabled else "screening-context"
                yield {"type": "phase", "data": {"phase": phase}}
            if "rerank_context" in update:
                yield {"type": "phase", "data": {"phase": "screening-context"}}
            if "guard_retrieved_context" in update:
                yield {"type": "phase", "data": {"phase": "generating"}}
            if "answer_question" in update:
                yield {"type": "phase", "data": {"phase": "validating"}}
            if "validate_citations" in update:
                yield {"type": "phase", "data": {"phase": "screening-output"}}

            # 安全拒绝/澄清直接结束；正常回答只能采用输出护栏之后的结果。
            final_update = update.get("build_safe_response") or update.get("guard_output")
            if isinstance(final_update, dict):
                candidate = final_update.get("result")
                if isinstance(candidate, dict):
                    validated_result = candidate

        if validated_result is None:
            raise RuntimeError("regulation QA stream returned no validated result")

        result = RegulationAnswerResponse.model_validate(validated_result)
        # Deltas are emitted only after validation. This preserves the streaming
        # UI contract without presenting an ungrounded draft as trusted output.
        for start in range(0, len(result.answer), 24):
            yield {
                "type": "text-delta",
                "data": {"textDelta": result.answer[start : start + 24]},
            }
        yield {
            "type": "sources",
            "data": {
                "sources": [
                    source.model_dump(mode="json", by_alias=True) for source in result.sources
                ]
            },
        }
        yield {
            "type": "verified",
            "data": {"answered": result.answered},
        }
        yield {"type": "done", "data": {}}

    async def _stream_pipeline_updates(
        self,
        state: RegulationQaState,
        *,
        emit_heartbeats: bool = True,
    ) -> AsyncIterator[dict | None]:
        """按固定业务顺序执行问答，并在耗时节点等待期间发送 SSE 心跳。"""
        async for update in self._run_step(
            state, "guard_user_input", self.nodes.guard_user_input, emit_heartbeats
        ):
            yield update
        if state.get("guardrail_decision") == GuardrailDecision.BLOCK.value:
            async for update in self._run_step(
                state, "build_safe_response", self.nodes.build_safe_response, emit_heartbeats
            ):
                yield update
            return

        async for update in self._run_step(
            state, "understand_query", self.nodes.understand_query, emit_heartbeats
        ):
            yield update
        if state.get("needs_clarification"):
            async for update in self._run_step(
                state, "build_safe_response", self.nodes.build_safe_response, emit_heartbeats
            ):
                yield update
            async for update in self._run_step(
                state, "guard_output", self.nodes.guard_output, emit_heartbeats
            ):
                yield update
            return

        steps: list[
            tuple[str, Callable[[RegulationQaState], Coroutine[Any, Any, NodeUpdate]]]
        ] = [
            ("retrieve_context", self.nodes.retrieve_context),
        ]
        if self.rerank_enabled:
            steps.append(("rerank_context", self.nodes.rerank_context))
        steps.extend(
            [
                ("guard_retrieved_context", self.nodes.guard_retrieved_context),
                ("answer_question", self.nodes.answer_question),
                ("validate_citations", self.nodes.validate_citations),
                ("guard_output", self.nodes.guard_output),
            ]
        )
        for name, operation in steps:
            async for update in self._run_step(state, name, operation, emit_heartbeats):
                yield update

    @staticmethod
    async def _run_step(
        state: RegulationQaState,
        name: str,
        operation: Callable[[RegulationQaState], Coroutine[Any, Any, NodeUpdate]],
        emit_heartbeats: bool,
    ) -> AsyncIterator[dict | None]:
        """执行单个节点；超时只发送心跳，不取消仍在运行的外部调用。"""
        task = asyncio.create_task(operation(state))
        try:
            while True:
                done, _ = await asyncio.wait(
                    {task},
                    timeout=STREAM_HEARTBEAT_SECONDS if emit_heartbeats else None,
                )
                if done:
                    update = dict(task.result())
                    # 每个节点只返回 RegulationQaState 的部分字段；Mapping 转成 dict 后
                    # 类型检查器会丢失 TypedDict 键信息，因此在图节点边界显式恢复。
                    state.update(cast(RegulationQaState, update))
                    yield {name: update}
                    return
                yield None
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    @staticmethod
    def _build_input(
        *,
        user_id: UUID,
        question: str,
        top_k: int,
        category: KnowledgeCategory | None,
        source_type: RegulationSourceType | None,
        jurisdiction: str | None,
        history: list[dict[str, str]] | None = None,
    ) -> RegulationQaState:
        """让同步与流式入口使用完全一致的图输入规范。"""
        return {
            "user_id": str(user_id),
            "question": question.strip(),
            "top_k": top_k,
            "category": category.value if category else None,
            "source_type": source_type.value if source_type else None,
            "jurisdiction": jurisdiction.strip() if jurisdiction else None,
            "history": history or [],
        }


def get_regulation_qa_service(
    search_service: RegulationSearchService = Depends(get_regulation_search_service),
) -> RegulationQaService:
    # 这些 getter 都是进程级缓存，但只在问答接口真正解析依赖时创建。
    # 因此模型或插件配置错误不会阻止 FastAPI 导入及健康检查启动。
    reranker = get_reranker()
    return RegulationQaService(
        search_service=search_service,
        model=get_chat_model(),
        guardrails=RegulationQaGuardrails(get_guard_model()),
        query_understanding=RegulationQueryUnderstanding(get_query_rewrite_model()),
        reranker=reranker,
        rerank_candidate_count=settings.AI_RERANK_CANDIDATE_COUNT,
    )
