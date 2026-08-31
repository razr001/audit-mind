from dataclasses import dataclass
from datetime import date
from uuid import UUID

from app.ai.embedding import EmbeddingService
from app.infrastructure.regulation_rule_vector_store import RegulationRuleVectorStore
from app.models.regulation import Regulation
from app.models.regulation_rule import RegulationRule

MAX_RULE_EMBEDDING_CHARACTERS = 12_000


@dataclass(frozen=True)
class RegulationRuleSearchHit:
    rule_id: UUID
    score: float


class RegulationRuleIndexService:
    """构建和查询规则级向量索引，不负责数据库事务或业务状态。"""

    def __init__(
        self,
        *,
        embedding: EmbeddingService,
        vector_store: RegulationRuleVectorStore,
    ) -> None:
        self.embedding = embedding
        self.vector_store = vector_store

    async def build_documents(
        self,
        *,
        regulation: Regulation,
        rules: list[RegulationRule],
    ) -> list[dict]:
        texts = [self._embedding_text(regulation=regulation, rule=rule) for rule in rules]
        vectors = await self.embedding.embed_documents(texts)
        return [
            self._index_document(
                regulation=regulation,
                rule=rule,
                embedding=embedding,
            )
            for rule, embedding in zip(rules, vectors, strict=True)
        ]

    async def replace_regulation_rules(
        self,
        *,
        regulation_id: UUID,
        documents: list[dict],
    ) -> None:
        await self.vector_store.replace_regulation_rules(
            regulation_id=str(regulation_id),
            rules=documents,
        )

    async def search(
        self,
        *,
        user_id: UUID,
        query: str,
        top_k: int,
        audit_as_of: date,
        regulation_ids: list[UUID] | None = None,
        categories: list[str] | None = None,
        jurisdictions: list[str] | None = None,
        rule_types: list[str] | None = None,
    ) -> list[RegulationRuleSearchHit]:
        query_vector = await self.embedding.embed_query(query)
        items = await self.vector_store.search_similar(
            query_text=query,
            query_vector=query_vector,
            user_id=str(user_id),
            top_k=top_k,
            audit_as_of=audit_as_of,
            regulation_ids=[str(value) for value in regulation_ids] if regulation_ids else None,
            categories=categories,
            jurisdictions=jurisdictions,
            rule_types=rule_types,
        )
        return [
            RegulationRuleSearchHit(
                rule_id=UUID(str(item["rule_id"])),
                score=float(item["score"]),
            )
            for item in items
        ]

    @staticmethod
    def _embedding_text(*, regulation: Regulation, rule: RegulationRule) -> str:
        parts = [
            f"法规标题：{regulation.title}",
            f"规则类型：{rule.rule_type.value}",
            f"主题：{rule.topic or ''}",
            f"责任主体：{rule.subject or ''}",
            f"行为要求：{rule.action or ''}",
            f"对象：{rule.object or ''}",
            f"适用条件：{rule.condition or ''}",
            f"时限：{rule.time_limit or ''}",
            f"要求：{'；'.join(rule.requirements)}",
            f"限制：{'；'.join(rule.restrictions)}",
            f"例外：{'；'.join(rule.exceptions)}",
            f"后果：{'；'.join(rule.consequences)}",
            f"法规原文：{rule.source_text}",
        ]
        return "\n".join(parts)[:MAX_RULE_EMBEDDING_CHARACTERS]

    @staticmethod
    def _index_document(
        *,
        regulation: Regulation,
        rule: RegulationRule,
        embedding: list[float],
    ) -> dict:
        return {
            "rule_id": str(rule.id),
            "regulation_id": str(regulation.id),
            "source_chunk_id": str(rule.source_chunk_id),
            "uploaded_by": str(regulation.uploaded_by),
            "visibility": regulation.visibility.value,
            "category": regulation.category.value,
            "source_type": regulation.source_type.value,
            "language": regulation.language,
            "jurisdiction": regulation.jurisdiction,
            "enabled": regulation.enabled,
            "effective_date": (
                regulation.effective_date.isoformat() if regulation.effective_date else None
            ),
            "expiration_date": (
                regulation.expiration_date.isoformat() if regulation.expiration_date else None
            ),
            "rule_type": rule.rule_type.value,
            "title": regulation.title,
            "topic": rule.topic,
            "subject": rule.subject,
            "action": rule.action,
            "object": rule.object,
            "condition": rule.condition,
            "time_limit": rule.time_limit,
            "requirements": rule.requirements,
            "restrictions": rule.restrictions,
            "exceptions": rule.exceptions,
            "consequences": rule.consequences,
            "content": rule.source_text,
            "embedding": embedding,
        }
