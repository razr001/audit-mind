import math
from functools import lru_cache

from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from app.core.config import get_settings

settings = get_settings()


class EmbeddingService:
    """统一生成文档向量，并在写入 ES 前验证返回结果。"""

    def __init__(
        self,
        *,
        model: Embeddings,
        dimensions: int,
    ) -> None:
        self.model = model
        self.dimensions = dimensions

    async def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        """批量生成文档向量，返回顺序与输入文本一致。"""
        if not texts:
            return []

        for index, text in enumerate(texts):
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"embedding text at index {index} must not be blank")

        vectors = await self.model.aembed_documents(texts)

        self._validate_vectors(
            vectors=vectors,
            expected_count=len(texts),
        )
        return vectors

    async def embed_query(
        self,
        text: str,
    ) -> list[float]:
        """生成用户查询向量。"""
        if not text.strip():
            raise ValueError("embedding query must not be blank")

        vector = await self.model.aembed_query(text)

        self._validate_vectors(
            vectors=[vector],
            expected_count=1,
        )
        return vector

    def _validate_vectors(
        self,
        *,
        vectors: list[list[float]],
        expected_count: int,
    ) -> None:
        """拦截数量、维度或数值异常，避免无效向量进入 ES。"""
        if len(vectors) != expected_count:
            raise RuntimeError("embedding result count does not match input count")

        for index, vector in enumerate(vectors):
            if len(vector) != self.dimensions:
                raise RuntimeError(
                    "embedding dimension mismatch: "
                    f"index={index}, expected={self.dimensions}, "
                    f"actual={len(vector)}"
                )

            if any(not math.isfinite(value) for value in vector):
                raise RuntimeError(f"embedding vector at index {index} contains invalid values")


@lru_cache
def get_embedding_service() -> EmbeddingService:
    """创建应用级 Embedding 客户端并复用底层 HTTP 连接。"""
    if not (
        settings.AI_EMBEDDING_BASE_URL.strip()
        and settings.AI_EMBEDDING_API_KEY.get_secret_value().strip()
        and settings.AI_EMBEDDING_MODEL.strip()
    ):
        raise RuntimeError("embedding model is not configured")

    model = OpenAIEmbeddings(
        model=settings.AI_EMBEDDING_MODEL,
        api_key=settings.AI_EMBEDDING_API_KEY,
        base_url=settings.AI_EMBEDDING_BASE_URL,
        dimensions=settings.AI_EMBEDDING_DIMENSIONS,
        chunk_size=settings.AI_EMBEDDING_BATCH_SIZE,
        timeout=settings.AI_TIMEOUT_SECONDS,
        max_retries=settings.AI_MAX_RETRIES,
        # 对兼容 OpenAI 协议的第三方模型，不依赖本地 tiktoken 模型名称。
        check_embedding_ctx_length=False,
    )

    return EmbeddingService(
        model=model,
        dimensions=settings.AI_EMBEDDING_DIMENSIONS,
    )
