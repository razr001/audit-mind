import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.audit_failure import AUDIT_RULES_MAINTAINING_MESSAGE
from app.infrastructure.redis_lock import acquire_redis_lease as acquire_real_redis_lease
from app.infrastructure.regulation_deletion_coordinator import (
    RegulationDeletionCoordinator,
)
from app.infrastructure.regulation_pipeline_lock import (
    REGULATION_RULE_INDEX_MAINTENANCE_LOCK_KEY,
    is_regulation_rule_index_maintenance_active,
)
from app.models.audit_task import AuditStatus, AuditTask
from app.models.document import Document, DocumentStatus
from app.repositories.audit_task_repository import AuditTaskRepository
from app.services.audit_pipeline_service import run_audit_pipeline
from app.services.regulation_availability_service import (
    require_regulation_rules_available,
)

USER_ID = UUID("9efa0f2d-7e1f-4204-8d0e-f254e36c8e59")


class FakeRedisBackend:
    """实现 RedisLease 所需的原子 SET、续租和按 token 删除语义。"""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key, value, *, nx, ex):
        assert nx is True
        assert ex >= 3
        if key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def exists(self, key):
        return int(key in self.values)

    async def eval(self, script, key_count, key, token, *args):
        assert key_count == 1
        if self.values.get(key) != token:
            return 0
        if "redis.call('del'" in script:
            del self.values[key]
        return 1


def test_rules_available_guard_rejects_rule_index_maintenance() -> None:
    async def run_test() -> None:
        with patch(
            "app.services.regulation_availability_service.is_regulation_rule_index_maintenance_active",
            new=AsyncMock(return_value=True),
        ):
            try:
                await require_regulation_rules_available()
            except Exception as exc:
                assert getattr(exc, "code", None) == 50302
                assert str(exc) == "规则正在维护，请稍后再试"
            else:
                raise AssertionError("active regulation maintenance must reject audit")

    asyncio.run(run_test())


def test_audit_background_holds_task_lock_while_checking_rule_index_maintenance() -> None:
    async def run_test() -> None:
        task_id = uuid4()
        user_id = uuid4()
        with (
            patch(
                "app.services.audit_pipeline_service.is_regulation_rule_index_maintenance_active",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.services.audit_pipeline_service.acquire_audit_pipeline_lease"
            ) as lease,
            patch(
                "app.services.audit_pipeline_service._claim_audit_pipeline_execution",
                new=AsyncMock(return_value=7),
            ),
            patch(
                "app.services.audit_pipeline_service._fail_audit_for_rules_maintenance",
                new=AsyncMock(),
            ) as fail_task,
        ):
            lease.return_value.__aenter__ = AsyncMock(return_value=True)
            lease.return_value.__aexit__ = AsyncMock(return_value=False)
            await run_audit_pipeline(task_id=task_id, user_id=user_id)

        lease.assert_called_once()
        fail_task.assert_awaited_once_with(
            task_id=task_id,
            user_id=user_id,
            expected_lock_version=7,
        )

    asyncio.run(run_test())


def test_audit_lock_conflict_does_not_touch_database() -> None:
    """Redis 是所有后台动作的第一道门；抢锁失败时禁止领取数据库版本。"""

    async def run_test() -> None:
        claim_execution = AsyncMock()
        with (
            patch(
                "app.services.audit_pipeline_service.acquire_audit_pipeline_lease"
            ) as lease,
            patch(
                "app.services.audit_pipeline_service._claim_audit_pipeline_execution",
                new=claim_execution,
            ),
        ):
            lease.return_value.__aenter__ = AsyncMock(return_value=False)
            lease.return_value.__aexit__ = AsyncMock(return_value=False)
            await run_audit_pipeline(task_id=uuid4(), user_id=USER_ID)

        claim_execution.assert_not_awaited()

    asyncio.run(run_test())


def test_only_audit_lock_owner_can_record_rules_maintenance_failure() -> None:
    """两个进程同时执行同一任务时，只有取得总锁的一方可以写失败状态。"""

    async def run_test() -> None:
        task_id = uuid4()
        backend = FakeRedisBackend()
        client = SimpleNamespace(client=backend)
        maintenance_checked = asyncio.Event()
        release_owner = asyncio.Event()
        fail_task = AsyncMock()
        claim_execution = AsyncMock(return_value=9)

        def acquire_test_lease(task_id):
            return acquire_real_redis_lease(
                key=f"lock:audit:pipeline:{task_id}",
                ttl_seconds=300,
                client=client,
            )

        async def maintenance_active() -> bool:
            maintenance_checked.set()
            await release_owner.wait()
            return True

        with (
            patch(
                "app.services.audit_pipeline_service.acquire_audit_pipeline_lease",
                new=acquire_test_lease,
            ),
            patch(
                "app.services.audit_pipeline_service.is_regulation_rule_index_maintenance_active",
                new=maintenance_active,
            ),
            patch(
                "app.services.audit_pipeline_service._fail_audit_for_rules_maintenance",
                new=fail_task,
            ),
            patch(
                "app.services.audit_pipeline_service._claim_audit_pipeline_execution",
                new=claim_execution,
            ),
        ):
            owner = asyncio.create_task(
                run_audit_pipeline(task_id=task_id, user_id=USER_ID)
            )
            await asyncio.wait_for(maintenance_checked.wait(), timeout=1)

            # owner 仍持有锁，竞争者必须立即返回，不能执行维护检查或写任务。
            contender = asyncio.create_task(
                run_audit_pipeline(task_id=task_id, user_id=USER_ID)
            )
            await asyncio.wait_for(contender, timeout=1)
            release_owner.set()
            await asyncio.wait_for(owner, timeout=1)

        claim_execution.assert_awaited_once_with(task_id=task_id, user_id=USER_ID)
        fail_task.assert_awaited_once_with(
            task_id=task_id,
            user_id=USER_ID,
            expected_lock_version=9,
        )
        assert backend.values == {}

    asyncio.run(run_test())


def test_rule_index_maintenance_check_uses_fixed_key() -> None:
    async def run_test() -> None:
        backend = FakeRedisBackend()
        client = SimpleNamespace(client=backend)
        assert not await is_regulation_rule_index_maintenance_active(client=client)
        backend.values[REGULATION_RULE_INDEX_MAINTENANCE_LOCK_KEY] = "owner"
        assert await is_regulation_rule_index_maintenance_active(client=client)

    asyncio.run(run_test())


def test_regulation_deletion_does_not_scan_or_block_unrelated_audit() -> None:
    async def run_test() -> None:
        backend = FakeRedisBackend()
        backend.values["lock:audit:pipeline:unrelated"] = "audit-owner"
        coordinator = RegulationDeletionCoordinator(
            client=SimpleNamespace(client=backend),
        )

        async with coordinator.acquire(uuid4()) as guard:
            assert guard.acquired is True
            assert guard.reason is None

        assert backend.values["lock:audit:pipeline:unrelated"] == "audit-owner"

    asyncio.run(run_test())


def test_rules_maintenance_failure_updates_only_non_terminal_owned_tasks() -> None:
    """数据库条件必须同时保护用户边界和所有已经结束的任务状态。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Document.__table__.create(engine)
    AuditTask.__table__.create(engine)
    now = datetime(2026, 8, 28, 1, 0, tzinfo=timezone.utc)
    document_id = uuid4()
    other_document_id = uuid4()
    other_user_id = uuid4()
    task_ids = {status: uuid4() for status in AuditStatus}
    other_user_task_id = uuid4()

    with Session(engine) as session:
        session.execute(
            Document.__table__.insert(),
            [
                {
                    "id": document_id,
                    "user_id": USER_ID,
                    "original_filename": "audit.pdf",
                    "storage_key": "documents/audit.pdf",
                    "content_type": "application/pdf",
                    "file_size": 10,
                    "status": DocumentStatus.READY,
                    "lock_version": 0,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": other_document_id,
                    "user_id": other_user_id,
                    "original_filename": "other.pdf",
                    "storage_key": "documents/other.pdf",
                    "content_type": "application/pdf",
                    "file_size": 10,
                    "status": DocumentStatus.READY,
                    "lock_version": 0,
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
        session.execute(
            AuditTask.__table__.insert(),
            [
                {
                    "id": task_id,
                    "document_id": document_id,
                    "status": task_status,
                    "created_at": now,
                    "updated_at": now,
                }
                for task_status, task_id in task_ids.items()
            ]
            + [
                {
                    "id": other_user_task_id,
                    "document_id": other_document_id,
                    "status": AuditStatus.CREATED,
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
        session.commit()

        class AsyncSessionAdapter:
            async def execute(self, statement):
                return session.execute(statement)

        repository = AuditTaskRepository(AsyncSessionAdapter())

        async def fail(task_id, expected_lock_version=0, user_id=USER_ID):
            return await repository.fail_for_rules_maintenance(
                task_id=task_id,
                user_id=user_id,
                expected_lock_version=expected_lock_version,
                error=AUDIT_RULES_MAINTAINING_MESSAGE,
                completed_at=now,
            )

        assert asyncio.run(fail(task_ids[AuditStatus.CREATED])) is True
        first_version = asyncio.run(
            repository.claim_pipeline_execution(
                task_id=task_ids[AuditStatus.RUNNING],
                user_id=USER_ID,
                started_at=now,
                stale_before=now,
            )
        )
        second_version = asyncio.run(
            repository.claim_pipeline_execution(
                task_id=task_ids[AuditStatus.RUNNING],
                user_id=USER_ID,
                started_at=now,
                stale_before=now,
            )
        )
        assert first_version == 1
        assert second_version == 2
        # 新执行者领取版本后，旧执行者即使恢复也不能再写任务状态。
        stale_progress = asyncio.run(
            repository.update_pipeline_state(
                task_id=task_ids[AuditStatus.RUNNING],
                user_id=USER_ID,
                expected_lock_version=first_version,
                values={"finding_count": 99},
            )
        )
        current_progress = asyncio.run(
            repository.update_pipeline_state(
                task_id=task_ids[AuditStatus.RUNNING],
                user_id=USER_ID,
                expected_lock_version=second_version,
                values={"finding_count": 7},
            )
        )
        assert stale_progress is None
        assert current_progress is not None
        assert current_progress.finding_count == 7
        assert (
            asyncio.run(
                fail(
                    task_ids[AuditStatus.RUNNING],
                    expected_lock_version=first_version,
                )
            )
            is False
        )
        assert (
            asyncio.run(
                fail(
                    task_ids[AuditStatus.RUNNING],
                    expected_lock_version=second_version,
                )
            )
            is True
        )
        # 同一个后台任务即使被重复投递，也只能完成一次状态转换。
        assert asyncio.run(fail(task_ids[AuditStatus.CREATED])) is False
        assert asyncio.run(fail(task_ids[AuditStatus.COMPLETED])) is False
        assert asyncio.run(fail(task_ids[AuditStatus.PARTIAL_FAILED])) is False
        assert asyncio.run(fail(task_ids[AuditStatus.FAILED])) is False
        assert asyncio.run(fail(other_user_task_id)) is False

        session.expire_all()
        for status in (AuditStatus.CREATED, AuditStatus.RUNNING):
            task = session.get(AuditTask, task_ids[status])
            assert task is not None
            assert task.status is AuditStatus.FAILED
            assert task.error == AUDIT_RULES_MAINTAINING_MESSAGE
            assert task.completed_at == now.replace(tzinfo=None)

        for status in (
            AuditStatus.COMPLETED,
            AuditStatus.PARTIAL_FAILED,
            AuditStatus.FAILED,
        ):
            task = session.get(AuditTask, task_ids[status])
            assert task is not None
            assert task.status is status

        other_task = session.get(AuditTask, other_user_task_id)
        assert other_task is not None
        assert other_task.status is AuditStatus.CREATED
