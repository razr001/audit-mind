import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from app.models.regulation import KnowledgeCategory
from app.models.regulation_rule import RegulationRuleType
from app.repositories.regulation_rule_repository import RegulationRuleRepository
from app.schemas.audit_task import AuditRuleScope
from app.services.audit_rule_retrieval_service import AuditRuleRetrievalService


class FakeUow:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False


def test_database_permission_check_does_not_arbitrarily_limit_es_candidates() -> None:
    class ScalarRows:
        def scalars(self):
            return self

        def all(self):
            return []

    session = SimpleNamespace(execute=AsyncMock(return_value=ScalarRows()))
    repository = RegulationRuleRepository(session)

    asyncio.run(
        repository.find_audit_candidates_by_ids(
            rule_ids=[uuid4() for _ in range(250)],
            user_id=uuid4(),
            audit_as_of=date(2026, 8, 28),
        )
    )

    statement = session.execute.await_args.args[0]
    assert statement._limit_clause is None


def test_audit_rule_retrieval_forwards_scope_and_preserves_rule_rank() -> None:
    first_rule = SimpleNamespace(id=uuid4())
    second_rule = SimpleNamespace(id=uuid4())
    search = SimpleNamespace(
        search=AsyncMock(
            return_value=[
                SimpleNamespace(rule_id=first_rule.id, score=0.8),
                SimpleNamespace(rule_id=second_rule.id, score=0.2),
            ]
        )
    )
    repository = SimpleNamespace(
        find_audit_candidates_by_ids=AsyncMock(return_value=[second_rule, first_rule])
    )
    service = AuditRuleRetrievalService(
        search_service=search,
        rule_repository=repository,
        uow=FakeUow(),
        reranker=None,
        candidate_count=30,
        top_n=10,
    )
    user_id = uuid4()
    regulation_id = uuid4()
    scope = AuditRuleScope(
        regulation_ids=[regulation_id],
        categories=[KnowledgeCategory.COMPANY_RULE],
        jurisdictions=["CN"],
        rule_types=[RegulationRuleType.PROHIBITION],
    )

    result = asyncio.run(
        service.retrieve(
            user_id=user_id,
            task_id=uuid4(),
            page_number=1,
            batch_index=1,
            page_query="不得默认勾选",
            scope=scope,
            audit_as_of=date(2026, 8, 26),
        )
    )

    assert [item.rule for item in result] == [first_rule, second_rule]
    search.search.assert_awaited_once_with(
        user_id=user_id,
        query="不得默认勾选",
        top_k=30,
        audit_as_of=date(2026, 8, 26),
        regulation_ids=[regulation_id],
        categories=[KnowledgeCategory.COMPANY_RULE.value],
        jurisdictions=["CN"],
        rule_types=[RegulationRuleType.PROHIBITION.value],
    )
    repository.find_audit_candidates_by_ids.assert_awaited_once()
    assert repository.find_audit_candidates_by_ids.await_args.kwargs["rule_types"] == [
        RegulationRuleType.PROHIBITION
    ]


def test_audit_rule_retrieval_returns_empty_when_search_has_no_candidates() -> None:
    search = SimpleNamespace(search=AsyncMock(return_value=[]))
    repository = SimpleNamespace(find_audit_candidates_by_ids=AsyncMock(return_value=[]))
    service = AuditRuleRetrievalService(
        search_service=search,
        rule_repository=repository,
        uow=FakeUow(),
        reranker=None,
        candidate_count=30,
        top_n=10,
    )

    result = asyncio.run(
        service.retrieve(
            user_id=uuid4(),
            task_id=uuid4(),
            page_number=1,
            batch_index=1,
            page_query="普通文本",
            scope=AuditRuleScope(),
            audit_as_of=date.today(),
        )
    )

    assert result == []
    repository.find_audit_candidates_by_ids.assert_not_awaited()


def test_long_page_query_is_split_with_overlap_instead_of_truncated() -> None:
    queries = AuditRuleRetrievalService._split_query("A" * 6_500)

    assert len(queries) == 3
    assert all(len(query) <= 3_000 for query in queries)
    assert queries[0][-200:] == queries[1][:200]
    assert queries[1][-200:] == queries[2][:200]


def test_ranked_windows_are_interleaved_without_duplicate_rules() -> None:
    first = SimpleNamespace(id=uuid4())
    shared = SimpleNamespace(id=uuid4())
    later = SimpleNamespace(id=uuid4())

    result = AuditRuleRetrievalService._interleave_ranked_rules(
        [[first, shared], [later, shared]]
    )

    assert result == [first, later, shared]


def test_long_page_reranks_each_three_thousand_character_window() -> None:
    rules = [SimpleNamespace(id=uuid4()) for _ in range(3)]
    search_active = 0
    search_max_active = 0
    search_call = 0

    async def search_window(**_kwargs):
        nonlocal search_active, search_max_active, search_call
        rule = rules[search_call]
        search_call += 1
        search_active += 1
        search_max_active = max(search_max_active, search_active)
        await asyncio.sleep(0)
        search_active -= 1
        return [SimpleNamespace(rule_id=rule.id, score=0.8)]

    search = SimpleNamespace(search=AsyncMock(side_effect=search_window))
    repository = SimpleNamespace(
        find_audit_candidates_by_ids=AsyncMock(return_value=rules)
    )
    service = AuditRuleRetrievalService(
        search_service=search,
        rule_repository=repository,
        uow=FakeUow(),
        reranker=SimpleNamespace(),
        candidate_count=30,
        top_n=10,
    )
    rerank_active = 0
    rerank_max_active = 0

    async def rerank_window(*, page_query, rules, **_context):
        nonlocal rerank_active, rerank_max_active
        del page_query
        rerank_active += 1
        rerank_max_active = max(rerank_max_active, rerank_active)
        await asyncio.sleep(0)
        rerank_active -= 1
        return rules

    service._rerank = AsyncMock(side_effect=rerank_window)

    result = asyncio.run(
        service.retrieve(
            user_id=uuid4(),
            task_id=uuid4(),
            page_number=1,
            batch_index=1,
            page_query="A" * 6_500,
            scope=AuditRuleScope(),
            audit_as_of=date.today(),
        )
    )

    assert [item.rule for item in result] == rules
    assert service._rerank.await_count == 3
    assert all(
        len(call.kwargs["page_query"]) <= 3_000
        for call in service._rerank.await_args_list
    )
    assert search_max_active == 1
    assert rerank_max_active == 1
