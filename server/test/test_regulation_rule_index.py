import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import BusinessException
from app.infrastructure.regulation_rule_vector_store import RegulationRuleVectorStore
from app.models.regulation import (
    KnowledgeCategory,
    KnowledgeVisibility,
    RegulationRuleStatus,
    RegulationSourceType,
)
from app.models.regulation_rule import RegulationRuleType
from app.services.regulation_rule_index_service import RegulationRuleIndexService
from app.services.regulation_rule_publisher import publish_regulation_rules


def make_regulation():
    return SimpleNamespace(
        id=uuid4(),
        lock_version=1,
        title="个人信息保护规定",
        uploaded_by=uuid4(),
        visibility=KnowledgeVisibility.SHARED,
        category=KnowledgeCategory.PUBLIC_KNOWLEDGE,
        source_type=RegulationSourceType.REGULATION,
        language="zh-CN",
        jurisdiction="CN",
        enabled=True,
        effective_date=date(2026, 1, 1),
        expiration_date=None,
    )


def make_rule():
    return SimpleNamespace(
        id=uuid4(),
        source_chunk_id=uuid4(),
        rule_type=RegulationRuleType.PROHIBITION,
        topic="用户同意",
        subject="个人信息处理者",
        action="不得默认勾选",
        object="同意选项",
        condition=None,
        time_limit=None,
        requirements=[],
        restrictions=["不得默认勾选"],
        exceptions=[],
        consequences=[],
        source_text="个人信息处理者不得通过默认勾选方式取得同意。",
    )


def test_rule_index_builds_one_search_document_per_atomic_rule() -> None:
    regulation = make_regulation()
    rule = make_rule()
    embedding = SimpleNamespace(embed_documents=AsyncMock(return_value=[[0.1, 0.2]]))
    store = SimpleNamespace()
    service = RegulationRuleIndexService(embedding=embedding, vector_store=store)

    documents = asyncio.run(
        service.build_documents(regulation=regulation, rules=[rule])
    )

    assert len(documents) == 1
    assert documents[0]["rule_id"] == str(rule.id)
    assert documents[0]["regulation_id"] == str(regulation.id)
    assert documents[0]["rule_type"] == "PROHIBITION"
    assert documents[0]["content"] == rule.source_text
    assert documents[0]["embedding"] == [0.1, 0.2]
    embedding.embed_documents.assert_awaited_once()
    assert "不得默认勾选" in embedding.embed_documents.await_args.args[0][0]


def test_rule_search_forwards_authoritative_scope_to_vector_store() -> None:
    rule_id = uuid4()
    user_id = uuid4()
    regulation_id = uuid4()
    embedding = SimpleNamespace(embed_query=AsyncMock(return_value=[0.1, 0.2]))
    store = SimpleNamespace(
        search_similar=AsyncMock(
            return_value=[{"rule_id": str(rule_id), "score": 0.25}]
        )
    )
    service = RegulationRuleIndexService(embedding=embedding, vector_store=store)

    result = asyncio.run(
        service.search(
            user_id=user_id,
            query="默认勾选是否合法",
            top_k=20,
            audit_as_of=date(2026, 8, 27),
            regulation_ids=[regulation_id],
            categories=["PUBLIC_KNOWLEDGE"],
            jurisdictions=["CN"],
            rule_types=["PROHIBITION"],
        )
    )

    assert result[0].rule_id == rule_id
    store.search_similar.assert_awaited_once_with(
        query_text="默认勾选是否合法",
        query_vector=[0.1, 0.2],
        user_id=str(user_id),
        top_k=20,
        audit_as_of=date(2026, 8, 27),
        regulation_ids=[str(regulation_id)],
        categories=["PUBLIC_KNOWLEDGE"],
        jurisdictions=["CN"],
        rule_types=["PROHIBITION"],
    )


class FakeUow:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False


def test_rule_publication_marks_ready_only_after_es_replace() -> None:
    regulation = make_regulation()
    regulation.rule_started_at = object()
    regulation.rule_status = RegulationRuleStatus.PROCESSING
    rule = make_rule()
    repository = SimpleNamespace(
        find_by_id_and_user_for_update=AsyncMock(return_value=regulation)
    )
    rule_repository = SimpleNamespace(replace_by_regulation=AsyncMock())
    index_service = SimpleNamespace(
        build_documents=AsyncMock(return_value=[{"rule_id": str(rule.id)}]),
        replace_regulation_rules=AsyncMock(),
    )

    result = asyncio.run(
        publish_regulation_rules(
            uow=FakeUow(),
            regulation_repository=repository,
            rule_repository=rule_repository,
            rule_index_service=index_service,
            regulation=regulation,
            rules=[rule],
            user_id=regulation.uploaded_by,
            expected_started_at=regulation.rule_started_at,
            expected_lock_version=regulation.lock_version,
        )
    )

    index_service.build_documents.assert_awaited_once()
    rule_repository.replace_by_regulation.assert_awaited_once()
    index_service.replace_regulation_rules.assert_awaited_once()
    assert result.rule_status.value == "READY"


def test_rule_publication_assigns_id_before_building_es_documents() -> None:
    """回归：SQLAlchemy 尚未 flush 的规则也必须携带有效 UUID 写入 ES。"""
    regulation = make_regulation()
    regulation.rule_started_at = object()
    regulation.rule_status = RegulationRuleStatus.PROCESSING
    rule = make_rule()
    rule.id = None

    async def assert_rule_id_is_ready(*, regulation, rules):
        assert regulation.id is not None
        assert rules[0].id is not None
        return [{"rule_id": str(rules[0].id)}]

    index_service = SimpleNamespace(
        build_documents=AsyncMock(side_effect=assert_rule_id_is_ready),
        replace_regulation_rules=AsyncMock(),
    )

    asyncio.run(
        publish_regulation_rules(
            uow=FakeUow(),
            regulation_repository=SimpleNamespace(
                find_by_id_and_user_for_update=AsyncMock(return_value=regulation)
            ),
            rule_repository=SimpleNamespace(replace_by_regulation=AsyncMock()),
            rule_index_service=index_service,
            regulation=regulation,
            rules=[rule],
            user_id=regulation.uploaded_by,
            expected_started_at=regulation.rule_started_at,
            expected_lock_version=regulation.lock_version,
        )
    )

    assert rule.id is not None
    index_service.replace_regulation_rules.assert_awaited_once_with(
        regulation_id=regulation.id,
        documents=[{"rule_id": str(rule.id)}],
    )


def test_rule_publication_rejects_same_version_after_status_was_reclaimed() -> None:
    """维护任务改掉 PROCESSING 后，即使时间和版本模拟相同也不能发布。"""
    regulation = make_regulation()
    regulation.rule_started_at = object()
    regulation.rule_status = RegulationRuleStatus.FAILED
    rule = make_rule()
    rule_repository = SimpleNamespace(replace_by_regulation=AsyncMock())

    with pytest.raises(BusinessException, match="regulation rule state has changed"):
        asyncio.run(
            publish_regulation_rules(
                uow=FakeUow(),
                regulation_repository=SimpleNamespace(
                    find_by_id_and_user_for_update=AsyncMock(return_value=regulation)
                ),
                rule_repository=rule_repository,
                rule_index_service=None,
                regulation=regulation,
                rules=[rule],
                user_id=regulation.uploaded_by,
                expected_started_at=regulation.rule_started_at,
                expected_lock_version=regulation.lock_version,
            )
        )

    rule_repository.replace_by_regulation.assert_not_awaited()


def test_rule_search_uses_vector_only_for_audit_page() -> None:
    rule_id = str(uuid4())
    client = SimpleNamespace(
        indices=SimpleNamespace(exists=AsyncMock(return_value=True)),
        search=AsyncMock(
            return_value={
                "hits": {
                    "hits": [
                        {
                            "_score": 0.91,
                            "_source": {"rule_id": rule_id, "content": "不得默认勾选"},
                        }
                    ]
                }
            }
        ),
    )
    store = RegulationRuleVectorStore(
        client=client,
        index_name="auditmind-regulation-rules",
        dimensions=2,
    )

    result = asyncio.run(
        store.search_similar(
            query_text="需要审计的整页内容" * 100,
            query_vector=[0.1, 0.2],
            user_id=str(uuid4()),
            top_k=30,
            audit_as_of=date(2026, 8, 27),
        )
    )

    assert result == [
        {"rule_id": rule_id, "content": "不得默认勾选", "score": 0.91}
    ]
    client.search.assert_awaited_once()
    client.indices.exists.assert_not_awaited()
    request = client.search.await_args.kwargs
    assert "knn" in request
    assert "query" not in request
    assert request["knn"]["k"] == 30


def test_rule_vector_store_uses_occ_for_replace_and_stale_delete() -> None:
    async def run_test() -> None:
        client = SimpleNamespace()
        store = RegulationRuleVectorStore(
            client=client,
            index_name="auditmind-regulation-rules",
            dimensions=2,
        )
        store.ensure_index = AsyncMock()
        store._load_regulation_document_versions = AsyncMock(
            return_value={"rule-1": (11, 3), "stale-rule": (12, 3)}
        )
        bulk = AsyncMock(return_value=(2, []))

        with patch(
            "app.infrastructure.regulation_rule_vector_store.async_bulk",
            new=bulk,
        ):
            await store.replace_regulation_rules(
                regulation_id="regulation-1",
                rules=[
                    {
                        "rule_id": "rule-1",
                        "regulation_id": "regulation-1",
                        "embedding": [0.1, 0.2],
                    }
                ],
            )

        actions = bulk.await_args.args[1]
        assert actions[0]["_op_type"] == "index"
        assert actions[0]["_if_seq_no"] == 11
        assert actions[0]["_if_primary_term"] == 3
        assert actions[1] == {
            "_op_type": "delete",
            "_index": "auditmind-regulation-rules",
            "_id": "stale-rule",
            "_if_seq_no": 12,
            "_if_primary_term": 3,
        }

    asyncio.run(run_test())


def test_rule_vector_store_creates_new_documents_without_stale_cas_token() -> None:
    async def run_test() -> None:
        store = RegulationRuleVectorStore(
            client=SimpleNamespace(),
            index_name="auditmind-regulation-rules",
            dimensions=2,
        )
        store.ensure_index = AsyncMock()
        store._load_regulation_document_versions = AsyncMock(return_value={})
        bulk = AsyncMock(return_value=(1, []))

        with patch(
            "app.infrastructure.regulation_rule_vector_store.async_bulk",
            new=bulk,
        ):
            await store.replace_regulation_rules(
                regulation_id="regulation-1",
                rules=[
                    {
                        "rule_id": "new-rule",
                        "regulation_id": "regulation-1",
                        "embedding": [0.1, 0.2],
                    }
                ],
            )

        action = bulk.await_args.args[1][0]
        assert action["_op_type"] == "create"
        assert "_if_seq_no" not in action
        assert "_if_primary_term" not in action

    asyncio.run(run_test())
