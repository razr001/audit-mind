from time import perf_counter

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.ai.regulation_qa.prompts import (
    REGULATION_QUERY_UNDERSTANDING_RETRY_PROMPT,
    REGULATION_QUERY_UNDERSTANDING_SYSTEM_PROMPT,
    REGULATION_QUERY_UNDERSTANDING_USER_PROMPT,
)
from app.ai.regulation_qa.schemas import QueryUnderstandingOutput
from app.core.logger import logger


class RegulationQueryUnderstanding:
    """将多轮口语问题转换为不依赖历史即可检索的结构化语义。"""

    def __init__(self, model: BaseChatModel) -> None:
        self.model = model.with_structured_output(
            QueryUnderstandingOutput,
            method="json_mode",
        )

    async def understand(
        self,
        *,
        question: str,
        history: list[dict[str, str]],
    ) -> QueryUnderstandingOutput:
        started_at = perf_counter()
        messages = [
            SystemMessage(content=REGULATION_QUERY_UNDERSTANDING_SYSTEM_PROMPT),
            HumanMessage(
                content=REGULATION_QUERY_UNDERSTANDING_USER_PROMPT.format(
                    history=self._format_history(history),
                    question=question,
                )
            ),
        ]
        result: object | None = None
        for attempt in range(2):
            try:
                result = await self.model.ainvoke(messages)
                break
            except OutputParserException:
                if attempt == 0:
                    # JSON mode only guarantees valid JSON, not that every Schema
                    # field is present. Retry once with a compact correction instead
                    # of silently inventing a missing intent in application code.
                    logger.warning(
                        "regulation.qa.query_understanding_retry",
                        reason="structured_output_validation_failed",
                    )
                    messages.append(
                        HumanMessage(
                            content=REGULATION_QUERY_UNDERSTANDING_RETRY_PROMPT,
                        )
                    )
                    continue
                logger.error(
                    "regulation.qa.query_understanding_failed",
                    error_type="OutputParserException",
                )
                raise
            except Exception as exc:
                logger.error(
                    "regulation.qa.query_understanding_failed",
                    error_type=type(exc).__name__,
                )
                raise
        if not isinstance(result, QueryUnderstandingOutput):
            raise RuntimeError("AI returned an invalid query understanding result")
        logger.info(
            "regulation.qa.query_understanding_completed",
            question_length=len(question),
            standalone_question_length=len(result.standalone_question),
            search_query_length=len(result.search_query),
            intent=result.intent.value,
            needs_clarification=result.needs_clarification,
            clarification_question_length=len(result.clarification_question or ""),
            duration_ms=round((perf_counter() - started_at) * 1000, 2),
        )
        return result

    @staticmethod
    def _format_history(history: list[dict[str, str]]) -> str:
        if not history:
            return "无"
        role_names = {"user": "用户", "assistant": "助手"}
        return "\n".join(
            f"{role_names.get(item.get('role', ''), '消息')}：{item.get('content', '')}"
            for item in history
        )
