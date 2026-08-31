import time
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from langchain_core.documents import Document as LangChainDocument
from langchain_core.documents.compressor import BaseDocumentCompressor

from app.ai.reranking.contract import RerankerError
from app.core.logger import logger
from app.infrastructure.db.unit_of_work import UnitOfWork
from app.models.regulation_rule import RegulationRule
from app.repositories.regulation_rule_repository import RegulationRuleRepository
from app.schemas.audit_task import AuditRuleScope
from app.services.regulation_rule_index_service import RegulationRuleIndexService

MAX_AUDIT_RULES = 10

MAX_RULE_RETRIEVAL_QUERY_CHARACTERS = 3_000
RULE_RETRIEVAL_QUERY_OVERLAP = 200


@dataclass(frozen=True)
class AuditRuleCandidate:
    """已通过权限和有效期校验、可以提交给审计模型的规则。"""

    rule: RegulationRule
    retrieval_score: float


class AuditRuleRetrievalService:
    """直接召回原子规则，并在数据库中执行第二层权限校验。"""

    def __init__(
        self,
        *,
        search_service: RegulationRuleIndexService,
        rule_repository: RegulationRuleRepository,
        uow: UnitOfWork,
        reranker: BaseDocumentCompressor | None,
        candidate_count: int,
        top_n: int,
    ) -> None:
        self.search_service = search_service
        self.rule_repository = rule_repository
        self.uow = uow
        self.reranker = reranker
        self.candidate_count = candidate_count
        # 审计模型每批最多接收 10 条规则；候选池仍可更大，供 reranker 精排。
        self.top_n = min(top_n, MAX_AUDIT_RULES)

    async def retrieve(
        self,
        *,
        user_id: UUID,
        task_id: UUID,
        page_number: int,
        batch_index: int,
        page_query: str,
        scope: AuditRuleScope,
        audit_as_of: date,
    ) -> list[AuditRuleCandidate]:
        queries = self._split_query(page_query)
        search_results = []
        for query in queries:
            search_results.append(
                await self.search_service.search(
                    user_id=user_id,
                    query=query,
                    top_k=self.candidate_count,
                    audit_as_of=audit_as_of,
                    regulation_ids=scope.regulation_ids,
                    categories=(
                        [value.value for value in scope.categories]
                        if scope.categories
                        else None
                    ),
                    jurisdictions=scope.jurisdictions,
                    rule_types=(
                        [value.value for value in scope.rule_types]
                        if scope.rule_types
                        else None
                    ),
                )
            )
        # 多窗口之间使用最高相似度，避免通用规则仅因重复命中而压过某个
        # 窗口中的精确规则。
        score_by_rule: dict[UUID, float] = {}
        for hits in search_results:
            for item in hits:
                score_by_rule[item.rule_id] = max(
                    score_by_rule.get(item.rule_id, float("-inf")),
                    item.score,
                )
        if not score_by_rule:
            return []
        # ES/Embedding 已在事务外完成；数据库权限复核只占用一个短事务。
        async with self.uow:
            rules = await self.rule_repository.find_audit_candidates_by_ids(
                rule_ids=list(score_by_rule),
                user_id=user_id,
                audit_as_of=audit_as_of,
                regulation_ids=scope.regulation_ids,
                categories=scope.categories,
                jurisdictions=scope.jurisdictions,
                rule_types=scope.rule_types,
            )
        rules_by_id = {rule.id: rule for rule in rules}
        window_rules = [
            [rules_by_id[item.rule_id] for item in hits if item.rule_id in rules_by_id]
            for hits in search_results
        ]
        if self.reranker is not None:
            # 每个 3000 字窗口只精排该窗口召回的规则。这样后半页规则不会
            # 被拿去和第一页文本比较，也不会把整页提交给 rerank 服务。
            ranked_windows = []
            for query, candidates in zip(queries, window_rules, strict=True):
                ranked_windows.append(
                    await self._rerank(
                        page_query=query,
                        rules=candidates,
                        task_id=task_id,
                        page_number=page_number,
                        batch_index=batch_index,
                    )
                )
        else:
            ranked_windows = window_rules
        ordered = self._interleave_ranked_rules(ranked_windows)
        return [
            AuditRuleCandidate(
                rule=rule,
                retrieval_score=score_by_rule.get(rule.id, 0.0),
            )
            for rule in ordered[: self.top_n]
        ]

    @staticmethod
    def _interleave_ranked_rules(
        ranked_windows: list[list[RegulationRule]],
    ) -> list[RegulationRule]:
        """轮流选取各窗口的高排名规则，避免长页第一页独占候选名额。"""
        result: list[RegulationRule] = []
        seen: set[UUID] = set()
        max_length = max((len(window) for window in ranked_windows), default=0)
        for rank in range(max_length):
            for window in ranked_windows:
                if rank >= len(window):
                    continue
                rule = window[rank]
                if rule.id in seen:
                    continue
                seen.add(rule.id)
                result.append(rule)
        return result

    async def _rerank(
        self,
        *,
        page_query: str,
        rules: list[RegulationRule],
        task_id: UUID,
        page_number: int,
        batch_index: int,
    ) -> list[RegulationRule]:
        if self.reranker is None or not rules:
            return rules
        documents = [
            LangChainDocument(
                page_content=self._rule_text(rule),
                metadata={"rule_id": str(rule.id)},
            )
            for rule in rules
        ]
        by_id = {str(rule.id): rule for rule in rules}
        started_at = time.perf_counter()
        try:
            reranked = await self.reranker.acompress_documents(
                documents=documents,
                query=page_query,
            )
            result = [
                by_id[rule_id]
                for document in reranked
                if (rule_id := str(document.metadata.get("rule_id"))) in by_id
            ]
            return result or rules
        except RerankerError as exc:
            # 精排是质量增强项，供应商失败时保留已经完成权限过滤的基础排序。
            logger.warning(
                "audit.rules.rerank_failed",
                task_id=str(task_id),
                page_number=page_number,
                batch_index=batch_index,
                stage="AUDITING",
                error_type=type(exc).__name__,
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
                exc_info=True,
            )
            return rules

    @staticmethod
    def _rule_text(rule: RegulationRule) -> str:
        parts = [
            rule.topic,
            rule.subject,
            rule.action,
            rule.object,
            rule.condition,
            rule.time_limit,
            *rule.requirements,
            *rule.restrictions,
            *rule.exceptions,
            *rule.consequences,
            rule.source_text,
        ]
        return "\n".join(value for value in parts if value)

    @staticmethod
    def _split_query(page_query: str) -> list[str]:
        """长页面分窗召回，避免只检索页面开头导致后半页规则漏召回。"""
        page_query = page_query.strip()
        if not page_query:
            return []
        if len(page_query) <= MAX_RULE_RETRIEVAL_QUERY_CHARACTERS:
            return [page_query]
        step = MAX_RULE_RETRIEVAL_QUERY_CHARACTERS - RULE_RETRIEVAL_QUERY_OVERLAP
        return [
            page_query[start : start + MAX_RULE_RETRIEVAL_QUERY_CHARACTERS]
            for start in range(0, len(page_query), step)
        ]
