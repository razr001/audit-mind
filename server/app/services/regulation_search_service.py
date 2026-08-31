from datetime import date
from uuid import UUID

from fastapi import Depends

from app.ai.embedding import EmbeddingService, get_embedding_service
from app.core.error_codes import INVALID_REGULATION_SEARCH_QUERY
from app.core.exceptions import BusinessException
from app.core.text_validation import contains_control_character
from app.infrastructure.db.unit_of_work import UnitOfWork, get_uow
from app.infrastructure.regulation_vector_store import (
    RegulationVectorStore,
    regulation_vector_store,
)
from app.models.regulation import KnowledgeCategory, RegulationSourceType
from app.repositories.regulation_chunk_repository import RegulationChunkRepository
from app.schemas.regulation_search import RegulationSearchItem


class RegulationSearchService:
    """生成查询向量，并把 Elasticsearch 结果转换为 API Schema。"""

    def __init__(
        self,
        *,
        embedding: EmbeddingService,
        vector_store: RegulationVectorStore,
        uow: UnitOfWork,
        chunk_repository: RegulationChunkRepository,
    ) -> None:
        self.embedding = embedding
        self.vector_store = vector_store
        self.uow = uow
        self.chunk_repository = chunk_repository

    async def search(
        self,
        *,
        user_id: UUID,
        query: str,
        top_k: int,
        category: KnowledgeCategory | None = None,
        source_type: RegulationSourceType | None = None,
        jurisdiction: str | None = None,
        regulation_ids: list[UUID] | None = None,
        categories: list[KnowledgeCategory] | None = None,
        jurisdictions: list[str] | None = None,
        audit_as_of: date | None = None,
    ) -> list[RegulationSearchItem]:
        """执行面向 RAG 上下文召回的 Top-K 关键词与向量混合检索。"""
        query = query.strip()
        if not query:
            raise BusinessException(
                INVALID_REGULATION_SEARCH_QUERY,
                "regulation search query must not be blank",
            )
        if contains_control_character(query):
            raise BusinessException(
                INVALID_REGULATION_SEARCH_QUERY,
                "regulation search query contains control characters",
            )

        if jurisdiction is not None:
            jurisdiction = jurisdiction.strip()
            if not jurisdiction:
                raise BusinessException(
                    INVALID_REGULATION_SEARCH_QUERY,
                    "regulation search jurisdiction must not be blank",
                )
            if contains_control_character(jurisdiction):
                raise BusinessException(
                    INVALID_REGULATION_SEARCH_QUERY,
                    "regulation search jurisdiction contains control characters",
                )

        query_vector = await self.embedding.embed_query(query)

        search_options = {
            "query_text": query,
            "query_vector": query_vector,
            "user_id": str(user_id),
            "top_k": top_k,
            "category": category.value if category else None,
            "source_type": source_type.value if source_type else None,
            "jurisdiction": jurisdiction,
        }
        # 旧法规搜索调用保持原参数契约；审计专用过滤只在实际提供时传入。
        if regulation_ids:
            search_options["regulation_ids"] = [str(value) for value in regulation_ids]
        if categories:
            search_options["categories"] = [value.value for value in categories]
        if jurisdictions:
            search_options["jurisdictions"] = jurisdictions
        if audit_as_of is not None:
            search_options["audit_as_of"] = audit_as_of

        items = await self.vector_store.search_similar(
            **search_options,
        )
        candidate_ids = [UUID(str(item["chunk_id"])) for item in items]
        # ES 只是候选查询副本。即使重建中遗留了旧副本，也必须以数据库中的
        # 权限、有效期和阶段状态为准，任何非 READY 数据均不可进入问答上下文。
        async with self.uow:
            searchable_ids = await self.chunk_repository.find_searchable_ids(
                chunk_ids=candidate_ids,
                user_id=user_id,
                audit_as_of=audit_as_of,
            )

        return [
            RegulationSearchItem.model_validate(item)
            for item in items
            if UUID(str(item["chunk_id"])) in searchable_ids
        ]


def get_regulation_search_service(
    uow: UnitOfWork = Depends(get_uow),
    embedding: EmbeddingService = Depends(get_embedding_service),
) -> RegulationSearchService:
    return RegulationSearchService(
        embedding=embedding,
        vector_store=regulation_vector_store,
        uow=uow,
        chunk_repository=RegulationChunkRepository(uow.session),
    )
