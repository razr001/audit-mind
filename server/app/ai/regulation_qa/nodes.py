import time
from uuid import UUID

from langchain_core.documents.compressor import BaseDocumentCompressor
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.ai.regulation_qa.context import build_evidence_spans, render_chunk_for_answer
from app.ai.regulation_qa.errors import RegulationCitationVerificationError
from app.ai.regulation_qa.guardrails import RegulationQaGuardrails
from app.ai.regulation_qa.prompts import REGULATION_QA_SYSTEM_PROMPT, REGULATION_QA_USER_PROMPT
from app.ai.regulation_qa.query_understanding import RegulationQueryUnderstanding
from app.ai.regulation_qa.schemas import GuardrailDecision, GuardrailReason, RegulationAnswerOutput
from app.ai.regulation_qa.state import RegulationQaState, RetrievedRegulationChunk
from app.ai.reranking.contract import RerankerError
from app.ai.reranking.document_mapper import chunks_to_documents, documents_to_chunks
from app.core.logger import logger
from app.models.regulation import KnowledgeCategory, RegulationSourceType
from app.services.regulation_search_service import RegulationSearchService

MAX_CONTEXT_CHARACTERS = 20_000

# 分类由安全模型负责，展示文本由服务端固定，避免拒绝响应被攻击者继续操纵。
BLOCK_MESSAGES = {
    GuardrailReason.OUT_OF_SCOPE: (
        "当前助手只提供法规查询、规则解读和合规分析，无法处理与此无关的请求。"
    ),
    GuardrailReason.PROMPT_INJECTION: "该请求试图改变系统安全规则，无法处理。",
    GuardrailReason.SYSTEM_PROMPT_EXTRACTION: "无法提供系统提示词或内部安全规则。",
    GuardrailReason.UNSUPPORTED_ACTION: (
        "当前助手只提供法规查询与合规分析，不具备编写代码、调用接口或执行操作的能力。"
    ),
    GuardrailReason.UNAUTHORIZED_DATA_ACCESS: "无法访问或处理未经授权的数据。",
    GuardrailReason.HARMFUL_REQUEST: "该请求涉及不安全或不允许的操作，无法处理。",
    GuardrailReason.UNSAFE_OUTPUT: "本次回答未通过安全检查，请调整问题后重试。",
}


class RegulationQaNodes:
    """法规问答图节点；模型只负责分类、改写和回答，不拥有工具权限。"""

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
        self.search_service = search_service
        self.guardrails = guardrails
        self.query_understanding = query_understanding
        self.reranker = reranker
        self.rerank_candidate_count = rerank_candidate_count
        self.structured_model = model.with_structured_output(
            RegulationAnswerOutput,
            method="json_mode",
        )

    async def guard_user_input(self, state: RegulationQaState) -> dict[str, str]:
        """在改写、检索和回答之前拦截直接提示注入及越权请求。"""
        decision = await self.guardrails.inspect_user_input(
            question=state["question"], history=state.get("history", [])
        )
        if decision.decision == GuardrailDecision.BLOCK:
            logger.warning("regulation.qa.input_blocked", reason=decision.reason.value)
        return {
            "guardrail_decision": decision.decision.value,
            "guardrail_reason": decision.reason.value,
        }

    async def understand_query(self, state: RegulationQaState) -> dict[str, object]:
        """消解多轮指代并生成独立检索语句，但不改写最终回答意图。"""
        result = await self.query_understanding.understand(
            question=state["question"], history=state.get("history", [])
        )
        return {
            "standalone_question": result.standalone_question,
            "search_query": result.search_query,
            "query_intent": result.intent.value,
            "needs_clarification": result.needs_clarification,
            "clarification_question": result.clarification_question,
        }

    async def build_safe_response(self, state: RegulationQaState) -> dict[str, dict]:
        """为拦截和澄清分支返回服务端受控响应，不再调用回答模型。"""
        if state.get("guardrail_decision") == GuardrailDecision.BLOCK.value:
            reason = GuardrailReason(
                state.get("guardrail_reason", GuardrailReason.HARMFUL_REQUEST.value)
            )
            answer = BLOCK_MESSAGES.get(reason, BLOCK_MESSAGES[GuardrailReason.HARMFUL_REQUEST])
        else:
            answer = state.get("clarification_question") or "请补充需要查询的法规主题。"
        return {"result": {"answered": False, "answer": answer, "sources": []}}

    async def retrieve_context(
        self, state: RegulationQaState
    ) -> dict[str, list[RetrievedRegulationChunk]]:
        """使用语义理解节点生成的 search_query 执行法规混合检索。"""
        category = KnowledgeCategory(state["category"]) if state.get("category") else None
        source_type = (
            RegulationSourceType(state["source_type"]) if state.get("source_type") else None
        )
        search_query = state.get("search_query")
        if not search_query:
            raise RuntimeError("query understanding did not produce a search query")
        retrieval_top_k = state["top_k"]
        if self.reranker is not None:
            retrieval_top_k = max(retrieval_top_k, self.rerank_candidate_count)
        items = await self.search_service.search(
            user_id=UUID(state["user_id"]),
            query=search_query,
            top_k=retrieval_top_k,
            category=category,
            source_type=source_type,
            jurisdiction=state.get("jurisdiction"),
        )
        chunks: list[RetrievedRegulationChunk] = [
            {
                "chunk_id": str(item.chunk_id),
                "regulation_id": str(item.regulation_id),
                "title": item.title,
                "page_number": item.page_number,
                "page_start": item.page_start,
                "page_end": item.page_end,
                "content": item.content,
                "score": item.score,
            }
            for item in items
        ]
        return {"chunks": chunks}

    async def rerank_context(
        self, state: RegulationQaState
    ) -> dict[str, list[RetrievedRegulationChunk]]:
        """用独立相关性模型精排候选；服务异常时安全降级到 RRF 顺序。"""
        chunks = state.get("chunks", [])
        if self.reranker is None:
            return {"chunks": chunks[: state["top_k"]]}

        query = state.get("standalone_question") or state["question"]
        started_at = time.perf_counter()
        try:
            documents = chunks_to_documents(chunks)
            reranked_documents = await self.reranker.acompress_documents(
                documents=documents,
                query=query,
            )
            reranked_chunks = documents_to_chunks(
                reranked_documents[: state["top_k"]],
                chunks,
            )
        except RerankerError as exc:
            # Rerank 只提升相关性，不应让可用的基础检索链路整体失败。
            logger.warning(
                "regulation.qa.rerank_failed",
                error_type=type(exc).__name__,
                provider_request_id=getattr(exc, "request_id", None),
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            return {"chunks": chunks[: state["top_k"]]}

        first_metadata = reranked_documents[0].metadata if reranked_documents else {}
        logger.info(
            "regulation.qa.rerank_completed",
            candidate_count=len(chunks),
            submitted_count=first_metadata.get(
                "rerank_submitted_count",
                len(documents),
            ),
            selected_count=len(reranked_chunks),
            rerank_provider=first_metadata.get("rerank_provider"),
            provider_request_id=first_metadata.get("rerank_request_id"),
            provider_total_tokens=first_metadata.get("rerank_total_tokens"),
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        return {"chunks": reranked_chunks}

    async def guard_retrieved_context(
        self, state: RegulationQaState
    ) -> dict[str, list[RetrievedRegulationChunk]]:
        """过滤法规原文中的间接提示注入，防止知识库内容控制模型。"""
        chunks = state.get("chunks", [])
        unsafe_ids = await self.guardrails.find_unsafe_context_chunks(
            question=state["question"], chunks=chunks
        )
        if unsafe_ids:
            logger.warning(
                "regulation.qa.context_chunks_blocked",
                blocked_chunk_count=len(unsafe_ids),
                blocked_chunk_ids=sorted(unsafe_ids),
            )
        return {"chunks": [chunk for chunk in chunks if chunk["chunk_id"] not in unsafe_ids]}

    async def answer_question(self, state: RegulationQaState) -> dict[str, dict]:
        """基于已通过安全检查的有限上下文生成带来源 ID 的回答。"""
        context_parts: list[str] = []
        context_length = 0
        for chunk in state.get("chunks", []):
            part = render_chunk_for_answer(chunk)
            if context_length + len(part) > MAX_CONTEXT_CHARACTERS:
                break
            context_parts.append(part)
            context_length += len(part)
        context = "\n\n---\n\n".join(context_parts)
        if not context:
            return {
                "model_output": RegulationAnswerOutput(
                    has_sufficient_evidence=False,
                    answer="现有法规知识中未找到充分依据。",
                    citations=[],
                ).model_dump(mode="json")
            }
        result = await self.structured_model.ainvoke(
            [
                SystemMessage(content=REGULATION_QA_SYSTEM_PROMPT),
                HumanMessage(
                    content=REGULATION_QA_USER_PROMPT.format(
                        # 原问题决定回答意图；search_query 只用于召回。
                        question=state["question"],
                        standalone_question=state.get(
                            "standalone_question",
                            state["question"],
                        ),
                        history=self._format_history(state.get("history", [])),
                        context=context,
                    )
                ),
            ]
        )
        if not isinstance(result, RegulationAnswerOutput):
            raise RuntimeError("AI returned an invalid regulation answer")
        logger.info(
            "regulation.qa.answer_generated",
            has_sufficient_evidence=result.has_sufficient_evidence,
            answer_length=len(result.answer),
            citation_count=len(result.citations),
        )
        return {"model_output": result.model_dump(mode="json")}

    async def validate_citations(self, state: RegulationQaState) -> dict[str, dict]:
        """校验 Chunk/证据片段 ID，并从可信检索结果补齐短原文。"""
        model_output = state.get("model_output")
        if model_output is None:
            raise RuntimeError("answer node did not produce model output")
        output = RegulationAnswerOutput.model_validate(model_output)
        chunks_by_id = {chunk["chunk_id"]: chunk for chunk in state.get("chunks", [])}
        if not output.has_sufficient_evidence:
            if output.citations:
                raise RegulationCitationVerificationError(
                    "AI returned citations for an insufficient answer"
                )
            return {
                "result": {
                    "answered": False,
                    "answer": "现有法规知识中未找到充分依据。",
                    "sources": [],
                }
            }
        if not output.citations:
            raise RegulationCitationVerificationError("AI answer does not contain citations")

        sources: list[dict] = []
        seen_chunk_ids: set[str] = set()
        for citation in output.citations:
            chunk_id = str(citation.chunk_id)
            chunk = chunks_by_id.get(chunk_id)
            if chunk is None:
                raise RegulationCitationVerificationError("AI cited an unknown regulation chunk")
            if chunk_id in seen_chunk_ids:
                continue
            seen_chunk_ids.add(chunk_id)
            evidence_by_id = {
                span["evidence_id"]: span["content"]
                for span in build_evidence_spans(chunk)
            }
            unknown_evidence_ids = set(citation.evidence_ids) - evidence_by_id.keys()
            if unknown_evidence_ids:
                raise RegulationCitationVerificationError(
                    "AI cited an unknown regulation evidence span"
                )
            quote = "\n".join(evidence_by_id[item] for item in citation.evidence_ids)
            sources.append(
                {
                    "chunk_id": chunk_id,
                    "regulation_id": chunk["regulation_id"],
                    "title": chunk["title"],
                    "page_number": chunk["page_number"],
                    "page_start": chunk["page_start"],
                    "page_end": chunk["page_end"],
                    # 模型只能选择服务端生成的片段 ID；展示文本仍来自
                    # 原始 Chunk，但不会把整块无关内容都返回给前端。
                    "quote": quote,
                }
            )
        return {"result": {"answered": True, "answer": output.answer, "sources": sources}}

    async def guard_output(self, state: RegulationQaState) -> dict[str, dict]:
        """在客户端看到回答前执行最后一道泄密和危险内容检查。"""
        result = state.get("result")
        if result is None:
            raise RuntimeError("citation validation did not produce a result")
        decision = await self.guardrails.inspect_output(question=state["question"], result=result)
        if decision.decision == GuardrailDecision.ALLOW:
            return {"result": result}
        logger.warning("regulation.qa.output_blocked", reason=decision.reason.value)
        return {
            "result": {
                "answered": False,
                "answer": BLOCK_MESSAGES[GuardrailReason.UNSAFE_OUTPUT],
                "sources": [],
            }
        }

    @staticmethod
    def _format_history(history: list[dict[str, str]]) -> str:
        if not history:
            return "无"
        role_names = {"user": "用户", "assistant": "助手"}
        return "\n".join(
            f"{role_names.get(item.get('role', ''), '消息')}：{item.get('content', '')}"
            for item in history
        )
