import asyncio
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import aiohttp
from langchain_core.callbacks import Callbacks
from langchain_core.documents import Document
from langchain_core.documents.compressor import BaseDocumentCompressor
from pydantic import ConfigDict, Field, SecretStr

from app.ai.reranking.contract import (
    RerankerConfigurationError,
    RerankerError,
    RerankerProviderConfig,
)
from app.infrastructure.http_client import AsyncHttpClient, outbound_http_client

DEFAULT_RERANK_INSTRUCTION = (
    "Given a regulatory compliance question, retrieve passages that directly "
    "answer the question or specify applicable obligations, prohibitions, "
    "conditions, exceptions, and consequences."
)


class BailianReranker(BaseDocumentCompressor):
    """LangChain compressor for Bailian's Qwen-compatible ``/reranks`` API."""

    base_url: str
    api_key: SecretStr
    model_name: str
    top_n: int = Field(default=10, ge=1)
    timeout_seconds: int = Field(default=30, ge=1)
    max_document_characters: int = Field(default=4_000, ge=1)
    max_total_characters: int = Field(default=60_000, ge=1)
    api_mode: str = "auto"
    http_client: AsyncHttpClient = Field(
        default=outbound_http_client,
        exclude=True,
        repr=False,
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def compress_documents(
        self,
        documents: Sequence[Document],
        query: str,
        callbacks: Callbacks | None = None,
    ) -> Sequence[Document]:
        """Provide the synchronous LangChain contract for non-ASGI consumers."""
        del callbacks
        prepared = self._prepare_documents(query=query, documents=documents)
        if not prepared:
            return []
        body, headers = self._post_sync(query=query, documents=prepared)
        return self._map_results(
            body=body,
            headers=headers,
            documents=prepared,
        )

    async def acompress_documents(
        self,
        documents: Sequence[Document],
        query: str,
        callbacks: Callbacks | None = None,
    ) -> Sequence[Document]:
        """Use the application's shared async connection pool inside FastAPI."""
        del callbacks
        prepared = self._prepare_documents(query=query, documents=documents)
        if not prepared:
            return []

        payload = self._payload(query=query, documents=prepared)
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            session = await self.http_client.get_session()
            async with session.post(
                self.base_url,
                json=payload,
                headers=self._headers(),
                timeout=timeout,
            ) as response:
                body = await self._read_async_json(response)
                request_id = self._request_id(response.headers, body)
                if response.status >= 400:
                    raise RerankerError(
                        f"rerank provider returned HTTP {response.status}",
                        request_id=request_id,
                    )
                return self._map_results(
                    body=body,
                    headers=response.headers,
                    documents=prepared,
                )
        except RerankerError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise RerankerError(
                f"rerank provider request failed: {type(exc).__name__}"
            ) from exc

    def _post_sync(
        self,
        *,
        query: str,
        documents: Sequence[Document],
    ) -> tuple[dict[str, Any], Mapping[str, str]]:
        request = Request(
            self.base_url,
            data=json.dumps(self._payload(query=query, documents=documents)).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                headers = dict(response.headers.items())
                body = self._decode_json(response.read(), headers=headers)
                return body, headers
        except HTTPError as exc:
            headers = dict(exc.headers.items()) if exc.headers else {}
            body = self._decode_json(exc.read(), headers=headers)
            raise RerankerError(
                f"rerank provider returned HTTP {exc.code}",
                request_id=self._request_id(headers, body),
            ) from exc
        except (URLError, TimeoutError) as exc:
            raise RerankerError(
                f"rerank provider request failed: {type(exc).__name__}"
            ) from exc

    def _payload(
        self,
        *,
        query: str,
        documents: Sequence[Document],
    ) -> dict[str, Any]:
        document_contents = [document.page_content for document in documents]
        top_n = min(self.top_n, len(documents))
        if self._uses_dashscope_api():
            # Bailian's native workspace endpoint uses the DashScope envelope.
            # The OpenAI-compatible /reranks endpoint uses flat fields instead.
            return {
                "model": self.model_name,
                "input": {
                    "query": query,
                    "documents": document_contents,
                },
                "parameters": {
                    "top_n": top_n,
                    "return_documents": False,
                    "instruct": DEFAULT_RERANK_INSTRUCTION,
                },
            }
        return {
            "model": self.model_name,
            "query": query,
            "documents": document_contents,
            "top_n": top_n,
            "instruct": DEFAULT_RERANK_INSTRUCTION,
        }

    def _uses_dashscope_api(self) -> bool:
        if self.api_mode == "dashscope":
            return True
        if self.api_mode == "openai":
            return False
        return "/api/v1/services/rerank/" in self.base_url.lower()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        api_key = self.api_key.get_secret_value().strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _prepare_documents(
        self,
        *,
        query: str,
        documents: Sequence[Document],
    ) -> list[Document]:
        """Apply Bailian request budgets while preserving document identity metadata."""
        prepared: list[Document] = []
        estimated_characters = 0
        for source in documents:
            remaining = self.max_total_characters - estimated_characters - len(query)
            if remaining <= 0:
                break
            # Compare the remaining budget with the truncated length, not the
            # original Chunk length. A very long source may still fit after the
            # provider-specific per-document limit is applied.
            content = source.page_content[: min(self.max_document_characters, remaining)]
            prepared.append(
                Document(
                    page_content=content,
                    metadata=dict(source.metadata),
                    id=source.id,
                )
            )
            estimated_characters += len(query) + len(content)

        if documents and not prepared:
            raise RerankerError("rerank input exceeds the configured character budget")
        return prepared

    @staticmethod
    async def _read_async_json(response: aiohttp.ClientResponse) -> dict[str, Any]:
        try:
            body = await response.json(content_type=None)
        except (ValueError, aiohttp.ClientError) as exc:
            raise RerankerError(
                "rerank provider returned invalid JSON",
                request_id=response.headers.get("x-request-id"),
            ) from exc
        if not isinstance(body, dict):
            raise RerankerError(
                "rerank provider returned a non-object response",
                request_id=response.headers.get("x-request-id"),
            )
        return body

    @staticmethod
    def _decode_json(data: bytes, *, headers: Mapping[str, str]) -> dict[str, Any]:
        try:
            body = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RerankerError(
                "rerank provider returned invalid JSON",
                request_id=BailianReranker._header_value(headers, "x-request-id"),
            ) from exc
        if not isinstance(body, dict):
            raise RerankerError(
                "rerank provider returned a non-object response",
                request_id=BailianReranker._header_value(headers, "x-request-id"),
            )
        return body

    @staticmethod
    def _request_id(headers: Mapping[str, str], body: dict[str, Any]) -> str | None:
        value = (
            body.get("id")
            or body.get("request_id")
            or BailianReranker._header_value(headers, "x-request-id")
        )
        return str(value) if value else None

    @staticmethod
    def _header_value(headers: Mapping[str, str], name: str) -> str | None:
        """Read headers from both case-insensitive clients and plain dictionaries."""
        normalized_name = name.lower()
        for key, value in headers.items():
            if key.lower() == normalized_name:
                return value
        return None

    @staticmethod
    def _total_tokens(body: dict[str, Any]) -> int | None:
        usage = body.get("usage")
        if not isinstance(usage, dict):
            return None
        value = usage.get("total_tokens")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

    def _map_results(
        self,
        *,
        body: dict[str, Any],
        headers: Mapping[str, str],
        documents: Sequence[Document],
    ) -> list[Document]:
        request_id = self._request_id(headers, body)
        results = body.get("results")
        if results is None:
            # Native DashScope responses wrap ranked items in output.results;
            # OpenAI-compatible responses expose results at the root.
            output = body.get("output")
            if isinstance(output, dict):
                results = output.get("results")
        if not isinstance(results, list):
            raise RerankerError(
                "rerank response does not contain a results array",
                request_id=request_id,
            )
        expected_count = min(self.top_n, len(documents))
        if len(results) != expected_count:
            raise RerankerError(
                "rerank response result count does not match top_n",
                request_id=request_id,
            )

        reranked: list[Document] = []
        seen_indices: set[int] = set()
        total_tokens = self._total_tokens(body)
        for item in results:
            if not isinstance(item, dict):
                raise RerankerError("rerank result is not an object", request_id=request_id)
            index = item.get("index")
            score = item.get("relevance_score")
            if isinstance(index, bool) or not isinstance(index, int):
                raise RerankerError(
                    "rerank result contains an invalid index",
                    request_id=request_id,
                )
            if index < 0 or index >= len(documents) or index in seen_indices:
                raise RerankerError(
                    "rerank result index is duplicate or out of range",
                    request_id=request_id,
                )
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise RerankerError(
                    "rerank result contains an invalid score",
                    request_id=request_id,
                )
            score_value = float(score)
            if not math.isfinite(score_value) or not 0.0 <= score_value <= 1.0:
                raise RerankerError(
                    "rerank result score is outside the supported range",
                    request_id=request_id,
                )

            seen_indices.add(index)
            source = documents[index]
            reranked.append(
                Document(
                    page_content=source.page_content,
                    metadata={
                        **source.metadata,
                        "rerank_score": score_value,
                        "rerank_provider": "bailian",
                        "rerank_submitted_count": len(documents),
                        "rerank_request_id": request_id,
                        "rerank_total_tokens": total_tokens,
                    },
                    id=source.id,
                )
            )
        return reranked


def _option_int(config: RerankerProviderConfig, name: str, default: int) -> int:
    value = config.options.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RerankerConfigurationError(f"reranker option '{name}' must be a positive integer")
    return value


def create_reranker(config: RerankerProviderConfig) -> BaseDocumentCompressor:
    """Create Bailian through the same entry-point contract used by external plugins."""
    if not config.base_url or not config.model:
        raise RerankerConfigurationError(
            "bailian reranker requires base_url and model"
        )
    allowed_options = {
        "api_mode",
        "max_document_characters",
        "max_total_characters",
    }
    unknown_options = set(config.options) - allowed_options
    if unknown_options:
        names = ", ".join(sorted(unknown_options))
        raise RerankerConfigurationError(f"unknown bailian reranker options: {names}")
    max_document_characters = _option_int(
        config,
        "max_document_characters",
        4_000,
    )
    max_total_characters = _option_int(
        config,
        "max_total_characters",
        60_000,
    )
    if max_total_characters < max_document_characters:
        raise RerankerConfigurationError(
            "bailian option 'max_total_characters' must be greater than or equal "
            "to 'max_document_characters'"
        )
    api_mode = config.options.get("api_mode", "auto")
    if api_mode not in {"auto", "dashscope", "openai"}:
        raise RerankerConfigurationError(
            "bailian option 'api_mode' must be auto, dashscope, or openai"
        )
    return BailianReranker(
        base_url=config.base_url,
        api_key=config.api_key,
        model_name=config.model,
        top_n=config.top_n,
        timeout_seconds=config.timeout_seconds,
        max_document_characters=max_document_characters,
        max_total_characters=max_total_characters,
        api_mode=api_mode,
    )
