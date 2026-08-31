from datetime import date
from typing import Any

from elasticsearch import AsyncElasticsearch, BadRequestError
from elasticsearch.helpers import async_bulk, async_scan

from app.core.config import get_settings
from app.infrastructure.es_client import es_client


class RegulationRuleVectorStore:
    """维护 RegulationRule 的可重建 ES 查询副本。

    PostgreSQL 始终保存规则事实和权限信息。ES 只负责生成候选，所有命中
    都必须回到数据库复核，不能直接作为审计依据。
    """

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

    async def ensure_index(self) -> None:
        if await self.client.indices.exists(index=self.index_name):
            return
        try:
            await self.client.indices.create(
                index=self.index_name,
                mappings={
                    "dynamic": "strict",
                    "_source": {"excludes": ["embedding"]},
                    "properties": {
                        "rule_id": {"type": "keyword"},
                        "regulation_id": {"type": "keyword"},
                        "source_chunk_id": {"type": "keyword"},
                        "uploaded_by": {"type": "keyword"},
                        "visibility": {"type": "keyword"},
                        "category": {"type": "keyword"},
                        "source_type": {"type": "keyword"},
                        "language": {"type": "keyword"},
                        "jurisdiction": {"type": "keyword"},
                        "enabled": {"type": "boolean"},
                        "effective_date": {"type": "date"},
                        "expiration_date": {"type": "date"},
                        "rule_type": {"type": "keyword"},
                        "title": self._text_mapping(),
                        "topic": self._text_mapping(),
                        "subject": self._text_mapping(),
                        "action": self._text_mapping(),
                        "object": self._text_mapping(),
                        "condition": self._text_mapping(),
                        "time_limit": self._text_mapping(),
                        "requirements": self._text_mapping(),
                        "restrictions": self._text_mapping(),
                        "exceptions": self._text_mapping(),
                        "consequences": self._text_mapping(),
                        "content": self._text_mapping(),
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
            # 多实例可能同时通过 exists 检查，后创建者只忽略索引已存在。
            error = exc.body.get("error") if isinstance(exc.body, dict) else None
            error_type = error.get("type") if isinstance(error, dict) else None
            if error_type != "resource_already_exists_exception":
                raise

    async def replace_regulation_rules(
        self,
        *,
        regulation_id: str,
        rules: list[dict[str, Any]],
    ) -> None:
        """使用 ES OCC 整体替换一份法规的规则查询副本。

        ``_seq_no`` 与 ``_primary_term`` 只能保护单个已读取文档，因此这里先
        读取当前法规全部文档的版本，再把条件覆盖、条件删除和新建操作交给
        bulk 批处理。Bulk 本身不是事务，但每个既有文档都有独立 CAS 条件；
        旧执行者持有的版本一旦过期就会以 409 失败，交由上层整体重试。
        """
        validated_rules: list[tuple[str, dict[str, Any]]] = []
        rule_ids: list[str] = []
        for rule in rules:
            vector = rule.get("embedding")
            if not isinstance(vector, list) or len(vector) != self.dimensions:
                raise RuntimeError("regulation rule embedding dimension mismatch")
            rule_id = str(rule["rule_id"])
            rule_ids.append(rule_id)
            validated_rules.append((rule_id, rule))

        await self.ensure_index()
        current_versions = await self._load_regulation_document_versions(regulation_id)
        actions: list[dict[str, Any]] = []
        for rule_id, rule in validated_rules:
            action: dict[str, Any] = {
                "_op_type": "index" if rule_id in current_versions else "create",
                "_index": self.index_name,
                "_id": rule_id,
                "_source": rule,
            }
            if rule_id in current_versions:
                action["_if_seq_no"], action["_if_primary_term"] = current_versions[rule_id]
            actions.append(action)

        active_ids = set(rule_ids)
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
            # raise_on_error 保持默认 True：任何 OCC 冲突都必须让本次任务失败，
            # 交给上层基于 PostgreSQL 状态安全重试，不能接受部分静默覆盖。
            await async_bulk(self.client, actions, refresh="wait_for")

    async def _load_regulation_document_versions(
        self,
        regulation_id: str,
    ) -> dict[str, tuple[int, int]]:
        """读取法规现有文档的 OCC token，供后续条件 bulk 使用。"""
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

    async def delete_regulation_rules(self, *, regulation_id: str) -> None:
        """删除某个知识源的全部规则查询副本。"""
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
        audit_as_of: date,
        regulation_ids: list[str] | None = None,
        categories: list[str] | None = None,
        jurisdictions: list[str] | None = None,
        rule_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not query_text.strip():
            raise ValueError("regulation rule search query must not be blank")
        if len(query_vector) != self.dimensions:
            raise RuntimeError("regulation rule query embedding dimension mismatch")

        filters = self._build_filters(
            user_id=user_id,
            audit_as_of=audit_as_of,
            regulation_ids=regulation_ids,
            categories=categories,
            jurisdictions=jurisdictions,
            rule_types=rule_types,
        )
        # 审计输入是一整页待检查文档，不是用户搜索词。直接对整页执行 BM25
        # 会把正文高频词误当成检索意图，并可能因中文分词产生过多查询子句。
        # 这里只做向量候选召回，随后由业务层完成权限复核和可选 rerank。
        candidate_size = top_k
        source_excludes = ["embedding"]
        response = await self.client.search(
            index=self.index_name,
            knn={
                "field": "embedding",
                "query_vector": query_vector,
                "k": candidate_size,
                "num_candidates": max(100, candidate_size * 4),
                "filter": {"bool": {"filter": filters}},
            },
            size=candidate_size,
            source_excludes=source_excludes,
        )
        return [
            {
                **hit["_source"],
                "rule_id": str(hit["_source"]["rule_id"]),
                "score": float(hit.get("_score") or 0.0),
            }
            for hit in response["hits"]["hits"][:top_k]
        ]

    @staticmethod
    def _text_mapping() -> dict[str, Any]:
        return {
            "type": "text",
            "analyzer": "ik_max_word",
            "search_analyzer": "ik_smart",
        }

    @staticmethod
    def _build_filters(
        *,
        user_id: str,
        audit_as_of: date,
        regulation_ids: list[str] | None,
        categories: list[str] | None,
        jurisdictions: list[str] | None,
        rule_types: list[str] | None,
    ) -> list[dict[str, Any]]:
        as_of = audit_as_of.isoformat()
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
        if regulation_ids:
            filters.append({"terms": {"regulation_id": regulation_ids}})
        if categories:
            filters.append({"terms": {"category": categories}})
        if jurisdictions:
            filters.append({"terms": {"jurisdiction": jurisdictions}})
        if rule_types:
            filters.append({"terms": {"rule_type": rule_types}})
        return filters


settings = get_settings()
regulation_rule_vector_store = RegulationRuleVectorStore(
    client=es_client.client,
    index_name=settings.ELASTICSEARCH_REGULATION_RULE_INDEX,
    dimensions=settings.AI_EMBEDDING_DIMENSIONS,
)
