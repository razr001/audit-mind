import json
from collections.abc import Mapping, Sequence
from time import perf_counter
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.ai.regulation_qa.context import render_retrieved_chunk
from app.ai.regulation_qa.input_policy import detect_input_policy_violation
from app.ai.regulation_qa.prompts import (
    REGULATION_CONTEXT_GUARD_SYSTEM_PROMPT,
    REGULATION_CONTEXT_GUARD_USER_PROMPT,
    REGULATION_INPUT_GUARD_SYSTEM_PROMPT,
    REGULATION_INPUT_GUARD_USER_PROMPT,
    REGULATION_OUTPUT_GUARD_SYSTEM_PROMPT,
    REGULATION_OUTPUT_GUARD_USER_PROMPT,
)
from app.ai.regulation_qa.schemas import (
    GuardrailDecision,
    GuardrailOutput,
    RetrievedContextGuardrailOutput,
)
from app.core.logger import logger

MAX_GUARD_CONTEXT_CHARACTERS = 24_000


class RegulationQaGuardrails:
    """使用独立结构化模型检查用户输入、RAG 上下文和最终输出。"""

    def __init__(self, model: BaseChatModel) -> None:
        self.input_model = model.with_structured_output(
            GuardrailOutput,
            method="json_mode",
        )
        self.context_model = model.with_structured_output(
            RetrievedContextGuardrailOutput,
            method="json_mode",
        )
        self.output_model = model.with_structured_output(
            GuardrailOutput,
            method="json_mode",
        )

    async def inspect_user_input(
        self,
        *,
        question: str,
        history: list[dict[str, str]],
    ) -> GuardrailOutput:
        """在任何语义重写、检索或回答模型调用前检查直接攻击。"""
        started_at = perf_counter()
        policy_reason = detect_input_policy_violation(question)
        if policy_reason is not None:
            result = GuardrailOutput(
                decision=GuardrailDecision.BLOCK,
                reason=policy_reason,
            )
            logger.info(
                "regulation.qa.input_guard_completed",
                decision=result.decision.value,
                reason=result.reason.value,
                policy_source="deterministic",
                duration_ms=round((perf_counter() - started_at) * 1000, 2),
            )
            return result
        try:
            result = await self.input_model.ainvoke(
                [
                    SystemMessage(content=REGULATION_INPUT_GUARD_SYSTEM_PROMPT),
                    HumanMessage(
                        content=REGULATION_INPUT_GUARD_USER_PROMPT.format(
                            history=self._format_history(history),
                            question=question,
                        )
                    ),
                ]
            )
        except Exception as exc:
            logger.error(
                "regulation.qa.input_guard_failed",
                error_type=type(exc).__name__,
            )
            raise
        if not isinstance(result, GuardrailOutput):
            raise RuntimeError("AI returned an invalid input guardrail decision")
        logger.info(
            "regulation.qa.input_guard_completed",
            decision=result.decision.value,
            reason=result.reason.value,
            policy_source="model",
            duration_ms=round((perf_counter() - started_at) * 1000, 2),
        )
        return result

    async def find_unsafe_context_chunks(
        self,
        *,
        question: str,
        chunks: Sequence[Mapping[str, object]],
    ) -> set[str]:
        """检测间接提示注入，并拒绝模型返回本次检索范围外的 Chunk ID。"""
        if not chunks:
            return set()

        batches: list[tuple[list[str], set[str]]] = []
        context_parts: list[str] = []
        context_length = 0
        checked_chunk_ids: set[str] = set()
        for chunk in chunks:
            # 必须检查回答模型实际看到的完整文本，title 等用户可控元数据
            # 不能留在间接提示注入检查之外。
            part = render_retrieved_chunk(chunk)
            if len(part) > MAX_GUARD_CONTEXT_CHARACTERS:
                # 不能只检查截断内容后继续把完整 Chunk 交给回答模型。
                raise RuntimeError("retrieved chunk exceeds the guardrail context limit")
            if context_parts and context_length + len(part) > MAX_GUARD_CONTEXT_CHARACTERS:
                batches.append((context_parts, checked_chunk_ids))
                context_parts = []
                context_length = 0
                checked_chunk_ids = set()
            context_parts.append(part)
            context_length += len(part)
            checked_chunk_ids.add(str(chunk["chunk_id"]))
        if context_parts:
            batches.append((context_parts, checked_chunk_ids))

        all_unsafe_ids: set[str] = set()
        started_at = perf_counter()
        for batch_parts, batch_chunk_ids in batches:
            try:
                result = await self.context_model.ainvoke(
                    [
                        SystemMessage(content=REGULATION_CONTEXT_GUARD_SYSTEM_PROMPT),
                        HumanMessage(
                            content=REGULATION_CONTEXT_GUARD_USER_PROMPT.format(
                                question=question,
                                context="\n\n---\n\n".join(batch_parts),
                            )
                        ),
                    ]
                )
            except Exception as exc:
                logger.error(
                    "regulation.qa.context_guard_failed",
                    error_type=type(exc).__name__,
                )
                raise
            if not isinstance(result, RetrievedContextGuardrailOutput):
                raise RuntimeError("AI returned an invalid context guardrail decision")

            unsafe_ids = {str(chunk_id) for chunk_id in result.unsafe_chunk_ids}
            unknown_ids = unsafe_ids - batch_chunk_ids
            if unknown_ids:
                logger.error(
                    "regulation.qa.context_guard_unknown_chunk",
                    unknown_chunk_count=len(unknown_ids),
                )
                # 安全模型输出越界意味着本次检查不可信，不能降级绕过。
                raise RuntimeError("context guardrail returned an unknown chunk ID")
            all_unsafe_ids.update(unsafe_ids)
        logger.info(
            "regulation.qa.context_guard_completed",
            checked_chunk_count=len(chunks),
            blocked_chunk_count=len(all_unsafe_ids),
            batch_count=len(batches),
            duration_ms=round((perf_counter() - started_at) * 1000, 2),
        )
        return all_unsafe_ids

    async def inspect_output(
        self,
        *,
        question: str,
        result: dict[str, Any],
    ) -> GuardrailOutput:
        """最终返回前检查泄密、越权操作声明和危险输出。"""
        started_at = perf_counter()
        try:
            decision = await self.output_model.ainvoke(
                [
                    SystemMessage(content=REGULATION_OUTPUT_GUARD_SYSTEM_PROMPT),
                    HumanMessage(
                        content=REGULATION_OUTPUT_GUARD_USER_PROMPT.format(
                            question=question,
                            result=json.dumps(
                                result,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        )
                    ),
                ]
            )
        except Exception as exc:
            logger.error(
                "regulation.qa.output_guard_failed",
                error_type=type(exc).__name__,
            )
            raise
        if not isinstance(decision, GuardrailOutput):
            raise RuntimeError("AI returned an invalid output guardrail decision")
        logger.info(
            "regulation.qa.output_guard_completed",
            decision=decision.decision.value,
            reason=decision.reason.value,
            duration_ms=round((perf_counter() - started_at) * 1000, 2),
        )
        return decision

    @staticmethod
    def _format_history(history: list[dict[str, str]]) -> str:
        if not history:
            return "无"
        role_names = {"user": "用户", "assistant": "助手"}
        return "\n".join(
            f"{role_names.get(item.get('role', ''), '消息')}：{item.get('content', '')}"
            for item in history
        )
