from importlib.metadata import EntryPoint, entry_points

from langchain_core.documents.compressor import BaseDocumentCompressor

from app.ai.reranking.contract import (
    RERANK_PROVIDER_ENTRY_POINT_GROUP,
    RerankerConfigurationError,
    RerankerProviderConfig,
)


def _normalize_provider_name(value: str) -> str:
    """Apply the same lookup rules to environment and entry-point names."""
    return value.strip().lower()


def _external_entry_points() -> list[EntryPoint]:
    """Read plugin metadata without importing any provider implementation."""
    return list(entry_points(group=RERANK_PROVIDER_ENTRY_POINT_GROUP))


def create_registered_reranker(
    config: RerankerProviderConfig,
) -> BaseDocumentCompressor:
    """Construct a first- or third-party LangChain compressor by entry point."""
    provider = _normalize_provider_name(config.provider)
    # Resolve metadata once. Besides avoiding duplicate work, this guarantees
    # that availability and duplicate checks use one consistent snapshot.
    provider_entry_points = _external_entry_points()
    matching_entry_points = [
        entry_point
        for entry_point in provider_entry_points
        if _normalize_provider_name(entry_point.name) == provider
    ]

    if len(matching_entry_points) > 1:
        raise RerankerConfigurationError(
            f"duplicate reranker provider entry point: {provider}"
        )
    if not matching_entry_points:
        available_names = {
            _normalize_provider_name(entry_point.name)
            for entry_point in provider_entry_points
        }
        available = ", ".join(sorted(available_names)) or "none"
        raise RerankerConfigurationError(
            f"unknown reranker provider '{provider}'; available providers: {available}"
        )

    # Import only the selected provider. A broken unrelated plugin must not
    # prevent AuditMind from starting with another configured provider.
    factory = matching_entry_points[0].load()
    if not callable(factory):
        raise RerankerConfigurationError(
            f"reranker provider factory is not callable: {provider}"
        )

    compressor = factory(config)
    if not isinstance(compressor, BaseDocumentCompressor):
        raise RerankerConfigurationError(
            f"reranker provider '{provider}' did not return BaseDocumentCompressor"
        )
    return compressor
