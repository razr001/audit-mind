from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from langchain_core.documents.compressor import BaseDocumentCompressor
from pydantic import SecretStr

RERANK_PROVIDER_ENTRY_POINT_GROUP: Final = "auditmind.rerankers"


class RerankerError(RuntimeError):
    """Expected provider or response error for which retrieval may safely degrade."""

    def __init__(self, message: str, *, request_id: str | None = None) -> None:
        super().__init__(message)
        self.request_id = request_id


class RerankerConfigurationError(ValueError):
    """Raised during startup when a configured reranker cannot be constructed."""


@dataclass(frozen=True, slots=True)
class RerankerProviderConfig:
    """Stable configuration passed to built-in and third-party provider factories."""

    provider: str
    model: str
    base_url: str = ""
    api_key: SecretStr = field(default_factory=lambda: SecretStr(""))
    top_n: int = 10
    timeout_seconds: int = 30
    options: dict[str, Any] = field(default_factory=dict)


class RerankerFactory(Protocol):
    """Factory signature exposed through the ``auditmind.rerankers`` entry point."""

    def __call__(self, config: RerankerProviderConfig) -> BaseDocumentCompressor: ...
