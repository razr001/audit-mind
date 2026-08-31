from elasticsearch import AsyncElasticsearch

from app.core.config import get_settings

settings = get_settings()


class ElasticsearchClient:
    """共享 Elasticsearch 连接；具体索引行为由各领域 VectorStore 封装。"""

    def __init__(self) -> None:
        self.client = AsyncElasticsearch(
            hosts=[settings.ELASTICSEARCH_URL],
            api_key=settings.ELASTICSEARCH_API_KEY.get_secret_value(),
        )

    async def ping(self) -> bool:
        return await self.client.ping()

    async def close(self) -> None:
        await self.client.close()


es_client = ElasticsearchClient()
