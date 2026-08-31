import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.regulation import router as regulation_router
from app.api.regulation_pipeline import process_regulation, require_local_environment
from app.api.regulation_sources import create_regulation_text, upload_regulation
from app.models.regulation import (
    KnowledgeCategory,
    KnowledgeVisibility,
    RegulationChunkStatus,
    RegulationIndexStatus,
    RegulationRuleStatus,
    RegulationSourceType,
    RegulationStatus,
)
from app.services.regulation_pipeline_service import (
    RegulationPipelineService,
    run_regulation_pipeline,
)


def pipeline_state(
    *,
    status=RegulationStatus.READY,
    chunk_status=RegulationChunkStatus.READY,
    index_status=RegulationIndexStatus.READY,
    rule_status=RegulationRuleStatus.READY,
):
    """构造只包含编排器决策字段的法规状态。"""
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        title="测试法规",
        source_type=RegulationSourceType.REGULATION,
        category=KnowledgeCategory.PUBLIC_KNOWLEDGE,
        original_filename="测试法规.pdf",
        file_size=1024,
        enabled=True,
        status=status,
        parse_error=None,
        parse_started_at=None,
        parse_completed_at=None,
        chunk_status=chunk_status,
        chunk_error=None,
        chunk_started_at=None,
        chunk_completed_at=None,
        index_status=index_status,
        index_error=None,
        index_started_at=None,
        index_completed_at=None,
        rule_status=rule_status,
        rule_error=None,
        rule_started_at=None,
        rule_completed_at=None,
        created_at=now,
        updated_at=now,
    )


def pipeline_service(
    *,
    initial,
    parse,
    knowledge,
    index,
    rule,
    timeout=1,
    index_service_provider=None,
):
    repository = SimpleNamespace(find_by_id_and_user=AsyncMock(return_value=initial))
    return RegulationPipelineService(
        uow=AsyncMockContextManager(),
        repository=repository,
        parse_service=parse,
        knowledge_service=knowledge,
        index_service_provider=index_service_provider or (lambda: index),
        rule_service=rule,
        poll_interval_seconds=0,
        wait_timeout_seconds=timeout,
    )


class AsyncMockContextManager:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def test_pipeline_api_route_is_registered():
    routes = [
        route
        for route in regulation_router.routes
        if route.path == "/regulation/process/{regulation_id}"
    ]

    assert len(routes) == 1
    assert "POST" in routes[0].methods
    assert routes[0].status_code == 202


def test_creation_endpoints_schedule_pipeline_before_returning() -> None:
    created = pipeline_state(rule_status=RegulationRuleStatus.PENDING)
    created.visibility = KnowledgeVisibility.SHARED
    user_id = uuid4()
    request = SimpleNamespace(state=SimpleNamespace(request_id="request-create"))
    upload_service = SimpleNamespace(upload=AsyncMock(return_value=created))
    text_service = SimpleNamespace(create=AsyncMock(return_value=created))

    with (
        patch(
            "app.api.regulation_sources.get_request_user",
            return_value=SimpleNamespace(user_id=user_id),
        ),
        patch(
            "app.api.regulation_sources.schedule_regulation_pipeline",
            new=AsyncMock(return_value=True),
        ) as schedule,
    ):
        upload_response = asyncio.run(
            upload_regulation(
                request=request,
                file=SimpleNamespace(),
                form=SimpleNamespace(),
                service=upload_service,
            )
        )
        text_response = asyncio.run(
            create_regulation_text(
                body=SimpleNamespace(),
                request=request,
                service=text_service,
            )
        )

    assert upload_response.data.id == created.id
    assert text_response.data.id == created.id
    assert schedule.await_count == 2
    schedule.assert_awaited_with(
        regulation=created,
        user_id=user_id,
        request_id="request-create",
    )

    create_routes = {
        route.path: route.status_code
        for route in regulation_router.routes
        if route.path in {"/regulation/upload", "/regulation/text"}
    }
    assert create_routes == {"/regulation/upload": None, "/regulation/text": None}


def test_process_regulation_enqueues_dramatiq_with_request_context():
    regulation = pipeline_state(rule_status=RegulationRuleStatus.PENDING)
    user_id = uuid4()
    request = SimpleNamespace(state=SimpleNamespace(request_id="request-id"))
    with (
        patch(
            "app.api.regulation_pipeline.get_request_user",
            return_value=SimpleNamespace(user_id=user_id),
        ),
        patch(
            "app.api.regulation_pipeline.get_regulation_pipeline_state",
            new=AsyncMock(return_value=regulation),
        ),
        patch(
            "app.api.regulation_pipeline.schedule_regulation_pipeline",
            new=AsyncMock(return_value=True),
        ) as enqueue,
    ):
        response = asyncio.run(
            process_regulation(request=request, regulation_id=regulation.id)
        )

    assert response.data.id == regulation.id
    enqueue.assert_awaited_once_with(
        regulation=regulation,
        user_id=user_id,
        request_id="request-id",
    )


def test_process_regulation_returns_503_when_queue_is_unavailable():
    regulation = pipeline_state(rule_status=RegulationRuleStatus.PENDING)
    request = SimpleNamespace(state=SimpleNamespace(request_id="request-id"))
    with (
        patch(
            "app.api.regulation_pipeline.get_request_user",
            return_value=SimpleNamespace(user_id=uuid4()),
        ),
        patch(
            "app.api.regulation_pipeline.get_regulation_pipeline_state",
            new=AsyncMock(return_value=regulation),
        ),
        patch(
            "app.api.regulation_pipeline.schedule_regulation_pipeline",
            new=AsyncMock(side_effect=ConnectionError("redis unavailable")),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(process_regulation(request=request, regulation_id=regulation.id))

    assert exc_info.value.status_code == 503


def test_single_step_pipeline_routes_only_allow_local_environment():
    require_local_environment(SimpleNamespace(ENVIRONMENT="local"))

    with pytest.raises(HTTPException) as exc_info:
        require_local_environment(SimpleNamespace(ENVIRONMENT="production"))

    assert exc_info.value.status_code == 404


def test_pipeline_runs_all_steps_and_polls_mineru_until_ready():
    uploaded = pipeline_state(
        status=RegulationStatus.UPLOADED,
        chunk_status=RegulationChunkStatus.PENDING,
        index_status=RegulationIndexStatus.PENDING,
        rule_status=RegulationRuleStatus.PENDING,
    )
    parsing = pipeline_state(
        status=RegulationStatus.PARSING,
        chunk_status=RegulationChunkStatus.PENDING,
        index_status=RegulationIndexStatus.PENDING,
        rule_status=RegulationRuleStatus.PENDING,
    )
    parsed = pipeline_state(
        chunk_status=RegulationChunkStatus.PENDING,
        index_status=RegulationIndexStatus.PENDING,
        rule_status=RegulationRuleStatus.PENDING,
    )
    chunked = pipeline_state(
        index_status=RegulationIndexStatus.PENDING,
        rule_status=RegulationRuleStatus.PENDING,
    )
    indexed = pipeline_state(rule_status=RegulationRuleStatus.PENDING)
    completed = pipeline_state()

    parse = SimpleNamespace(
        start_parse=AsyncMock(return_value=parsing),
        sync_parse_result=AsyncMock(side_effect=[parsing, parsed]),
    )
    knowledge = SimpleNamespace(build=AsyncMock(return_value=chunked))
    index = SimpleNamespace(index=AsyncMock(return_value=indexed))
    rule = SimpleNamespace(build=AsyncMock(return_value=completed))
    service = pipeline_service(
        initial=uploaded,
        parse=parse,
        knowledge=knowledge,
        index=index,
        rule=rule,
    )

    result = asyncio.run(service.run(regulation_id=uuid4(), user_id=uuid4()))

    assert result is completed
    parse.start_parse.assert_awaited_once()
    assert parse.sync_parse_result.await_count == 2
    knowledge.build.assert_awaited_once()
    index.index.assert_awaited_once()
    rule.build.assert_awaited_once()


def test_pipeline_retry_starts_at_failed_index_step():
    index_failed = pipeline_state(
        index_status=RegulationIndexStatus.FAILED,
        rule_status=RegulationRuleStatus.PENDING,
    )
    indexed = pipeline_state(rule_status=RegulationRuleStatus.PENDING)
    completed = pipeline_state()
    parse = SimpleNamespace(
        start_parse=AsyncMock(),
        sync_parse_result=AsyncMock(),
    )
    knowledge = SimpleNamespace(build=AsyncMock())
    index = SimpleNamespace(index=AsyncMock(return_value=indexed))
    rule = SimpleNamespace(build=AsyncMock(return_value=completed))
    service = pipeline_service(
        initial=index_failed,
        parse=parse,
        knowledge=knowledge,
        index=index,
        rule=rule,
    )

    result = asyncio.run(service.run(regulation_id=uuid4(), user_id=uuid4()))

    assert result is completed
    parse.start_parse.assert_not_awaited()
    parse.sync_parse_result.assert_not_awaited()
    knowledge.build.assert_not_awaited()
    index.index.assert_awaited_once()
    rule.build.assert_awaited_once()


def test_pipeline_does_not_initialize_index_service_when_index_is_ready():
    completed = pipeline_state()
    provider = Mock()
    service = pipeline_service(
        initial=completed,
        parse=SimpleNamespace(start_parse=AsyncMock(), sync_parse_result=AsyncMock()),
        knowledge=SimpleNamespace(build=AsyncMock()),
        index=SimpleNamespace(index=AsyncMock()),
        rule=SimpleNamespace(build=AsyncMock()),
        index_service_provider=provider,
    )

    result = asyncio.run(service.run(regulation_id=uuid4(), user_id=uuid4()))

    assert result is completed
    provider.assert_not_called()


def test_pipeline_timeout_keeps_parsing_state_and_stops_downstream():
    parsing = pipeline_state(
        status=RegulationStatus.PARSING,
        chunk_status=RegulationChunkStatus.PENDING,
        index_status=RegulationIndexStatus.PENDING,
        rule_status=RegulationRuleStatus.PENDING,
    )
    parse = SimpleNamespace(
        start_parse=AsyncMock(),
        sync_parse_result=AsyncMock(return_value=parsing),
    )
    knowledge = SimpleNamespace(build=AsyncMock())
    index = SimpleNamespace(index=AsyncMock())
    rule = SimpleNamespace(build=AsyncMock())
    service = pipeline_service(
        initial=parsing,
        parse=parse,
        knowledge=knowledge,
        index=index,
        rule=rule,
        timeout=0,
    )

    result = asyncio.run(service.run(regulation_id=uuid4(), user_id=uuid4()))

    assert result is parsing
    parse.sync_parse_result.assert_awaited_once()
    knowledge.build.assert_not_awaited()
    index.index.assert_not_awaited()
    rule.build.assert_not_awaited()


def test_pipeline_failure_log_identifies_the_exact_step():
    index_failed = pipeline_state(
        index_status=RegulationIndexStatus.FAILED,
        rule_status=RegulationRuleStatus.PENDING,
    )
    failure = RuntimeError("sensitive third-party response")
    service = pipeline_service(
        initial=index_failed,
        parse=SimpleNamespace(
            start_parse=AsyncMock(),
            sync_parse_result=AsyncMock(),
        ),
        knowledge=SimpleNamespace(build=AsyncMock()),
        index=SimpleNamespace(index=AsyncMock(side_effect=failure)),
        rule=SimpleNamespace(build=AsyncMock()),
    )
    regulation_id = uuid4()

    with patch(
        "app.services.regulation_pipeline_service.log_regulation_failure"
    ) as failure_log:
        with pytest.raises(RuntimeError):
            asyncio.run(service.run(regulation_id=regulation_id, user_id=uuid4()))

    failure_log.assert_called_once_with(
        "regulation.pipeline.index.failed",
        regulation_id=regulation_id,
        error=failure,
    )


def test_pipeline_logs_redis_lock_acquisition_failure():
    """Redis 不可用时记录明确阶段，并吞掉响应返回后的后台异常。"""

    class FailingLease:
        async def __aenter__(self):
            raise ConnectionError("redis unavailable")

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    regulation_id = uuid4()
    user_id = uuid4()
    with (
        patch(
            "app.services.regulation_pipeline_service.acquire_regulation_pipeline_lease",
            return_value=FailingLease(),
        ),
        patch(
            "app.services.regulation_pipeline_service.log_regulation_failure"
        ) as failure_log,
    ):
        asyncio.run(
            run_regulation_pipeline(
                regulation_id=regulation_id,
                user_id=user_id,
            )
        )

    failure = failure_log.call_args.kwargs["error"]
    assert isinstance(failure, ConnectionError)
    failure_log.assert_called_once_with(
        "regulation.pipeline.lock_failed",
        regulation_id=regulation_id,
        error=failure,
    )
