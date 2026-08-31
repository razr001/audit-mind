import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.audit_maintenance import router
from app.core.config import get_settings
from app.infrastructure.redis_lock import acquire_redis_lease
from app.models.audit_task import AuditStage, AuditStatus, AuditTask
from app.models.audit_task_page import AuditTaskPage, AuditTaskPageStatus
from app.models.document import Document, DocumentStatus
from app.repositories.audit_maintenance_repository import AuditMaintenanceRepository
from app.repositories.audit_result_repository import AuditResultRepository
from app.schemas.audit_maintenance import AuditTimeoutStage
from app.services.audit_maintenance_service import (
    AuditMaintenanceService,
    get_audit_maintenance_service,
)
from app.services.audit_progress_service import AuditProgressService


class AsyncContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def test_audit_maintenance_uses_separate_pipeline_and_page_timeouts():
    task_id = uuid4()
    repository = SimpleNamespace(
        find_stale_task_ids=AsyncMock(return_value=[task_id]),
        mark_stale_failed=AsyncMock(return_value=2),
    )
    settings = SimpleNamespace(
        AUDIT_TASK_STALE_SECONDS=3600,
        AUDIT_PAGE_STALE_SECONDS=900,
    )
    service = AuditMaintenanceService(
        uow=AsyncContext(),
        repository=repository,
        settings=settings,
    )

    before = datetime.now(timezone.utc)

    @asynccontextmanager
    async def acquire(_task_id):
        yield True

    with patch(
        "app.services.audit_maintenance_service.acquire_audit_pipeline_lease",
        new=acquire,
    ):
        result = asyncio.run(service.mark_timed_out_failed(stage=AuditTimeoutStage.PAGE))

    assert result.updated_count == 2
    assert before - timedelta(seconds=900) <= result.stale_before
    repository.find_stale_task_ids.assert_awaited_once()
    repository.mark_stale_failed.assert_awaited_once()


def test_audit_maintenance_api_reuses_scheduler_authentication():
    application = FastAPI()
    application.include_router(router)
    settings = SimpleNamespace(SCHEDULER_ACCESS_TOKEN=SecretStr("a" * 32))
    result = SimpleNamespace(
        stage=AuditTimeoutStage.PIPELINE,
        stale_before=datetime.now(timezone.utc),
        updated_count=1,
    )
    service = SimpleNamespace(mark_timed_out_failed=AsyncMock(return_value=result))
    application.dependency_overrides[get_settings] = lambda: settings
    application.dependency_overrides[get_audit_maintenance_service] = lambda: service
    client = TestClient(application)

    assert client.post("/internal/audit/tasks/timeout/pipeline").status_code == 401
    response = client.post(
        "/internal/audit/tasks/timeout/pipeline",
        headers={"X-Internal-Token": "a" * 32},
    )

    assert response.status_code == 200
    assert response.json()["data"]["updatedCount"] == 1
    service.mark_timed_out_failed.assert_awaited_once_with(stage=AuditTimeoutStage.PIPELINE)


def test_audit_maintenance_skips_task_while_pipeline_holds_same_redis_lock() -> None:
    """XXL-JOB 拿不到任务总锁时必须跳过，且不能触碰数据库状态。"""
    task_id = uuid4()

    class FakeRedisBackend:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        async def set(self, key, value, *, nx, ex):
            if nx and key in self.values:
                return False
            self.values[key] = value
            return True

        async def get(self, key):
            return self.values.get(key)

        async def eval(self, script, _key_count, key, token, *args):
            if self.values.get(key) != token:
                return 0
            if "redis.call('del'" in script:
                del self.values[key]
            return 1

    backend = FakeRedisBackend()
    client = SimpleNamespace(client=backend)
    repository = SimpleNamespace(
        find_stale_task_ids=AsyncMock(return_value=[task_id]),
        mark_stale_failed=AsyncMock(return_value=1),
    )
    service = AuditMaintenanceService(
        uow=AsyncContext(),
        repository=repository,
        settings=SimpleNamespace(
            AUDIT_TASK_STALE_SECONDS=3600,
            AUDIT_PAGE_STALE_SECONDS=600,
        ),
    )

    def acquire_test_lease(candidate_task_id):
        return acquire_redis_lease(
            key=f"lock:audit:pipeline:{candidate_task_id}",
            ttl_seconds=300,
            client=client,
        )

    async def run_test() -> None:
        async with acquire_test_lease(task_id) as owner_acquired:
            assert owner_acquired is True
            with patch(
                "app.services.audit_maintenance_service.acquire_audit_pipeline_lease",
                new=acquire_test_lease,
            ):
                result = await service.mark_timed_out_failed(stage=AuditTimeoutStage.PIPELINE)
        assert result.updated_count == 0
        repository.mark_stale_failed.assert_not_awaited()

    asyncio.run(run_test())


def test_page_timeout_fences_old_executor_from_all_later_page_writes() -> None:
    """维护接管后，旧版本既不能完成当前页，也不能领取下一页。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Document.__table__.create(engine)
    AuditTask.__table__.create(engine)
    AuditTaskPage.__table__.create(engine)
    now = datetime(2026, 8, 28, 2, 0, tzinfo=timezone.utc)
    stale_started_at = now - timedelta(hours=1)
    user_id = uuid4()
    document_id = uuid4()
    task_id = uuid4()
    running_page_id = uuid4()
    pending_page_id = uuid4()
    active_page_id = uuid4()

    with Session(engine) as session:
        session.execute(
            Document.__table__.insert(),
            {
                "id": document_id,
                "user_id": user_id,
                "original_filename": "audit.pdf",
                "storage_key": "documents/audit.pdf",
                "content_type": "application/pdf",
                "file_size": 10,
                "status": DocumentStatus.READY,
                "lock_version": 0,
                "created_at": now,
                "updated_at": now,
            },
        )
        session.execute(
            AuditTask.__table__.insert(),
            {
                "id": task_id,
                "document_id": document_id,
                "status": AuditStatus.RUNNING,
                "stage": AuditStage.AUDITING,
                "lock_version": 1,
                "created_at": now,
                "updated_at": stale_started_at,
            },
        )
        session.execute(
            AuditTaskPage.__table__.insert(),
            [
                {
                    "id": running_page_id,
                    "task_id": task_id,
                    "page_number": 1,
                    "status": AuditTaskPageStatus.RUNNING,
                    "started_at": stale_started_at,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": pending_page_id,
                    "task_id": task_id,
                    "page_number": 2,
                    "status": AuditTaskPageStatus.PENDING,
                    "started_at": None,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": active_page_id,
                    "task_id": task_id,
                    "page_number": 3,
                    "status": AuditTaskPageStatus.RUNNING,
                    "started_at": now,
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
        session.commit()

        class AsyncSessionAdapter:
            async def execute(self, statement):
                return session.execute(statement)

        adapter = AsyncSessionAdapter()
        maintenance = AuditMaintenanceRepository(adapter)
        results = AuditResultRepository(adapter)

        assert (
            asyncio.run(
                maintenance.mark_stale_failed(
                    task_id=task_id,
                    stage=AuditTimeoutStage.PAGE,
                    stale_before=now - timedelta(minutes=15),
                    completed_at=now,
                )
            )
            == 2
        )
        session.commit()

        # 旧执行版本为 1；维护接管后任务版本已经变为 2。
        assert (
            asyncio.run(
                results.complete_page(
                    page_id=running_page_id,
                    task_id=task_id,
                    expected_lock_version=1,
                    expected_started_at=stale_started_at,
                    finding_count=3,
                    completed_at=now,
                )
            )
            is None
        )
        assert (
            asyncio.run(
                results.claim_page(
                    page_id=pending_page_id,
                    task_id=task_id,
                    expected_lock_version=1,
                    started_at=now,
                )
            )
            is None
        )
        assert (
            asyncio.run(
                results.fail_page(
                    page_id=active_page_id,
                    task_id=task_id,
                    expected_lock_version=1,
                    expected_started_at=now,
                    error="stale executor",
                    completed_at=now,
                )
            )
            is None
        )

        session.expire_all()
        task = session.get(AuditTask, task_id)
        running_page = session.get(AuditTaskPage, running_page_id)
        pending_page = session.get(AuditTaskPage, pending_page_id)
        active_page = session.get(AuditTaskPage, active_page_id)
        assert task is not None and task.lock_version == 2
        assert task.status is AuditStatus.PARTIAL_FAILED
        assert running_page is not None and running_page.status is AuditTaskPageStatus.FAILED
        assert pending_page is not None and pending_page.status is AuditTaskPageStatus.PENDING
        assert active_page is not None and active_page.status is AuditTaskPageStatus.FAILED


def test_new_pipeline_owner_recovers_interrupted_running_pages() -> None:
    """新执行版本可恢复遗留页面，旧执行版本没有恢复权限。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Document.__table__.create(engine)
    AuditTask.__table__.create(engine)
    AuditTaskPage.__table__.create(engine)
    now = datetime(2026, 8, 28, 3, 0, tzinfo=timezone.utc)
    user_id = uuid4()
    document_id = uuid4()
    task_id = uuid4()
    running_page_id = uuid4()
    completed_page_id = uuid4()

    with Session(engine) as session:
        session.execute(
            Document.__table__.insert(),
            {
                "id": document_id,
                "user_id": user_id,
                "original_filename": "audit.pdf",
                "storage_key": "documents/retry-audit.pdf",
                "content_type": "application/pdf",
                "file_size": 10,
                "status": DocumentStatus.READY,
                "lock_version": 0,
                "created_at": now,
                "updated_at": now,
            },
        )
        session.execute(
            AuditTask.__table__.insert(),
            {
                "id": task_id,
                "document_id": document_id,
                "status": AuditStatus.RUNNING,
                "stage": AuditStage.AUDITING,
                "lock_version": 2,
                "created_at": now,
                "updated_at": now,
            },
        )
        session.execute(
            AuditTaskPage.__table__.insert(),
            [
                {
                    "id": running_page_id,
                    "task_id": task_id,
                    "page_number": 1,
                    "status": AuditTaskPageStatus.RUNNING,
                    "attempt_count": 1,
                    "finding_count": 2,
                    "error": "old worker stopped",
                    "started_at": now - timedelta(minutes=20),
                    "completed_at": now - timedelta(minutes=10),
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": completed_page_id,
                    "task_id": task_id,
                    "page_number": 2,
                    "status": AuditTaskPageStatus.COMPLETED,
                    "attempt_count": 1,
                    "finding_count": 3,
                    "error": None,
                    "started_at": now - timedelta(minutes=15),
                    "completed_at": now,
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
        session.commit()

        class AsyncSessionAdapter:
            async def execute(self, statement):
                return session.execute(statement)

        repository = AuditResultRepository(AsyncSessionAdapter())

        # 错误的执行版本没有恢复权限。
        assert (
            asyncio.run(
                repository.reset_interrupted_pages(
                    task_id=task_id,
                    expected_lock_version=1,
                )
            )
            == 0
        )
        assert (
            asyncio.run(
                repository.reset_interrupted_pages(
                    task_id=task_id,
                    expected_lock_version=2,
                )
            )
            == 1
        )
        session.commit()
        session.expire_all()

        recovered = session.get(AuditTaskPage, running_page_id)
        completed = session.get(AuditTaskPage, completed_page_id)
        assert recovered is not None
        assert recovered.status is AuditTaskPageStatus.PENDING
        assert recovered.attempt_count == 1
        assert recovered.finding_count == 0
        assert recovered.error is None
        assert recovered.started_at is None
        assert recovered.completed_at is None
        assert completed is not None
        assert completed.status is AuditTaskPageStatus.COMPLETED
        assert completed.finding_count == 3


def test_finalize_does_not_complete_task_with_unfinished_pages() -> None:
    """即使没有 FAILED，只要仍有 PENDING/RUNNING 页面就不能标记完成。"""
    task = SimpleNamespace(id=uuid4())
    updated_task = SimpleNamespace(id=task.id)
    result_repository = SimpleNamespace(
        summarize_pages=AsyncMock(return_value=(3, 2, 0, 4)),
    )
    task_repository = SimpleNamespace(
        update_pipeline_state=AsyncMock(return_value=updated_task),
    )
    service = AuditProgressService(
        uow=AsyncContext(),
        task_repository=task_repository,
        result_repository=result_repository,
    )

    result = asyncio.run(
        service.finalize(
            task=task,
            user_id=uuid4(),
            expected_lock_version=3,
        )
    )

    assert result is updated_task
    values = task_repository.update_pipeline_state.await_args.kwargs["values"]
    assert values["status"] is AuditStatus.PARTIAL_FAILED
    assert values["error"] is not None
