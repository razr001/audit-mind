import asyncio
from datetime import date
from typing import Any

from elasticsearch import AsyncElasticsearch, BadRequestError
from elasticsearch.helpers import async_bulk, async_scan

from app.core.config import get_settings
from app.infrastructure.es_client import es_client
from app.infrastructure.regulation_result_fusion import fuse_regulation_results


class RegulationVectorStore:
    """维护法规 Chunk 的 Elasticsearch 查询副本并提供混合检索。"""

    def __init__(
        self,
        *,
        client: AsyncElasticsearch,
        index_name: str,
        dimensions: int,
    ) -> None:
        self.client = client
        self.index_name = index_name
        self.dimensions = dimensions
        # 旧索引的页码范围 Mapping 每个进程只需检查并补齐一次。
        self._page_range_mapping_ensured = False

    async def ensure_index(self) -> None:
        """按需创建法规向量索引，并兼容多个实例同时首次创建。"""
        exists = await self.client.indices.exists(index=self.index_name)
        if exists:
            if not self._page_range_mapping_ensured:
                await self.client.indices.put_mapping(
                    index=self.index_name,
                    properties={
                        "chunk_id": {"type": "keyword"},
                        "page_start": {"type": "integer"},
                        "page_end": {"type": "integer"},
                    },
                )
                self._page_range_mapping_ensured = True
            return

        try:
            await self.client.indices.create(
                index=self.index_name,
                mappings={
                    "dynamic": "strict",
                    "_source": {"excludes": ["embedding"]},
                    "properties": {
                        "chunk_id": {"type": "keyword"},
                        "regulation_id": {"type": "keyword"},
                        "uploaded_by": {"type": "keyword"},
                        "visibility": {"type": "keyword"},
                        "category": {"type": "keyword"},
                        "source_type": {"type": "keyword"},
                        "language": {"type": "keyword"},
                        "jurisdiction": {"type": "keyword"},
                        "enabled": {"type": "boolean"},
                        "title": {
                            "type": "text",
                            "analyzer": "ik_max_word",
                            "search_analyzer": "ik_smart",
                        },
                        "authority": {"type": "keyword", "ignore_above": 256},
                        "effective_date": {"type": "date"},
                        "expiration_date": {"type": "date"},
                        "chunk_index": {"type": "integer"},
                        "article_number": {"type": "keyword", "ignore_above": 100},
                        "chapter": {
                            "type": "text",
                            "analyzer": "ik_max_word",
                            "search_analyzer": "ik_smart",
                        },
                        "page_number": {"type": "integer"},
                        "page_start": {"type": "integer"},
                        "page_end": {"type": "integer"},
                        "content": {
                            "type": "text",
                            "analyzer": "ik_max_word",
                            "search_analyzer": "ik_smart",
                        },
                        "rule_type": {"type": "keyword"},
                        "subject": {
                            "type": "text",
                            "analyzer": "ik_max_word",
                            "search_analyzer": "ik_smart",
                        },
                        "action": {
                            "type": "text",
                            "analyzer": "ik_max_word",
                            "search_analyzer": "ik_smart",
                        },
                        "condition": {
                            "type": "text",
                            "analyzer": "ik_max_word",
                            "search_analyzer": "ik_smart",
                        },
                        "exception": {
                            "type": "text",
                            "analyzer": "ik_max_word",
                            "search_analyzer": "ik_smart",
                        },
                        "consequence": {
                            "type": "text",
                            "analyzer": "ik_max_word",
                            "search_analyzer": "ik_smart",
                        },
                        "embedding": {
                            "type": "dense_vector",
                            "dims": self.dimensions,
                            "index": True,
                            "similarity": "cosine",
                        },
                    },
                },
            )
        except BadRequestError as exc:
            error = exc.body.get("error") if isinstance(exc.body, dict) else None
            error_type = error.get("type") if isinstance(error, dict) else None
            if error_type != "resource_already_exists_exception":
                raise
        self._page_range_mapping_ensured = True

    async def replace_regulation_chunks(
        self,
        *,
        regulation_id: str,
        chunks: list[dict[str, Any]],
    ) -> None:
        """使用 ES OCC 整体替换一份法规的全文 Chunk 查询副本。"""
        validated_chunks: list[tuple[str, dict[str, Any]]] = []
        for chunk in chunks:
            vector = chunk.get("embedding")
            if not isinstance(vector, list) or len(vector) != self.dimensions:
                raise RuntimeError("regulation chunk embedding dimension mismatch")
            document_id = str(chunk.get("document_id", chunk["id"]))
            validated_chunks.append((document_id, chunk))

        await self.ensure_index()
        current_versions = await self._load_regulation_document_versions(regulation_id)
        actions: list[dict[str, Any]] = []
        active_ids: set[str] = set()
        for document_id, chunk in validated_chunks:
            vector = chunk["embedding"]
            active_ids.add(document_id)
            action: dict[str, Any] = {
                    "_op_type": "index" if document_id in current_versions else "create",
                    "_index": self.index_name,
                    "_id": document_id,
                    "_source": {
                        "chunk_id": chunk["id"],
                        "regulation_id": chunk["regulation_id"],
                        "uploaded_by": chunk["uploaded_by"],
                        "visibility": chunk["visibility"],
                        "category": chunk["category"],
                        "source_type": chunk["source_type"],
                        "language": chunk["language"],
                        "jurisdiction": chunk["jurisdiction"],
                        "enabled": chunk["enabled"],
                        "title": chunk["title"],
                        "authority": chunk.get("authority"),
                        "effective_date": chunk.get("effective_date"),
                        "expiration_date": chunk.get("expiration_date"),
                        "chunk_index": chunk["chunk_index"],
                        "article_number": chunk.get("article_number"),
                        "chapter": chunk.get("chapter"),
                        "page_number": chunk.get("page_number"),
                        "page_start": chunk.get("page_start"),
                        "page_end": chunk.get("page_end"),
                        "content": chunk["content"],
                        "rule_type": chunk.get("rule_type"),
                        "subject": chunk.get("subject"),
                        "action": chunk.get("action"),
                        "condition": chunk.get("condition"),
                        "exception": chunk.get("exception"),
                        "consequence": chunk.get("consequence"),
                        "embedding": vector,
                    },
                }
            if document_id in current_versions:
                action["_if_seq_no"], action["_if_primary_term"] = current_versions[
                    document_id
                ]
            actions.append(action)

        for stale_id, (seq_no, primary_term) in current_versions.items():
            if stale_id not in active_ids:
                actions.append(
                    {
                        "_op_type": "delete",
                        "_index": self.index_name,
                        "_id": stale_id,
                        "_if_seq_no": seq_no,
                        "_if_primary_term": primary_term,
                    }
                )

        if actions:
            await async_bulk(self.client, actions, refresh="wait_for")

    async def _load_regulation_document_versions(
        self,
        regulation_id: str,
    ) -> dict[str, tuple[int, int]]:
        """读取法规现有 Chunk 的 OCC token，避免旧任务覆盖新副本。"""
        versions: dict[str, tuple[int, int]] = {}
        query = {"query": {"term": {"regulation_id": regulation_id}}}
        async for hit in async_scan(
            self.client,
            index=self.index_name,
            query=query,
            source=False,
            seq_no_primary_term=True,
        ):
            versions[str(hit["_id"])] = (
                int(hit["_seq_no"]),
                int(hit["_primary_term"]),
            )
        return versions

    async def delete_regulation_chunks(self, *, regulation_id: str) -> None:
        """让某一法规的旧 ES 查询副本立即失效。"""
        if not await self.client.indices.exists(index=self.index_name):
            return
        await self.client.delete_by_query(
            index=self.index_name,
            query={"term": {"regulation_id": regulation_id}},
            conflicts="proceed",
            refresh=True,
        )

    async def search_similar(
        self,
        *,
        query_text: str,
        query_vector: list[float],
        user_id: str,
        top_k: int,
        category: str | None = None,
        source_type: str | None = None,
        jurisdiction: str | None = None,
        regulation_ids: list[str] | None = None,
        categories: list[str] | None = None,
        jurisdictions: list[str] | None = None,
        audit_as_of: date | None = None,
    ) -> list[dict[str, Any]]:
        """使用 BM25 和 KNN 混合检索当前用户可访问的知识。"""
        query_text = query_text.strip()
        if not query_text:
            raise ValueError("regulation search query must not be blank")
        if len(query_vector) != self.dimensions:
            raise RuntimeError("regulation query embedding dimension mismatch")

        await self.ensure_index()
        filters: list[dict[str, Any]] = [
            {"term": {"enabled": True}},
            {
                "bool": {
                    "should": [
                        {"term": {"visibility": "SHARED"}},
                        {
                            "bool": {
                                "filter": [
                                    {"term": {"visibility": "PRIVATE"}},
                                    {"term": {"uploaded_by": user_id}},
                                ]
                            }
                        },
                    ],
                    "minimum_should_match": 1,
                }
            },
        ]
        if category is not None:
            filters.append({"term": {"category": category}})
        if source_type is not None:
            filters.append({"term": {"source_type": source_type}})
        if jurisdiction is not None:
            filters.append({"term": {"jurisdiction": jurisdiction}})
        if regulation_ids:
            filters.append({"terms": {"regulation_id": regulation_ids}})
        if categories:
            filters.append({"terms": {"category": categories}})
        if jurisdictions:
            filters.append({"terms": {"jurisdiction": jurisdictions}})
        if audit_as_of is not None:
            as_of = audit_as_of.isoformat()
            filters.extend(
                [
                    {
                        "bool": {
                            "should": [
                                {"bool": {"must_not": {"exists": {"field": "effective_date"}}}},
                                {"range": {"effective_date": {"lte": as_of}}},
                            ],
                            "minimum_should_match": 1,
                        }
                    },
                    {
                        "bool": {
                            "should": [
                                {"bool": {"must_not": {"exists": {"field": "expiration_date"}}}},
                                {"range": {"expiration_date": {"gte": as_of}}},
                            ],
                            "minimum_should_match": 1,
                        }
                    },
                ]
            )

        candidate_size = max(50, top_k * 5)
        source_excludes = ["embedding"]
        bm25_request = self.client.search(
            index=self.index_name,
            query={
                "bool": {
                    "must": {
                        "multi_match": {
                            "query": query_text,
                            "fields": [
                                "content^4",
                                "title^2",
                                "chapter",
                                "subject",
                                "action",
                                "condition",
                                "exception",
                                "consequence",
                            ],
                            "type": "best_fields",
                        }
                    },
                    "filter": filters,
                }
            },
            size=candidate_size,
            source_excludes=source_excludes,
        )
        knn_request = self.client.search(
            index=self.index_name,
            knn={
                "field": "embedding",
                "query_vector": query_vector,
                "k": candidate_size,
                "num_candidates": max(100, candidate_size * 2),
                "filter": {"bool": {"filter": filters}},
            },
            size=candidate_size,
            source_excludes=source_excludes,
        )
        bm25_response, knn_response = await asyncio.gather(bm25_request, knn_request)
        return fuse_regulation_results(
            bm25_hits=bm25_response["hits"]["hits"],
            knn_hits=knn_response["hits"]["hits"],
            top_k=top_k,
        )


settings = get_settings()
regulation_vector_store = RegulationVectorStore(
    client=es_client.client,
    index_name=settings.ELASTICSEARCH_REGULATION_CHUNK_INDEX,
    dimensions=settings.AI_EMBEDDING_DIMENSIONS,
)
