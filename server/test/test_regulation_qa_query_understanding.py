import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.exceptions import OutputParserException

from app.ai.regulation_qa import query_understanding as query_understanding_module
from app.ai.regulation_qa.prompts import REGULATION_QUERY_UNDERSTANDING_SYSTEM_PROMPT
from app.ai.regulation_qa.query_understanding import RegulationQueryUnderstanding
from app.ai.regulation_qa.schemas import (
    QueryUnderstandingOutput,
    RegulationQueryIntent,
)


def test_query_understanding_prompt_declares_every_required_output_field() -> None:
    for field_name in QueryUnderstandingOutput.model_fields:
        assert field_name in REGULATION_QUERY_UNDERSTANDING_SYSTEM_PROMPT
    for intent in RegulationQueryIntent:
        assert intent.value in REGULATION_QUERY_UNDERSTANDING_SYSTEM_PROMPT


def test_query_understanding_retries_once_after_schema_parse_failure(monkeypatch) -> None:
    expected = QueryUnderstandingOutput(
        standalone_question="工具类 App 有哪些规则？",
        search_query="工具类 App 规则 必要个人信息",
        intent=RegulationQueryIntent.REGULATION_QA,
        needs_clarification=False,
        clarification_question=None,
    )
    runnable = SimpleNamespace(
        ainvoke=AsyncMock(
            side_effect=[
                OutputParserException("intent field is required"),
                expected,
            ]
        )
    )
    model = SimpleNamespace(with_structured_output=lambda *_args, **_kwargs: runnable)
    understanding = RegulationQueryUnderstanding(model)
    log_info = Mock()
    monkeypatch.setattr(query_understanding_module.logger, "info", log_info)

    result = asyncio.run(
        understanding.understand(
            question="工具类 App 有哪些规则？",
            history=[],
        )
    )

    assert result is expected
    assert runnable.ainvoke.await_count == 2
    retry_messages = runnable.ainvoke.await_args_list[1].args[0]
    for field_name in QueryUnderstandingOutput.model_fields:
        assert field_name in retry_messages[-1].content
    log_info.assert_called_once_with(
        "regulation.qa.query_understanding_completed",
        question_length=len("工具类 App 有哪些规则？"),
        standalone_question_length=len("工具类 App 有哪些规则？"),
        search_query_length=len("工具类 App 规则 必要个人信息"),
        intent="REGULATION_QA",
        needs_clarification=False,
        clarification_question_length=0,
        duration_ms=pytest.approx(0, abs=100),
    )
