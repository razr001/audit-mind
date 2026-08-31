import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.regulation_maintenance import router, verify_scheduler_token
from app.core.config import get_settings
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
from app.repositories.regulation_maintenance_repository import (
    RegulationMaintenanceRepository,
)
from app.repositories.regulation_repository import RegulationRepository
from app.schemas.regulation_maintenance import RegulationTimeoutStage
from app.services.regulation_maintenance_service import (
    RegulationMaintenanceService,
    get_regulation_maintenance_service,
)


class AsyncContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class AsyncSessionAdapter:
    def __init__(self, session: Session):
        self.session = session

    async def execute(self, statement):
        return self.session.execute(statement)


def maintenance_settings():
    return SimpleNamespace(
        REGULATION_PARSE_STALE_SECONDS=7200,
        REGULATION_CHUNK_STALE_SECONDS=1800,
        REGULATION_INDEX_STALE_SECONDS=3600,
        REGULATION_RULE_STALE_SECONDS=7200,
    )


@pytest.mark.parametrize(
    ("stage", "expected_seconds"),
    [
        (RegulationTimeoutStage.PARSE, 7200),
        (RegulationTimeoutStage.CHUNK, 1800),
        (RegulationTimeoutStage.INDEX, 3600),
        (RegulationTimeoutStage.RULE, 7200),
    ],
)
def test_maintenance_uses_stage_timeout_and_returns_updated_count(
    stage: RegulationTimeoutStage,
    expected_seconds: int,
):
    regulation_ids = [uuid4(), uuid4(), uuid4()]
    repository = SimpleNamespace(
        find_stale_regulation_ids=AsyncMock(return_value=regulation_ids),
        mark_stale_failed=AsyncMock(return_value=1),
    )
    service = RegulationMaintenanceService(
        uow=AsyncContext(),
        repository=repository,
        settings=maintenance_settings(),
    )

    @asynccontextmanager
    async def acquire(_regulation_id):
        yield True

    before = datetime.now(timezone.utc)
    with patch(
        "app.services.regulation_maintenance_service.acquire_regulation_pipeline_lease",
        new=acquire,
    ):
        result = asyncio.run(service.mark_timed_out_failed(stage=stage))
    after = datetime.now(timezone.utc)

    assert result.stage == stage
    assert result.updated_count == 3
    assert before - timedelta(seconds=expected_seconds) <= result.stale_before
    assert result.stale_before <= after - timedelta(seconds=expected_seconds)
    repository.find_stale_regulation_ids.assert_awaited_once()
    assert repository.mark_stale_failed.await_count == len(regulation_ids)


def test_maintenance_skips_regulation_while_pipeline_holds_total_lock():
    """法规仍持有流水线锁时，XXL-JOB 必须零写入地跳过。"""
    regulation_id = uuid4()
    repository = SimpleNamespace(
        find_stale_regulation_ids=AsyncMock(return_value=[regulation_id]),
        mark_stale_failed=AsyncMock(return_value=1),
    )
    service = RegulationMaintenanceService(
        uow=AsyncContext(),
        repository=repository,
        settings=maintenance_settings(),
    )

    @asynccontextmanager
    async def acquire(_regulation_id):
        yield False

    with patch(
        "app.services.regulation_maintenance_service.acquire_regulation_pipeline_lease",
        new=acquire,
    ):
        result = asyncio.run(
            service.mark_timed_out_failed(stage=RegulationTimeoutStage.CHUNK)
        )

    assert result.updated_count == 0
    repository.mark_stale_failed.assert_not_awaited()


def test_scheduler_token_is_independent_and_fails_closed():
    configured = SimpleNamespace(
        SCHEDULER_ACCESS_TOKEN=SecretStr("a" * 32),
    )
    verify_scheduler_token(configured, "a" * 32)

    with pytest.raises(HTTPException) as missing:
        verify_scheduler_token(configured, "")
    assert missing.value.status_code == 401

    disabled = SimpleNamespace(SCHEDULER_ACCESS_TOKEN=SecretStr(""))
    with pytest.raises(HTTPException) as disabled_error:
        verify_scheduler_token(disabled, "a" * 32)
    assert disabled_error.value.status_code == 401


def test_repository_marks_each_stale_stage_failed_and_fences_old_rule_task():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Regulation.__table__.create(engine)
    now = datetime.now(timezone.utc)
    stale_started_at = now - timedelta(hours=3)

    with Session(engine) as session:
        regulation = Regulation(
            title="Stale regulation",
            source_type=RegulationSourceType.REGULATION,
            category=KnowledgeCategory.PUBLIC_KNOWLEDGE,
            visibility=KnowledgeVisibility.SHARED,
            language="zh-CN",
            jurisdiction="CN",
            storage_key=f"regulations/{uuid4()}.pdf",
            original_filename="stale.pdf",
            content_type="application/pdf",
            file_size=100,
            content_hash=uuid4().hex.ljust(64, "0"),
            uploaded_by=uuid4(),
            enabled=True,
            status=RegulationStatus.PARSING,
            parse_started_at=stale_started_at,
            chunk_status=RegulationChunkStatus.PROCESSING,
            chunk_started_at=stale_started_at,
            index_status=RegulationIndexStatus.PROCESSING,
            index_started_at=stale_started_at,
            rule_status=RegulationRuleStatus.PROCESSING,
            rule_started_at=stale_started_at,
        )
        session.add(regulation)
        session.commit()
        repository = RegulationMaintenanceRepository(AsyncSessionAdapter(session))

        for stage in RegulationTimeoutStage:
            updated = asyncio.run(
                repository.mark_stale_failed(
                    regulation_id=regulation.id,
                    stage=stage,
                    stale_before=now - timedelta(hours=1),
                    completed_at=now,
                )
            )
            assert updated == 1
        session.commit()
        session.refresh(regulation)

        assert regulation.status == RegulationStatus.FAILED
        assert regulation.chunk_status == RegulationChunkStatus.FAILED
        assert regulation.index_status == RegulationIndexStatus.FAILED
        assert regulation.rule_status == RegulationRuleStatus.FAILED
        assert regulation.rule_started_at is None
        # 每回收一个阶段都使旧执行者持有的版本失效。
        assert regulation.lock_version == len(RegulationTimeoutStage)


def test_stage_claims_reject_active_work_and_take_over_only_after_timeout():
    """Chunk/Rules/Parse 使用同一超时语义，且 Parse 不重复提交已有 MinerU 任务。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Regulation.__table__.create(engine)
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=3)
    user_id = uuid4()

    def regulation(**values):
        return Regulation(
            title="Takeover",
            source_type=RegulationSourceType.REGULATION,
            category=KnowledgeCategory.PUBLIC_KNOWLEDGE,
            visibility=KnowledgeVisibility.SHARED,
            language="zh-CN",
            jurisdiction="CN",
            storage_key=f"regulations/{uuid4()}.pdf",
            original_filename="takeover.pdf",
            content_type="application/pdf",
            file_size=100,
            content_hash=uuid4().hex.ljust(64, "0"),
            uploaded_by=user_id,
            enabled=True,
            **values,
        )

    with Session(engine) as session:
        chunk = regulation(
            status=RegulationStatus.READY,
            chunk_status=RegulationChunkStatus.PROCESSING,
            chunk_started_at=old,
        )
        rule = regulation(
            status=RegulationStatus.READY,
            chunk_status=RegulationChunkStatus.READY,
            rule_status=RegulationRuleStatus.PROCESSING,
            rule_started_at=old,
        )
        parse_with_task = regulation(
            status=RegulationStatus.PARSING,
            parse_started_at=old,
            parse_task_id="mineru-existing",
        )
        parse_without_task = regulation(
            status=RegulationStatus.PARSING,
            parse_started_at=old,
            parse_task_id=None,
        )
        session.add_all([chunk, rule, parse_with_task, parse_without_task])
        session.commit()
        repository = RegulationRepository(AsyncSessionAdapter(session))

        # 截止时间早于 attempt_started_at，仍在正常窗口内，不能接管。
        active_chunk = asyncio.run(
            repository.claim_for_chunks(
                regulation_id=chunk.id,
                user_id=user_id,
                started_at=now,
                stale_before=old - timedelta(seconds=1),
            )
        )
        assert active_chunk is None
        stale_chunk = asyncio.run(
            repository.claim_for_chunks(
                regulation_id=chunk.id,
                user_id=user_id,
                started_at=now,
                stale_before=old,
            )
        )
        assert stale_chunk is not None
        assert stale_chunk.lock_version == 1

        stale_rule = asyncio.run(
            repository.claim_for_rules(
                regulation_id=rule.id,
                user_id=user_id,
                started_at=now,
                stale_before=old,
            )
        )
        assert stale_rule is not None
        assert stale_rule.lock_version == 1

        existing_task = asyncio.run(
            repository.claim_for_parse(
                regulation_id=parse_with_task.id,
                user_id=user_id,
                started_at=now,
                stale_before=old,
            )
        )
        assert existing_task is None
        missing_task = asyncio.run(
            repository.claim_for_parse(
                regulation_id=parse_without_task.id,
                user_id=user_id,
                started_at=now,
                stale_before=old,
            )
        )
        assert missing_task is not None
        assert missing_task.lock_version == 1


def test_maintenance_api_requires_internal_token_and_returns_result():
    application = FastAPI()
    application.include_router(router)
    settings = SimpleNamespace(SCHEDULER_ACCESS_TOKEN=SecretStr("a" * 32))
    result = SimpleNamespace(
        stage=RegulationTimeoutStage.CHUNK,
        stale_before=datetime.now(timezone.utc),
        updated_count=2,
    )
    service = SimpleNamespace(mark_timed_out_failed=AsyncMock(return_value=result))
    application.dependency_overrides[get_settings] = lambda: settings
    application.dependency_overrides[get_regulation_maintenance_service] = lambda: service
    client = TestClient(application)

    unauthorized = client.post("/internal/regulation/tasks/timeout/chunk")
    response = client.post(
        "/internal/regulation/tasks/timeout/chunk",
        headers={"X-Internal-Token": "a" * 32},
    )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.json()["data"]["stage"] == "chunk"
    assert response.json()["data"]["updatedCount"] == 2
    service.mark_timed_out_failed.assert_awaited_once_with(
        stage=RegulationTimeoutStage.CHUNK,
    )
