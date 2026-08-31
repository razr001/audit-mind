import asyncio
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException
from app.models.regulation import (
    KnowledgeCategory,
    KnowledgeVisibility,
    Regulation,
    RegulationChunkStatus,
    RegulationIndexStatus,
    RegulationRuleStatus,
    RegulationSourceType,
    RegulationStatus,
)
from app.repositories.regulation_management_repository import (
    RegulationManagementRepository,
)
from app.schemas.regulation import RegulationTextCreateRequest
from app.services.regulation_management_service import RegulationManagementService
from app.services.regulation_text_service import RegulationTextService

USER_ID = UUID("9efa0f2d-7e1f-4204-8d0e-f254e36c8e59")


class FakeUnitOfWork:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False


class FakeDeletionCoordinator:
    def __init__(self, *, acquired: bool = True, reason: str | None = None) -> None:
        self.guard = SimpleNamespace(acquired=acquired, reason=reason)
        self.active = False

    @asynccontextmanager
    async def acquire(self, _regulation_id: UUID):
        self.active = True
        try:
            yield self.guard
        finally:
            self.active = False


def test_text_knowledge_preserves_source_and_skips_mineru() -> None:
    repository = SimpleNamespace(
        find_duplicate_by_content_hash=AsyncMock(return_value=None),
        save=AsyncMock(),
    )
    parse_blocks = SimpleNamespace(replace_by_regulation=AsyncMock())
    storage = SimpleNamespace(upload_text=AsyncMock(return_value="regulations/text.md"))
    audit = SimpleNamespace(record_regulation_created=AsyncMock())
    service = RegulationTextService(
        uow=FakeUnitOfWork(),
        repository=repository,
        parse_block_repository=parse_blocks,
        storage=storage,
        operation_audit=audit,
    )
    source = "# 内部规则\n\n员工不得向外部披露客户资料。\n"
    request = RegulationTextCreateRequest(
        title="内部数据规则",
        content=source,
        source_type=RegulationSourceType.INTERNAL_POLICY,
        visibility=KnowledgeVisibility.PRIVATE,
    )

    regulation = asyncio.run(
        service.create(request=request, user_id=USER_ID, request_id="request-1")
    )

    assert regulation.status == RegulationStatus.READY
    assert regulation.language == "zh-CN"
    assert regulation.parse_task_id is None
    assert regulation.original_filename == "内部数据规则.md"
    storage.upload_text.assert_awaited_once_with(data=source.encode("utf-8"))
    block = parse_blocks.replace_by_regulation.await_args.kwargs["blocks"][0]
    assert block.content == source
    assert (block.char_start, block.char_end) == (0, len(source))
    assert block.block_metadata == {"sourceFormat": "markdown"}
    audit.record_regulation_created.assert_awaited_once()


def test_text_language_detection_runs_outside_event_loop_thread() -> None:
    repository = SimpleNamespace(
        find_duplicate_by_content_hash=AsyncMock(return_value=None),
        save=AsyncMock(),
    )
    service = RegulationTextService(
        uow=FakeUnitOfWork(),
        repository=repository,
        parse_block_repository=SimpleNamespace(replace_by_regulation=AsyncMock()),
        storage=SimpleNamespace(upload_text=AsyncMock(return_value="regulations/text.md")),
        operation_audit=SimpleNamespace(record_regulation_created=AsyncMock()),
    )
    caller_thread = threading.get_ident()
    detection_threads = []

    def detect_in_worker(*_args, **_kwargs):
        detection_threads.append(threading.get_ident())
        return "zh-CN"

    request = RegulationTextCreateRequest(
        title="线程测试",
        content="这是用于识别语言的法规正文。",
        source_type=RegulationSourceType.INTERNAL_POLICY,
        visibility=KnowledgeVisibility.PRIVATE,
    )
    with patch(
        "app.services.regulation_text_service.detect_content_language",
        side_effect=detect_in_worker,
    ):
        asyncio.run(service.create(request=request, user_id=USER_ID))

    assert detection_threads
    assert detection_threads[0] != caller_thread


def test_text_knowledge_rejects_unsafe_control_characters() -> None:
    with pytest.raises(ValidationError, match="unsafe control characters"):
        RegulationTextCreateRequest(
            title="控制字符测试",
            content="合法正文\x00隐藏内容",
            source_type=RegulationSourceType.INTERNAL_POLICY,
            visibility=KnowledgeVisibility.PRIVATE,
        )

    multiline = RegulationTextCreateRequest(
        title="Markdown 换行测试",
        content="# 标题\n\n正文\t内容",
        source_type=RegulationSourceType.INTERNAL_POLICY,
        visibility=KnowledgeVisibility.PRIVATE,
    )
    assert multiline.content == "# 标题\n\n正文\t内容"


def test_text_knowledge_keeps_storage_object_after_database_failure() -> None:
    repository = SimpleNamespace(
        find_duplicate_by_content_hash=AsyncMock(return_value=None),
        save=AsyncMock(side_effect=RuntimeError("database unavailable")),
    )
    storage = SimpleNamespace(
        upload_text=AsyncMock(return_value="regulations/text.md"),
        remove=AsyncMock(),
    )
    service = RegulationTextService(
        uow=FakeUnitOfWork(),
        repository=repository,
        parse_block_repository=SimpleNamespace(replace_by_regulation=AsyncMock()),
        storage=storage,
        operation_audit=SimpleNamespace(record_regulation_created=AsyncMock()),
    )
    request = RegulationTextCreateRequest(
        title="内部数据规则",
        content="员工不得向外部披露客户资料。",
        source_type=RegulationSourceType.INTERNAL_POLICY,
        visibility=KnowledgeVisibility.PRIVATE,
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(
            service.create(request=request, user_id=USER_ID, request_id="request-failed")
        )

    storage.remove.assert_not_awaited()


def test_delete_regulation_removes_fact_storage_and_search_copies() -> None:
    regulation_id = uuid4()
    regulation = SimpleNamespace(
        id=regulation_id,
        lock_version=1,
        storage_key="regulations/source.pdf",
        status=RegulationStatus.READY,
        chunk_status=RegulationChunkStatus.READY,
        index_status=RegulationIndexStatus.READY,
        rule_status=RegulationRuleStatus.READY,
    )
    repository = SimpleNamespace(
        claim_for_deletion=AsyncMock(return_value=regulation),
        find_by_id_and_user_for_update=AsyncMock(return_value=regulation),
        delete_if_lock_version=AsyncMock(return_value=True),
    )
    rule_repository = SimpleNamespace(delete_by_regulation=AsyncMock())
    operation_audit = SimpleNamespace(record_regulation_deleted=AsyncMock())
    storage = SimpleNamespace(
        remove=AsyncMock(),
        remove_parse_assets=AsyncMock(),
    )
    chunk_vector_store = SimpleNamespace(delete_regulation_chunks=AsyncMock())
    rule_vector_store = SimpleNamespace(delete_regulation_rules=AsyncMock())
    service = RegulationManagementService(
        uow=FakeUnitOfWork(),
        repository=repository,
        rule_repository=rule_repository,
        operation_audit=operation_audit,
        storage=storage,
        chunk_vector_store=chunk_vector_store,
        rule_vector_store=rule_vector_store,
        deletion_coordinator=FakeDeletionCoordinator(),
    )

    deleted_id = asyncio.run(
        service.delete(
            regulation_id=regulation_id,
            user_id=USER_ID,
            request_id="request-delete",
        )
    )

    assert deleted_id == regulation_id
    repository.claim_for_deletion.assert_awaited_once_with(
        regulation_id=regulation_id, user_id=USER_ID
    )
    assert repository.find_by_id_and_user_for_update.await_count == 1
    repository.find_by_id_and_user_for_update.assert_awaited_with(
        regulation_id=regulation_id, user_id=USER_ID
    )
    operation_audit.record_regulation_deleted.assert_awaited_once_with(
        regulation=regulation,
        user_id=USER_ID,
        request_id="request-delete",
    )
    rule_repository.delete_by_regulation.assert_awaited_once_with(regulation_id)
    repository.delete_if_lock_version.assert_awaited_once_with(
        regulation_id=regulation_id,
        user_id=USER_ID,
        expected_lock_version=1,
    )
    chunk_vector_store.delete_regulation_chunks.assert_awaited_once_with(
        regulation_id=str(regulation_id),
    )
    rule_vector_store.delete_regulation_rules.assert_awaited_once_with(
        regulation_id=str(regulation_id),
    )
    storage.remove.assert_awaited_once_with("regulations/source.pdf")
    storage.remove_parse_assets.assert_awaited_once_with(regulation_id)


def test_delete_regulation_rejects_processing_status_after_lock_acquired() -> None:
    regulation_id = uuid4()
    regulation = SimpleNamespace(
        id=regulation_id,
        lock_version=1,
        storage_key="regulations/source.pdf",
        status=RegulationStatus.PARSING,
        chunk_status=RegulationChunkStatus.PENDING,
        index_status=RegulationIndexStatus.PENDING,
        rule_status=RegulationRuleStatus.PENDING,
    )
    repository = SimpleNamespace(
        claim_for_deletion=AsyncMock(return_value=None),
        find_by_id_and_user=AsyncMock(return_value=regulation),
        delete_if_lock_version=AsyncMock(return_value=True),
    )
    rule_repository = SimpleNamespace(delete_by_regulation=AsyncMock())
    operation_audit = SimpleNamespace(record_regulation_deleted=AsyncMock())
    storage = SimpleNamespace(
        remove=AsyncMock(),
        remove_parse_assets=AsyncMock(),
    )
    chunk_vector_store = SimpleNamespace(delete_regulation_chunks=AsyncMock())
    rule_vector_store = SimpleNamespace(delete_regulation_rules=AsyncMock())
    service = RegulationManagementService(
        uow=FakeUnitOfWork(),
        repository=repository,
        rule_repository=rule_repository,
        operation_audit=operation_audit,
        storage=storage,
        chunk_vector_store=chunk_vector_store,
        rule_vector_store=rule_vector_store,
        deletion_coordinator=FakeDeletionCoordinator(),
    )

    with pytest.raises(BusinessException, match="cannot be deleted while processing"):
        asyncio.run(
            service.delete(
                regulation_id=regulation_id,
                user_id=USER_ID,
                request_id="request-delete-processing",
            )
        )

    repository.delete_if_lock_version.assert_not_awaited()
    chunk_vector_store.delete_regulation_chunks.assert_not_awaited()
    rule_vector_store.delete_regulation_rules.assert_not_awaited()


def test_delete_regulation_rejects_same_regulation_processing_before_database_access() -> None:
    regulation_id = uuid4()
    repository = SimpleNamespace(
        claim_for_deletion=AsyncMock(),
        find_by_id_and_user_for_update=AsyncMock(),
        delete_if_lock_version=AsyncMock(),
    )
    service = RegulationManagementService(
        uow=FakeUnitOfWork(),
        repository=repository,
        rule_repository=SimpleNamespace(delete_by_regulation=AsyncMock()),
        operation_audit=SimpleNamespace(record_regulation_deleted=AsyncMock()),
        storage=SimpleNamespace(remove=AsyncMock(), remove_parse_assets=AsyncMock()),
        chunk_vector_store=SimpleNamespace(delete_regulation_chunks=AsyncMock()),
        rule_vector_store=SimpleNamespace(delete_regulation_rules=AsyncMock()),
        deletion_coordinator=FakeDeletionCoordinator(
            acquired=False,
            reason="regulation_processing",
        ),
    )

    with pytest.raises(BusinessException):
        asyncio.run(
            service.delete(
                regulation_id=regulation_id,
                user_id=USER_ID,
                request_id="request-delete-audit-running",
            )
        )

    repository.claim_for_deletion.assert_not_awaited()
    repository.find_by_id_and_user_for_update.assert_not_awaited()
    repository.delete_if_lock_version.assert_not_awaited()


def test_delete_regulation_keeps_postgres_when_es_cleanup_fails() -> None:
    regulation_id = uuid4()
    regulation = SimpleNamespace(
        id=regulation_id,
        lock_version=1,
        storage_key="regulations/source.pdf",
        status=RegulationStatus.READY,
        chunk_status=RegulationChunkStatus.READY,
        index_status=RegulationIndexStatus.READY,
        rule_status=RegulationRuleStatus.READY,
    )
    repository = SimpleNamespace(
        claim_for_deletion=AsyncMock(return_value=regulation),
        find_by_id_and_user_for_update=AsyncMock(return_value=regulation),
        delete_if_lock_version=AsyncMock(return_value=True),
    )
    rule_repository = SimpleNamespace(delete_by_regulation=AsyncMock())
    operation_audit = SimpleNamespace(record_regulation_deleted=AsyncMock())
    storage = SimpleNamespace(
        remove=AsyncMock(),
        remove_parse_assets=AsyncMock(),
    )
    chunk_vector_store = SimpleNamespace(
        delete_regulation_chunks=AsyncMock(side_effect=RuntimeError("ES unavailable"))
    )
    rule_vector_store = SimpleNamespace(delete_regulation_rules=AsyncMock())
    service = RegulationManagementService(
        uow=FakeUnitOfWork(),
        repository=repository,
        rule_repository=rule_repository,
        operation_audit=operation_audit,
        storage=storage,
        chunk_vector_store=chunk_vector_store,
        rule_vector_store=rule_vector_store,
        deletion_coordinator=FakeDeletionCoordinator(),
    )

    with pytest.raises(RuntimeError, match="ES unavailable"):
        asyncio.run(
            service.delete(
                regulation_id=regulation_id,
                user_id=USER_ID,
                request_id="request-delete-es-failed",
            )
        )

    repository.delete_if_lock_version.assert_not_awaited()
    rule_repository.delete_by_regulation.assert_not_awaited()
    operation_audit.record_regulation_deleted.assert_not_awaited()
    rule_vector_store.delete_regulation_rules.assert_not_awaited()
    storage.remove.assert_not_awaited()
    storage.remove_parse_assets.assert_not_awaited()


def test_es_delete_failure_persists_deleting_intent_for_retry() -> None:
    """M3 回归：ES 失败不能让法规继续保持 READY，也不能删除事实记录。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Regulation.__table__.create(engine)
    regulation_id = uuid4()

    with Session(engine) as session:
        session.add(
            Regulation(
                id=regulation_id,
                title="删除恢复测试",
                source_type=RegulationSourceType.REGULATION,
                category=KnowledgeCategory.PUBLIC_KNOWLEDGE,
                visibility=KnowledgeVisibility.SHARED,
                language="zh-CN",
                jurisdiction="CN",
                storage_key=f"regulations/{regulation_id}.pdf",
                original_filename="删除恢复测试.pdf",
                content_type="application/pdf",
                file_size=10,
                content_hash="c" * 64,
                    uploaded_by=USER_ID,
                    enabled=True,
                    status=RegulationStatus.READY,
                    chunk_status=RegulationChunkStatus.READY,
                    index_status=RegulationIndexStatus.READY,
                    rule_status=RegulationRuleStatus.READY,
            )
        )
        session.commit()

        class AsyncSessionAdapter:
            async def execute(self, statement):
                return session.execute(statement)

        class SyncSessionUnitOfWork:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_value, traceback):
                if exc_type:
                    session.rollback()
                else:
                    session.commit()
                return False

        repository = RegulationManagementRepository(AsyncSessionAdapter())  # type: ignore[arg-type]
        service = RegulationManagementService(
            uow=SyncSessionUnitOfWork(),  # type: ignore[arg-type]
            repository=repository,
            rule_repository=SimpleNamespace(delete_by_regulation=AsyncMock()),
            operation_audit=SimpleNamespace(record_regulation_deleted=AsyncMock()),
            storage=SimpleNamespace(remove=AsyncMock(), remove_parse_assets=AsyncMock()),
            chunk_vector_store=SimpleNamespace(
                delete_regulation_chunks=AsyncMock(
                    side_effect=RuntimeError("ES unavailable")
                )
            ),
            rule_vector_store=SimpleNamespace(delete_regulation_rules=AsyncMock()),
            deletion_coordinator=FakeDeletionCoordinator(),
        )

        with pytest.raises(RuntimeError, match="ES unavailable"):
            asyncio.run(
                service.delete(
                    regulation_id=regulation_id,
                    user_id=USER_ID,
                    request_id="request-delete-intent",
                )
            )

        session.expire_all()
        remaining = session.get(Regulation, regulation_id)
        assert remaining is not None
        assert remaining.status is RegulationStatus.DELETING
        assert remaining.enabled is False
        assert remaining.lock_version == 1


def test_delete_regulation_stops_when_fencing_version_is_superseded() -> None:
    """租约过期后新执行者递增版本，旧删除者不得删除数据库和文件。"""
    regulation_id = uuid4()
    claimed = SimpleNamespace(
        id=regulation_id,
        lock_version=4,
        storage_key="regulations/source.pdf",
        title="法规",
        source_type=RegulationSourceType.REGULATION,
        visibility=KnowledgeVisibility.SHARED,
        original_filename="法规.pdf",
        content_type="application/pdf",
    )
    current = SimpleNamespace(**{**vars(claimed), "lock_version": 5})
    repository = SimpleNamespace(
        claim_for_deletion=AsyncMock(return_value=claimed),
        find_by_id_and_user_for_update=AsyncMock(return_value=current),
        delete_if_lock_version=AsyncMock(return_value=False),
    )
    storage = SimpleNamespace(remove=AsyncMock(), remove_parse_assets=AsyncMock())
    service = RegulationManagementService(
        uow=FakeUnitOfWork(),
        repository=repository,
        rule_repository=SimpleNamespace(delete_by_regulation=AsyncMock()),
        operation_audit=SimpleNamespace(record_regulation_deleted=AsyncMock()),
        storage=storage,
        chunk_vector_store=SimpleNamespace(delete_regulation_chunks=AsyncMock()),
        rule_vector_store=SimpleNamespace(delete_regulation_rules=AsyncMock()),
        deletion_coordinator=FakeDeletionCoordinator(),
    )

    with pytest.raises(BusinessException, match="execution lease was superseded"):
        asyncio.run(
            service.delete(
                regulation_id=regulation_id,
                user_id=USER_ID,
                request_id="request-delete-superseded",
            )
        )

    repository.delete_if_lock_version.assert_awaited_once_with(
        regulation_id=regulation_id,
        user_id=USER_ID,
        expected_lock_version=4,
    )
    storage.remove.assert_not_awaited()
    storage.remove_parse_assets.assert_not_awaited()


@pytest.mark.parametrize(
    "processing_state",
    [
        {"status": RegulationStatus.UPLOADED},
        {"status": RegulationStatus.PARSING},
        {"chunk_status": RegulationChunkStatus.PENDING},
        {"chunk_status": RegulationChunkStatus.PROCESSING},
        {"index_status": RegulationIndexStatus.PENDING},
        {"index_status": RegulationIndexStatus.PROCESSING},
        {"rule_status": RegulationRuleStatus.PENDING},
        {"rule_status": RegulationRuleStatus.PROCESSING},
    ],
)
def test_regulation_deletion_repository_rejects_every_processing_stage(
    processing_state,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Regulation.__table__.create(engine)
    regulation_id = uuid4()
    with Session(engine) as session:
        regulation = Regulation(
            id=regulation_id,
            title="处理中法规",
            source_type=RegulationSourceType.REGULATION,
            category=KnowledgeCategory.PUBLIC_KNOWLEDGE,
            visibility=KnowledgeVisibility.SHARED,
            language="zh-CN",
            jurisdiction="CN",
            storage_key=f"regulations/{regulation_id}.pdf",
            original_filename="处理中法规.pdf",
            content_type="application/pdf",
            file_size=10,
            content_hash="d" * 64,
            uploaded_by=USER_ID,
            status=RegulationStatus.READY,
            chunk_status=RegulationChunkStatus.READY,
            index_status=RegulationIndexStatus.READY,
            rule_status=RegulationRuleStatus.READY,
        )
        for field_name, value in processing_state.items():
            setattr(regulation, field_name, value)
        session.add(regulation)
        session.commit()

        class AsyncSessionAdapter:
            async def execute(self, statement):
                return session.execute(statement)

        claimed = asyncio.run(
            RegulationManagementRepository(  # type: ignore[arg-type]
                AsyncSessionAdapter()
            ).claim_for_deletion(
                regulation_id=regulation_id,
                user_id=USER_ID,
            )
        )

        assert claimed is None
        session.expire_all()
        remaining = session.get(Regulation, regulation_id)
        assert remaining is not None
        assert remaining.enabled is True
        assert remaining.status is not RegulationStatus.DELETING


def test_regulation_deletion_repository_rejects_stale_fencing_version() -> None:
    """真实执行条件 DELETE，证明旧版本无法删除新执行者接管后的记录。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Regulation.__table__.create(engine)
    regulation_id = uuid4()
    with Session(engine) as session:
        session.add(
            Regulation(
                id=regulation_id,
                title="测试法规",
                source_type=RegulationSourceType.REGULATION,
                category=KnowledgeCategory.PUBLIC_KNOWLEDGE,
                visibility=KnowledgeVisibility.SHARED,
                language="zh-CN",
                jurisdiction="CN",
                storage_key=f"regulations/{regulation_id}.pdf",
                original_filename="测试法规.pdf",
                content_type="application/pdf",
                file_size=10,
                    content_hash="a" * 64,
                    uploaded_by=USER_ID,
                    status=RegulationStatus.READY,
                    chunk_status=RegulationChunkStatus.READY,
                    index_status=RegulationIndexStatus.READY,
                    rule_status=RegulationRuleStatus.READY,
                )
        )
        session.commit()

        class AsyncSessionAdapter:
            async def execute(self, statement):
                return session.execute(statement)

        repository = RegulationManagementRepository(AsyncSessionAdapter())  # type: ignore[arg-type]
        claimed = asyncio.run(
            repository.claim_for_deletion(
                regulation_id=regulation_id,
                user_id=USER_ID,
            )
        )
        assert claimed is not None
        assert claimed.lock_version == 1
        assert claimed.status is RegulationStatus.DELETING
        assert claimed.enabled is False
        session.commit()

        # 模拟 Redis 租约过期后，新执行者领取了更高的数据库版本。
        session.execute(
            update(Regulation)
            .where(Regulation.id == regulation_id)
            .values(lock_version=2)
        )
        session.commit()

        stale_deleted = asyncio.run(
            repository.delete_if_lock_version(
                regulation_id=regulation_id,
                user_id=USER_ID,
                expected_lock_version=1,
            )
        )
        assert stale_deleted is False
        assert session.get(Regulation, regulation_id) is not None

        current_deleted = asyncio.run(
            repository.delete_if_lock_version(
                regulation_id=regulation_id,
                user_id=USER_ID,
                expected_lock_version=2,
            )
        )
        assert current_deleted is True
        session.commit()
        assert session.get(Regulation, regulation_id) is None


def test_deleting_regulation_remains_visible_only_to_its_uploader() -> None:
    """删除失败后上传者仍有重试入口，其他用户不能看到删除中的知识。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Regulation.__table__.create(engine)
    regulation_id = uuid4()
    other_user_id = uuid4()
    with Session(engine) as session:
        session.add(
            Regulation(
                id=regulation_id,
                title="待删除法规",
                source_type=RegulationSourceType.REGULATION,
                category=KnowledgeCategory.PUBLIC_KNOWLEDGE,
                visibility=KnowledgeVisibility.SHARED,
                language="zh-CN",
                jurisdiction="CN",
                storage_key=f"regulations/{regulation_id}.pdf",
                original_filename="待删除法规.pdf",
                content_type="application/pdf",
                file_size=10,
                content_hash="b" * 64,
                uploaded_by=USER_ID,
                enabled=False,
                status=RegulationStatus.DELETING,
            )
        )
        session.commit()

        class AsyncSessionAdapter:
            async def execute(self, statement):
                return session.execute(statement)

            async def scalar(self, statement):
                return session.scalar(statement)

        repository = RegulationManagementRepository(AsyncSessionAdapter())  # type: ignore[arg-type]
        owner_items, owner_total = asyncio.run(
            repository.find_accessible_page(
                user_id=USER_ID,
                offset=0,
                limit=20,
            )
        )
        other_items, other_total = asyncio.run(
            repository.find_accessible_page(
                user_id=other_user_id,
                offset=0,
                limit=20,
            )
        )

        assert [item.id for item in owner_items] == [regulation_id]
        assert owner_total == 1
        assert other_items == []
        assert other_total == 0


def test_delete_regulation_keeps_total_lock_during_file_cleanup_failure() -> None:
    regulation_id = uuid4()
    regulation = SimpleNamespace(
        id=regulation_id,
        lock_version=1,
        storage_key="regulations/source.pdf",
        status=RegulationStatus.READY,
        chunk_status=RegulationChunkStatus.READY,
        index_status=RegulationIndexStatus.READY,
        rule_status=RegulationRuleStatus.READY,
    )
    repository = SimpleNamespace(
        claim_for_deletion=AsyncMock(return_value=regulation),
        find_by_id_and_user_for_update=AsyncMock(return_value=regulation),
        delete_if_lock_version=AsyncMock(return_value=True),
    )
    rule_repository = SimpleNamespace(delete_by_regulation=AsyncMock())
    operation_audit = SimpleNamespace(record_regulation_deleted=AsyncMock())
    coordinator = FakeDeletionCoordinator()

    observed_guard_states: list[bool] = []

    def fail_while_guard_is_held(_storage_key: str) -> None:
        observed_guard_states.append(coordinator.active)
        raise RuntimeError("MinIO unavailable")

    storage = SimpleNamespace(
        remove=AsyncMock(side_effect=fail_while_guard_is_held),
        remove_parse_assets=AsyncMock(),
    )
    chunk_vector_store = SimpleNamespace(delete_regulation_chunks=AsyncMock())
    rule_vector_store = SimpleNamespace(delete_regulation_rules=AsyncMock())
    service = RegulationManagementService(
        uow=FakeUnitOfWork(),
        repository=repository,
        rule_repository=rule_repository,
        operation_audit=operation_audit,
        storage=storage,
        chunk_vector_store=chunk_vector_store,
        rule_vector_store=rule_vector_store,
        deletion_coordinator=coordinator,
    )

    deleted_id = asyncio.run(
        service.delete(
            regulation_id=regulation_id,
            user_id=USER_ID,
            request_id="request-delete-minio-failed",
        )
    )

    assert deleted_id == regulation_id
    repository.delete_if_lock_version.assert_awaited_once()
    rule_repository.delete_by_regulation.assert_awaited_once_with(regulation_id)
    storage.remove_parse_assets.assert_awaited_once_with(regulation_id)
    assert observed_guard_states == [True]
