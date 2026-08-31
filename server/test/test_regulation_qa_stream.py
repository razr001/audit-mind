import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.ai.regulation_qa.errors import (
    REGULATION_QA_VERIFICATION_ERROR_CODE,
    REGULATION_QA_VERIFICATION_ERROR_MESSAGE,
    RegulationCitationVerificationError,
)
from app.ai.regulation_qa.nodes import RegulationQaNodes
from app.ai.regulation_qa.schemas import (
    RegulationAnswerOutput,
    RegulationCitationOutput,
)
from app.api.regulation import regulation_answer_event_stream
from app.core.regulation_qa_limits import MAX_REGULATION_ANSWER_SOURCES
from app.core.security import get_jwt_user
from app.main import create_app
from app.schemas.auth import CurrentUser
from app.schemas.regulation_qa import (
    RegulationAnswerResponse,
    RegulationAnswerSource,
)
from app.services import regulation_qa_service as qa_service_module
from app.services.regulation_qa_service import (
    RegulationQaService,
    get_regulation_qa_service,
)

USER_ID = UUID("9efa0f2d-7e1f-4204-8d0e-f254e36c8e59")


class FakeNodes:
    def __init__(self, result):
        self.result = result
        self.input = None

    async def guard_user_input(self, state):
        self.input = state.copy()
        return {"guardrail_decision": "ALLOW"}

    async def understand_query(self, _state):
        return {"needs_clarification": False}

    async def retrieve_context(self, _state):
        return {"chunks": []}

    async def rerank_context(self, _state):
        return {"chunks": []}

    async def guard_retrieved_context(self, _state):
        return {"chunks": []}

    async def answer_question(self, _state):
        return {"model_output": {}}

    async def validate_citations(self, _state):
        return {"result": self.result}

    async def guard_output(self, _state):
        return {"result": self.result}


class FakeRequest:
    def __init__(self, disconnected):
        self.disconnected = disconnected

    async def is_disconnected(self):
        if callable(self.disconnected):
            return self.disconnected()
        return self.disconnected


def collect_stream(service, **kwargs):
    async def collect():
        return [event async for event in service.stream(**kwargs)]

    return asyncio.run(collect())


def test_answer_source_limit_is_shared_by_model_and_public_response() -> None:
    citations = [
        RegulationCitationOutput(chunk_id=chunk_id, evidence_ids=[f"{chunk_id}:e1"])
        for chunk_id in [uuid4() for _ in range(MAX_REGULATION_ANSWER_SOURCES)]
    ]
    assert (
        len(
            RegulationAnswerOutput(
                has_sufficient_evidence=True,
                answer="已校验回答",
                citations=citations,
            ).citations
        )
        == MAX_REGULATION_ANSWER_SOURCES
    )
    with pytest.raises(ValidationError):
        RegulationAnswerOutput(
            has_sufficient_evidence=True,
            answer="来源过多",
            citations=[
                *citations,
                RegulationCitationOutput(
                    chunk_id=(extra_chunk_id := uuid4()),
                    evidence_ids=[f"{extra_chunk_id}:e1"],
                ),
            ],
        )

    chunk_id = uuid4()
    source = RegulationAnswerSource(
        chunk_id=chunk_id,
        regulation_id=uuid4(),
        title="审\u00ad计法",
        page_number=1,
        page_start=1,
        page_end=1,
        quote="原文\u200b引用\r\n第二行",
    )
    assert source.title == "审\u00ad计法"
    assert source.quote == "原文\u200b引用\r\n第二行"

    with pytest.raises(ValidationError, match="safe source text"):
        RegulationAnswerSource(
            chunk_id=chunk_id,
            regulation_id=uuid4(),
            title="审计法",
            page_number=1,
            quote="原文\x00引用",
        )
    with pytest.raises(ValidationError):
        RegulationAnswerResponse(
            answered=True,
            answer="来源过多",
            sources=[source] * (MAX_REGULATION_ANSWER_SOURCES + 1),
        )


@pytest.mark.parametrize(
    "format_character",
    ["\u200b", "\u2060", "\u00ad", "\ufeff"],
    ids=["zero-width-space", "word-joiner", "soft-hyphen", "bom"],
)
def test_source_text_rejects_format_only_content(format_character: str) -> None:
    chunk_id = uuid4()
    for field_name in ("title", "quote"):
        values = {"title": "审计法", "quote": "原文引用"}
        values[field_name] = format_character
        with pytest.raises(ValidationError, match="safe source text"):
            RegulationAnswerSource(
                chunk_id=chunk_id,
                regulation_id=uuid4(),
                page_number=1,
                **values,
            )


@pytest.mark.parametrize("has_sufficient_evidence", [True, False])
@pytest.mark.parametrize(
    "unreadable_answer",
    [
        "   \n\t",
        "\u200b",
        "\u2060",
        "\ufeff",
        "\x00",
        "可信\u202e答案",
        "可信\u200b答案",
        "可信\x00答案",
    ],
    ids=[
        "whitespace",
        "zero-width-space",
        "word-joiner",
        "bom",
        "control",
        "embedded-bidi",
        "embedded-zero-width",
        "embedded-control",
    ],
)
def test_model_answer_rejects_unreadable_text(
    has_sufficient_evidence: bool,
    unreadable_answer: str,
) -> None:
    with pytest.raises(ValidationError, match="visible"):
        RegulationAnswerOutput(
            has_sufficient_evidence=has_sufficient_evidence,
            answer=unreadable_answer,
            citations=[],
        )

    answer = "\t  保留合法回答的原始空白。\n"
    assert (
        RegulationAnswerOutput(
            has_sufficient_evidence=has_sufficient_evidence,
            answer=answer,
            citations=[],
        ).answer
        == answer
    )


@pytest.mark.parametrize("answered", [True, False])
@pytest.mark.parametrize(
    "unreadable_answer",
    [
        "   \n\t",
        "\u200b",
        "\u2060",
        "\ufeff",
        "\x00",
        "可信\u202e答案",
        "可信\u200b答案",
        "可信\x00答案",
    ],
    ids=[
        "whitespace",
        "zero-width-space",
        "word-joiner",
        "bom",
        "control",
        "embedded-bidi",
        "embedded-zero-width",
        "embedded-control",
    ],
)
def test_public_answer_rejects_unreadable_text(
    answered: bool,
    unreadable_answer: str,
) -> None:
    with pytest.raises(ValidationError, match="visible"):
        RegulationAnswerResponse(
            answered=answered,
            answer=unreadable_answer,
            sources=[],
        )


@pytest.mark.parametrize("answered", [True, False])
def test_answer_schemas_reject_an_embedded_surrogate(answered: bool) -> None:
    answer = "可信\ud800答案"
    with pytest.raises(ValidationError):
        RegulationAnswerOutput(
            has_sufficient_evidence=answered,
            answer=answer,
            citations=[],
        )
    with pytest.raises(ValidationError):
        RegulationAnswerResponse(
            answered=answered,
            answer=answer,
            sources=[],
        )


def test_service_streams_only_the_validated_result() -> None:
    regulation_id = uuid4()
    chunk_id = uuid4()
    nodes = FakeNodes(
        {
            "answered": True,
            "answer": "依据现有法规，经营者应当如实披露商品信息。",
            "sources": [
                {
                    "chunkId": str(chunk_id),
                    "regulationId": str(regulation_id),
                    "title": "网络交易监督管理办法",
                    "pageNumber": 4,
                    "pageStart": 4,
                    "pageEnd": 5,
                    "quote": "经营者应当如实披露商品信息",
                }
            ],
        }
    )
    service = RegulationQaService.__new__(RegulationQaService)
    service.nodes = nodes
    service.rerank_enabled = False

    events = collect_stream(
        service,
        user_id=USER_ID,
        question="  经营者有什么披露义务？  ",
        top_k=5,
    )

    assert [event["data"]["phase"] for event in events[:7]] == [
        "guarding",
        "understanding",
        "retrieving",
        "screening-context",
        "generating",
        "validating",
        "screening-output",
    ]
    text = "".join(event["data"]["textDelta"] for event in events if event["type"] == "text-delta")
    assert text == nodes.result["answer"]
    assert events[-3]["type"] == "sources"
    assert events[-3]["data"]["sources"][0]["regulationId"] == str(regulation_id)
    assert events[-2] == {"type": "verified", "data": {"answered": True}}
    assert events[-1] == {"type": "done", "data": {}}
    assert nodes.input["question"] == "经营者有什么披露义务？"


def test_clarification_stream_reports_output_guard_phase() -> None:
    result = {
        "answered": False,
        "answer": "请说明具体业务场景。",
        "sources": [],
    }

    class ClarificationNodes(FakeNodes):
        async def understand_query(self, _state):
            return {"needs_clarification": True}

        async def build_safe_response(self, _state):
            return {"result": result}

    service = RegulationQaService.__new__(RegulationQaService)
    service.nodes = ClarificationNodes(result)
    service.rerank_enabled = False

    events = collect_stream(
        service,
        user_id=USER_ID,
        question="这个呢？",
        top_k=5,
    )

    phases = [event["data"]["phase"] for event in events if event["type"] == "phase"]
    assert phases == ["guarding", "understanding", "screening-output"]
    assert "retrieving" not in phases
    assert (
        "".join(event["data"]["textDelta"] for event in events if event["type"] == "text-delta")
        == result["answer"]
    )


def make_app(service):
    application = create_app(
        settings=SimpleNamespace(
            APP_NAME="AuditMind Test",
            CORS_ALLOWED_ORIGINS=[],
        )
    )
    application.dependency_overrides[get_jwt_user] = lambda: CurrentUser(
        user_id=USER_ID,
        username="admin",
    )
    application.dependency_overrides[get_regulation_qa_service] = lambda: service
    return application


def parse_sse(body: str):
    events = []
    for frame in body.strip().split("\n\n"):
        lines = frame.splitlines()
        events.append(
            {
                "type": lines[0].removeprefix("event: "),
                "data": json.loads(lines[1].removeprefix("data: ")),
            }
        )
    return events


def test_stream_endpoint_returns_sse_and_forwards_filters() -> None:
    async def stream(**kwargs):
        yield {"type": "phase", "data": {"phase": "retrieving"}}
        yield {"type": "text-delta", "data": {"textDelta": "answer"}}
        yield {"type": "sources", "data": {"sources": []}}
        yield {"type": "verified", "data": {"answered": True}}
        yield {"type": "done", "data": {}}

    service = SimpleNamespace(stream=stream)
    response = TestClient(make_app(service)).post(
        "/regulation/ask/stream",
        json={
            "question": "What applies?",
            "topK": 3,
            "category": "PUBLIC_KNOWLEDGE",
            "sourceType": "LAW",
            "jurisdiction": "CN",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    assert [event["type"] for event in parse_sse(response.text)] == [
        "phase",
        "text-delta",
        "sources",
        "verified",
        "done",
    ]


def test_stream_endpoint_hides_internal_errors() -> None:
    async def stream(**kwargs):
        if False:
            yield None
        raise RuntimeError("provider secret sk-do-not-leak")

    response = TestClient(make_app(SimpleNamespace(stream=stream))).post(
        "/regulation/ask/stream",
        json={"question": "What applies?"},
    )

    events = parse_sse(response.text)
    assert [event["type"] for event in events] == ["error", "done"]
    assert events[0]["data"]["code"] == 50000
    assert "sk-do-not-leak" not in response.text


def test_stream_endpoint_classifies_verification_failure_without_leaking() -> None:
    async def stream(**kwargs):
        if False:
            yield None
        raise RegulationCitationVerificationError("fabricated quote must not leak")

    response = TestClient(make_app(SimpleNamespace(stream=stream))).post(
        "/regulation/ask/stream",
        json={"question": "What applies?"},
    )

    events = parse_sse(response.text)
    assert [event["type"] for event in events] == ["error", "done"]
    assert events[0]["data"] == {
        "code": REGULATION_QA_VERIFICATION_ERROR_CODE,
        "message": REGULATION_QA_VERIFICATION_ERROR_MESSAGE,
    }
    assert "fabricated quote" not in response.text


def test_stream_request_validation_happens_before_streaming() -> None:
    service = SimpleNamespace(stream=AsyncMock())
    response = TestClient(make_app(service)).post(
        "/regulation/ask/stream",
        json={"question": "", "topK": 11},
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")


def test_service_sends_heartbeat_without_cancelling_slow_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SlowNodes(FakeNodes):
        async def guard_user_input(self, state):
            await asyncio.sleep(0.03)
            return await super().guard_user_input(state)

    service = RegulationQaService.__new__(RegulationQaService)
    service.nodes = SlowNodes({"answered": False, "answer": "依据不足", "sources": []})
    service.rerank_enabled = False
    monkeypatch.setattr(
        qa_service_module,
        "STREAM_HEARTBEAT_SECONDS",
        0.005,
    )

    events = collect_stream(
        service,
        user_id=USER_ID,
        question="question",
        top_k=5,
    )

    heartbeat_indexes = [
        index for index, event in enumerate(events) if event["type"] == "heartbeat"
    ]
    assert heartbeat_indexes
    validating_index = next(
        index
        for index, event in enumerate(events)
        if event == {"type": "phase", "data": {"phase": "validating"}}
    )
    assert all(
        event["type"] not in {"text-delta", "sources"} for event in events[:validating_index]
    )
    assert events[-1]["type"] == "done"


def test_disconnect_closes_service_generator_without_emitting() -> None:
    state = {"closed": False}

    async def stream(**kwargs):
        try:
            yield {"type": "phase", "data": {"phase": "retrieving"}}
            yield {"type": "text-delta", "data": {"textDelta": "unsafe"}}
        finally:
            state["closed"] = True

    async def collect():
        return [
            frame
            async for frame in regulation_answer_event_stream(
                request_body=SimpleNamespace(
                    question="question",
                    top_k=5,
                    category=None,
                    source_type=None,
                    jurisdiction=None,
                ),
                request=FakeRequest(True),
                service=SimpleNamespace(stream=stream),
                user_id=USER_ID,
            )
        ]

    assert asyncio.run(collect()) == []
    assert state["closed"] is True


def test_cancelled_stream_is_not_converted_to_error_event() -> None:
    async def stream(**kwargs):
        if False:
            yield None
        raise asyncio.CancelledError

    async def collect():
        return [
            frame
            async for frame in regulation_answer_event_stream(
                request_body=SimpleNamespace(
                    question="question",
                    top_k=5,
                    category=None,
                    source_type=None,
                    jurisdiction=None,
                ),
                request=FakeRequest(False),
                service=SimpleNamespace(stream=stream),
                user_id=USER_ID,
            )
        ]

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(collect())


def test_missing_validated_result_emits_only_safe_error_and_done() -> None:
    class MissingResultNodes(FakeNodes):
        async def validate_citations(self, _state):
            return {}

        async def guard_output(self, _state):
            return {}

    service = RegulationQaService.__new__(RegulationQaService)
    service.nodes = MissingResultNodes({})
    service.rerank_enabled = False

    async def collect():
        return [
            frame
            async for frame in regulation_answer_event_stream(
                request_body=SimpleNamespace(
                    question="question",
                    top_k=5,
                    category=None,
                    source_type=None,
                    jurisdiction=None,
                ),
                request=FakeRequest(False),
                service=service,
                user_id=USER_ID,
            )
        ]

    frames = asyncio.run(collect())
    assert all("event: text-delta" not in frame for frame in frames)
    assert all("event: sources" not in frame for frame in frames)
    assert frames[-2].startswith("event: error")
    assert frames[-1].startswith("event: done")


def test_retrieve_context_uses_query_understanding_output() -> None:
    search_service = SimpleNamespace(search=AsyncMock(return_value=[]))
    model = SimpleNamespace(with_structured_output=lambda *_args, **_kwargs: object())
    nodes = RegulationQaNodes(
        search_service=search_service,
        model=model,
        guardrails=SimpleNamespace(),
        query_understanding=SimpleNamespace(),
    )

    asyncio.run(
        nodes.retrieve_context(
            {
                "user_id": str(USER_ID),
                "question": "其中支付信息是必要的吗？",
                "search_query": "网上购物类 必要个人信息 支付信息",
                "top_k": 5,
                "history": [{"role": "user", "content": "购物类必要信息是什么？"}],
            }
        )
    )

    query = search_service.search.await_args.kwargs["query"]
    assert query == "网上购物类 必要个人信息 支付信息"
    assert "\n" not in query
