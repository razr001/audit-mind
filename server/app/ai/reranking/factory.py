from functools import lru_cache

from langchain_core.documents.compressor import BaseDocumentCompressor

from app.ai.reranking.contract import RerankerProviderConfig
from app.ai.reranking.registry import create_registered_reranker
from app.core.config import Settings, get_settings


def create_reranker(settings: Settings) -> BaseDocumentCompressor | None:
    """Create the configured provider, or disable reranking when no model is set."""
    provider = settings.AI_RERANK_PROVIDER.strip().lower()
    if not provider:
        return None

    return create_registered_reranker(
        RerankerProviderConfig(
            provider=provider,
            model=settings.AI_RERANK_MODEL.strip(),
            base_url=settings.AI_RERANK_URL.strip(),
            api_key=settings.AI_RERANK_API_KEY,
            top_n=settings.AI_RERANK_TOP_N,
            timeout_seconds=settings.AI_RERANK_TIMEOUT_SECONDS,
            # Core configuration treats provider options as opaque data. Each
            # provider owns its option names, defaults and validation rules.
            options=dict(settings.AI_RERANK_OPTIONS),
        )
    )


@lru_cache
def get_reranker() -> BaseDocumentCompressor | None:
    """首次使用检索增强时才加载插件并创建 Reranker 客户端。"""
    return create_reranker(get_settings())
