import asyncio
from collections.abc import Sequence
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.documents import Document
from langchain_core.documents.compressor import BaseDocumentCompressor
from pydantic import SecretStr

from app.ai.regulation_qa.nodes import RegulationQaNodes
from app.ai.reranking import registry
from app.ai.reranking.adapters import ErrorMappingDocumentCompressor
from app.ai.reranking.contract import (
    RerankerConfigurationError,
    RerankerError,
    RerankerProviderConfig,
)
from app.ai.reranking.document_mapper import (
    chunks_to_documents,
    documents_to_chunks,
)
from app.infrastructure.http_client import AsyncHttpClient
from app.plugin.reranking.bailian import BailianReranker, create_reranker
from app.services.regulation_qa_service import RegulationQaService


def _search_item(*, content: str, score: float):
    return SimpleNamespace(
        chunk_id=uuid4(),
        regulation_id=uuid4(),
        title="测试法规",
        page_number=1,
        page_start=1,
        page_end=1,
        content=content,
        score=score,
    )


def _nodes(*, search_service, reranker=None) -> RegulationQaNodes:
    model = SimpleNamespace(with_structured_output=lambda *_args, **_kwargs: object())
    return RegulationQaNodes(
        search_service=search_service,
        model=model,
        guardrails=SimpleNamespace(),
        query_understanding=SimpleNamespace(),
        reranker=reranker,
        rerank_candidate_count=30,
    )


def _state(*, top_k: int = 2) -> dict:
    return {
        "user_id": str(uuid4()),
        "question": "其中需要收集什么信息？",
        "standalone_question": "网上购物类需要收集哪些必要个人信息？",
        "search_query": "网上购物 必要个人信息",
        "top_k": top_k,
        "history": [],
    }


def _bailian_reranker(**overrides) -> BailianReranker:
    values = {
        "base_url": "https://example.com/v1/reranks",
        "api_key": SecretStr("test-key"),
        "model_name": "qwen3-rerank",
    }
    values.update(overrides)
    return BailianReranker(**values)


def test_disabled_reranker_does_not_expand_retrieval() -> None:
    search = SimpleNamespace(search=AsyncMock(return_value=[]))

    asyncio.run(_nodes(search_service=search).retrieve_context(_state()))

    assert search.search.await_args.kwargs["top_k"] == 2


def test_enabled_reranker_expands_candidates_and_uses_standalone_question() -> None:
    class FakeReranker:
        def __init__(self) -> None:
            self.acompress_documents = AsyncMock(side_effect=self._rerank)

        async def _rerank(self, *, documents, **_kwargs):
            return list(reversed(documents))

    search = SimpleNamespace(
        search=AsyncMock(
            return_value=[
                _search_item(content="候选一", score=0.8),
                _search_item(content="候选二", score=0.7),
                _search_item(content="候选三", score=0.6),
            ]
        )
    )
    reranker = FakeReranker()
    nodes = _nodes(search_service=search, reranker=reranker)
    state = _state()
    retrieved = asyncio.run(nodes.retrieve_context(state))
    state["chunks"] = retrieved["chunks"]

    result = asyncio.run(nodes.rerank_context(state))

    assert search.search.await_args.kwargs["top_k"] == 30
    assert (
        reranker.acompress_documents.await_args.kwargs["query"]
        == state["standalone_question"]
    )
    assert [chunk["content"] for chunk in result["chunks"]] == ["候选三", "候选二"]


def test_native_langchain_compressor_can_be_used_without_provider_adapter() -> None:
    class NativeLangChainCompressor(BaseDocumentCompressor):
        def compress_documents(
            self,
            documents: Sequence[Document],
            query: str,
            callbacks=None,
        ) -> Sequence[Document]:
            del query, callbacks
            return list(reversed(documents))

    state = _state(top_k=1)
    state["chunks"] = [
        {"chunk_id": "1", "title": "法规", "content": "第一项"},
        {"chunk_id": "2", "title": "法规", "content": "第二项"},
    ]
    nodes = _nodes(
        search_service=SimpleNamespace(),
        reranker=NativeLangChainCompressor(),
    )

    result = asyncio.run(nodes.rerank_context(state))

    assert [chunk["chunk_id"] for chunk in result["chunks"]] == ["2"]


def test_external_entry_point_factory_returns_langchain_compressor(monkeypatch) -> None:
    class PluginCompressor(BaseDocumentCompressor):
        def compress_documents(
            self,
            documents: Sequence[Document],
            query: str,
            callbacks=None,
        ) -> Sequence[Document]:
            del query, callbacks
            return documents

    factory = lambda _config: PluginCompressor()  # noqa: E731
    entry_point = SimpleNamespace(name=" Example ", load=lambda: factory)
    monkeypatch.setattr(
        registry,
        "entry_points",
        lambda *, group: [entry_point]
        if group == "auditmind.rerankers"
        else [],
    )

    compressor = registry.create_registered_reranker(
        RerankerProviderConfig(provider="EXAMPLE", model="example-model")
    )

    assert isinstance(compressor, BaseDocumentCompressor)


def test_bailian_is_discovered_through_the_public_entry_point() -> None:
    entry_points = [
        entry_point
        for entry_point in registry._external_entry_points()
        if entry_point.name == "bailian"
    ]

    assert len(entry_points) == 1
    assert (
        entry_points[0].value
        == "app.plugin.reranking.bailian:create_reranker"
    )
    compressor = registry.create_registered_reranker(
        RerankerProviderConfig(
            provider="bailian",
            model="qwen3-rerank",
            base_url="https://example.com/v1/reranks",
        )
    )
    assert isinstance(compressor, BailianReranker)


def test_unselected_external_entry_point_is_not_imported(monkeypatch) -> None:
    selected = _bailian_reranker()
    selected_entry_point = SimpleNamespace(
        name="selected",
        load=lambda: lambda _config: selected,
    )
    unrelated = SimpleNamespace(
        name="broken-plugin",
        load=lambda: (_ for _ in ()).throw(ModuleNotFoundError("optional dependency")),
    )
    monkeypatch.setattr(
        registry,
        "_external_entry_points",
        lambda: [selected_entry_point, unrelated],
    )

    compressor = registry.create_registered_reranker(
        RerankerProviderConfig(provider="selected", model="model")
    )

    assert compressor is selected


def test_error_mapping_adapter_only_converts_declared_provider_errors() -> None:
    class ProviderUnavailableError(RuntimeError):
        pass

    class FailingCompressor(BaseDocumentCompressor):
        failure_kind: str

        def compress_documents(self, documents, query, callbacks=None):
            del documents, query, callbacks
            if self.failure_kind == "provider":
                raise ProviderUnavailableError("offline")
            raise AttributeError("bug")

    expected = ErrorMappingDocumentCompressor(
        compressor=FailingCompressor(failure_kind="provider"),
        provider_name="example",
        recoverable_exceptions=(ProviderUnavailableError,),
    )
    unexpected = ErrorMappingDocumentCompressor(
        compressor=FailingCompressor(failure_kind="bug"),
        provider_name="example",
        recoverable_exceptions=(ProviderUnavailableError,),
    )

    with pytest.raises(RerankerError):
        expected.compress_documents([Document("candidate")], "query")
    with pytest.raises(AttributeError, match="bug"):
        unexpected.compress_documents([Document("candidate")], "query")

    with pytest.raises(ValueError, match="cannot be recoverable"):
        ErrorMappingDocumentCompressor(
            compressor=FailingCompressor(failure_kind="bug"),
            provider_name="unsafe",
            recoverable_exceptions=(Exception,),
        )


def test_error_mapping_adapter_adds_provider_without_mutating_source() -> None:
    class SuccessfulCompressor(BaseDocumentCompressor):
        def compress_documents(self, documents, query, callbacks=None):
            del query, callbacks
            return documents

    source = Document("candidate", metadata={"chunk_id": "1"})
    adapter = ErrorMappingDocumentCompressor(
        compressor=SuccessfulCompressor(),
        provider_name="cohere",
        recoverable_exceptions=(TimeoutError,),
    )

    result = adapter.compress_documents([source], "query")

    assert result[0].metadata["rerank_provider"] == "cohere"
    assert result[0].metadata["rerank_submitted_count"] == 1
    assert result[0].metadata["chunk_id"] == "1"
    assert "rerank_provider" not in source.metadata


def test_langchain_relevance_score_is_normalized_to_rerank_score() -> None:
    chunks = [{"chunk_id": "1", "title": "法规", "content": "候选"}]
    documents = chunks_to_documents(chunks)
    documents[0].metadata["relevance_score"] = 0.87

    mapped = documents_to_chunks(documents, chunks)

    assert mapped[0]["rerank_score"] == 0.87


def test_rerank_failure_falls_back_to_original_rrf_order() -> None:
    reranker = SimpleNamespace(
        acompress_documents=AsyncMock(side_effect=RerankerError("provider unavailable")),
    )
    nodes = _nodes(search_service=SimpleNamespace(), reranker=reranker)
    state = _state()
    state["chunks"] = [
        {"chunk_id": "1", "title": "法规", "content": "RRF 第一项"},
        {"chunk_id": "2", "title": "法规", "content": "RRF 第二项"},
        {"chunk_id": "3", "title": "法规", "content": "RRF 第三项"},
    ]

    result = asyncio.run(nodes.rerank_context(state))

    assert [chunk["content"] for chunk in result["chunks"]] == [
        "RRF 第一项",
        "RRF 第二项",
    ]


def test_rerank_response_indices_are_mapped_back_to_original_chunks() -> None:
    chunks = [
        {"chunk_id": "1", "title": "法规", "content": "第一项", "score": 0.9},
        {"chunk_id": "2", "title": "法规", "content": "第二项", "score": 0.8},
        {"chunk_id": "3", "title": "法规", "content": "第三项", "score": 0.7},
    ]
    reranker = _bailian_reranker(top_n=2)
    documents = chunks_to_documents(chunks)
    reranked_documents = reranker._map_results(
        body={
            "results": [
                {"index": 2, "relevance_score": 0.98},
                {"index": 0, "relevance_score": 0.75},
            ]
        },
        headers={"x-request-id": "request-1"},
        documents=documents,
    )
    result = documents_to_chunks(reranked_documents, chunks)

    assert [chunk["content"] for chunk in result] == ["第三项", "第一项"]
    assert [chunk["rerank_score"] for chunk in result] == [0.98, 0.75]
    # 原始 RRF 分数继续保留，方便比较两阶段排序和排障。
    assert [chunk["score"] for chunk in result] == [0.7, 0.9]
    assert reranked_documents[0].metadata["rerank_submitted_count"] == 3


def test_bailian_dashscope_request_and_response_envelopes_are_supported() -> None:
    reranker = _bailian_reranker(
        base_url=(
            "https://workspace.example.com/api/v1/services/"
            "rerank/text-rerank/text-rerank"
        ),
        top_n=1,
    )
    documents = [Document("候选内容", metadata={"chunk_id": "1"})]

    payload = reranker._payload(query="查询", documents=documents)
    reranked = reranker._map_results(
        body={
            "output": {
                "results": [{"index": 0, "relevance_score": 0.91}],
            },
            "usage": {"total_tokens": 12},
            "request_id": "dashscope-request",
        },
        headers={},
        documents=documents,
    )

    assert payload["input"] == {
        "query": "查询",
        "documents": ["候选内容"],
    }
    assert payload["parameters"]["top_n"] == 1
    assert payload["parameters"]["return_documents"] is False
    assert reranked[0].metadata["rerank_score"] == 0.91
    assert reranked[0].metadata["rerank_request_id"] == "dashscope-request"


def test_bailian_openai_compatible_request_remains_flat() -> None:
    reranker = _bailian_reranker(
        base_url="https://workspace.example.com/compatible-api/v1/reranks",
    )

    payload = reranker._payload(
        query="查询",
        documents=[Document("候选内容")],
    )

    assert payload["query"] == "查询"
    assert payload["documents"] == ["候选内容"]
    assert "input" not in payload


def test_rerank_response_rejects_duplicate_indices() -> None:
    reranker = _bailian_reranker(top_n=2)
    with pytest.raises(RerankerError) as caught:
        reranker._map_results(
            body={
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.8},
                ]
            },
            headers={"x-request-id": "request-2"},
            documents=[Document("候选一"), Document("候选二")],
        )
    assert caught.value.request_id == "request-2"


def test_rerank_document_contains_title_and_content() -> None:
    document = chunks_to_documents(
        [
            {
                "chunk_id": "chunk-1",
                "title": "个人信息保护法",
                "content": "处理个人信息应当具有明确目的。",
            }
        ]
    )[0].page_content

    assert "个人信息保护法" in document
    assert "处理个人信息应当具有明确目的。" in document


def test_qwen_response_id_and_usage_are_extracted() -> None:
    response = SimpleNamespace(headers={"x-request-id": "header-request"})
    body = {"id": "qwen-request", "usage": {"total_tokens": 321}}

    assert BailianReranker._request_id(response.headers, body) == "qwen-request"
    assert BailianReranker._total_tokens(body) == 321


def test_sync_response_header_request_id_is_case_insensitive() -> None:
    assert (
        BailianReranker._request_id(
            {"X-Request-Id": "header-request"},
            {},
        )
        == "header-request"
    )


def test_bailian_omits_authorization_header_when_api_key_is_empty() -> None:
    without_key = BailianReranker(
        base_url="http://localhost:8000/v1/reranks",
        api_key=SecretStr(""),
        model_name="local-reranker",
    )

    assert "Authorization" not in without_key._headers()
    assert _bailian_reranker()._headers()["Authorization"] == "Bearer test-key"


def test_invalid_json_preserves_header_request_id() -> None:
    class InvalidResponse:
        headers = {"x-request-id": "header-request"}

        async def json(self, *, content_type):
            assert content_type is None
            raise ValueError("invalid JSON")

    with pytest.raises(RerankerError) as caught:
        asyncio.run(BailianReranker._read_async_json(InvalidResponse()))

    assert caught.value.request_id == "header-request"


@pytest.mark.parametrize(
    "results",
    [
        [{"index": 0, "relevance_score": 1.1}],
        [],
    ],
)
def test_rerank_response_rejects_invalid_score_or_incomplete_count(results) -> None:
    reranker = _bailian_reranker(top_n=1)
    with pytest.raises(RerankerError):
        reranker._map_results(
            body={"results": results},
            headers={"x-request-id": "request-3"},
            documents=[Document("候选")],
        )


def test_shared_http_client_reuses_and_closes_session() -> None:
    http_client = AsyncHttpClient()

    async def exercise_session() -> None:
        first = await http_client.get_session()
        second = await http_client.get_session()
        assert first is second
        await http_client.close()
        assert first.closed is True

    asyncio.run(exercise_session())


def test_rerank_input_respects_document_and_total_character_budgets() -> None:
    reranker = _bailian_reranker(
        max_document_characters=20,
        max_total_characters=30,
    )
    documents = [
        Document("甲" * 100, metadata={"chunk_id": "1"}),
        Document("乙" * 100, metadata={"chunk_id": "2"}),
    ]

    prepared = reranker._prepare_documents(
        query="问题",
        documents=documents,
    )

    assert [len(document.page_content) for document in prepared] == [20, 6]
    assert all(len(document.page_content) <= 20 for document in prepared)
    assert sum(len("问题") + len(document.page_content) for document in prepared) <= 30


def test_long_documents_continue_filling_budget_after_per_document_truncation() -> None:
    reranker = _bailian_reranker(
        max_document_characters=20,
        max_total_characters=50,
    )
    documents = [
        Document("甲" * 100, metadata={"chunk_id": "1"}),
        Document("乙" * 100, metadata={"chunk_id": "2"}),
    ]

    prepared = reranker._prepare_documents(query="问题", documents=documents)

    assert [len(document.page_content) for document in prepared] == [20, 20]
    assert sum(len("问题") + len(document.page_content) for document in prepared) <= 50


def test_bailian_validates_its_own_character_budget_options() -> None:
    with pytest.raises(RerankerConfigurationError, match="max_total_characters"):
        create_reranker(
            RerankerProviderConfig(
                provider="bailian",
                model="qwen3-rerank",
                base_url="https://example.com/v1/reranks",
                api_key=SecretStr("test-key"),
                options={
                    "max_document_characters": 5_000,
                    "max_total_characters": 4_000,
                },
            )
        )


def test_bailian_rejects_unknown_provider_options() -> None:
    with pytest.raises(RerankerConfigurationError, match="unknown bailian"):
        create_reranker(
            RerankerProviderConfig(
                provider="bailian",
                model="local-reranker",
                base_url="http://localhost:8000/v1/reranks",
                options={"max_document_character": 5_000},
            )
        )


def test_unexpected_reranker_bug_is_not_silently_downgraded() -> None:
    reranker = SimpleNamespace(
        acompress_documents=AsyncMock(side_effect=AttributeError("programming bug")),
    )
    nodes = _nodes(search_service=SimpleNamespace(), reranker=reranker)
    state = _state()
    state["chunks"] = [{"chunk_id": "1", "title": "法规", "content": "候选"}]

    with pytest.raises(AttributeError, match="programming bug"):
        asyncio.run(nodes.rerank_context(state))


def test_enabled_rerank_reports_its_own_stream_phase() -> None:
    result = {
        "answered": False,
        "answer": "现有法规知识中未找到充分依据。",
        "sources": [],
    }

    class RerankNodes:
        async def guard_user_input(self, _state):
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
            return {"result": result}

        async def guard_output(self, _state):
            return {"result": result}

    service = RegulationQaService.__new__(RegulationQaService)
    service.nodes = RerankNodes()
    service.rerank_enabled = True

    async def collect_phases() -> list[str]:
        events = [
            event
            async for event in service.stream(
                user_id=uuid4(),
                question="问题",
                top_k=2,
            )
        ]
        return [event["data"]["phase"] for event in events if event["type"] == "phase"]

    assert asyncio.run(collect_phases()) == [
        "guarding",
        "understanding",
        "retrieving",
        "reranking",
        "screening-context",
        "generating",
        "validating",
        "screening-output",
    ]
