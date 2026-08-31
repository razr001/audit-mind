from collections.abc import Sequence

from langchain_core.callbacks import Callbacks
from langchain_core.documents import Document
from langchain_core.documents.compressor import BaseDocumentCompressor
from pydantic import ConfigDict, field_validator

from app.ai.reranking.contract import RerankerError


class ErrorMappingDocumentCompressor(BaseDocumentCompressor):
    """Normalize explicitly declared SDK failures without hiding code defects.

    Native LangChain integrations use provider-specific exception classes. A
    plugin factory can wrap such a compressor and list only the failures that
    mean "temporarily unavailable". AuditMind can then safely fall back to RRF,
    while TypeError, AttributeError and other programming errors still escape.
    """

    compressor: BaseDocumentCompressor
    provider_name: str
    recoverable_exceptions: tuple[type[Exception], ...]

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_validator("recoverable_exceptions")
    @classmethod
    def validate_recoverable_exceptions(
        cls,
        value: tuple[type[Exception], ...],
    ) -> tuple[type[Exception], ...]:
        if not value:
            raise ValueError("at least one recoverable provider exception is required")
        if Exception in value or BaseException in value:
            raise ValueError("Exception and BaseException cannot be recoverable provider errors")
        return value

    def compress_documents(
        self,
        documents: Sequence[Document],
        query: str,
        callbacks: Callbacks | None = None,
    ) -> Sequence[Document]:
        try:
            return self._tag_provider(
                self.compressor.compress_documents(
                    documents=documents,
                    query=query,
                    callbacks=callbacks,
                ),
                submitted_count=len(documents),
            )
        except self.recoverable_exceptions as exc:
            raise RerankerError(
                f"{self.provider_name} rerank provider request failed: "
                f"{type(exc).__name__}"
            ) from exc

    async def acompress_documents(
        self,
        documents: Sequence[Document],
        query: str,
        callbacks: Callbacks | None = None,
    ) -> Sequence[Document]:
        try:
            return self._tag_provider(
                await self.compressor.acompress_documents(
                    documents=documents,
                    query=query,
                    callbacks=callbacks,
                ),
                submitted_count=len(documents),
            )
        except self.recoverable_exceptions as exc:
            raise RerankerError(
                f"{self.provider_name} rerank provider request failed: "
                f"{type(exc).__name__}"
            ) from exc

    def _tag_provider(
        self,
        documents: Sequence[Document],
        *,
        submitted_count: int,
    ) -> list[Document]:
        """Add AuditMind observability metadata without mutating provider output."""
        return [
            Document(
                page_content=document.page_content,
                metadata={
                    **document.metadata,
                    "rerank_provider": self.provider_name,
                    "rerank_submitted_count": submitted_count,
                },
                id=document.id,
            )
            for document in documents
        ]
