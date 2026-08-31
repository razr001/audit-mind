from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.audit_failure import AUDIT_DISPATCH_FAILED_MESSAGE
from app.core.error_codes import REGULATION_RULES_MAINTAINING
from app.core.exceptions import BusinessException
from app.core.security import get_jwt_user
from app.main import create_app
from app.models.audit_task import AuditStage, AuditStatus
from app.schemas.auth import CurrentUser
from app.services.audit_workflow_service import (
    AuditWorkflowService,
    get_audit_workflow_service,
)
from app.services.regulation_availability_service import (
    require_regulation_rules_available,
)

USER_ID = uuid4()


def make_task():
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        document_id=uuid4(),
        document_filename="待审计.pdf",
        status=AuditStatus.CREATED,
        lock_version=0,
        stage=AuditStage.PARSING,
        total_pages=0,
        completed_pages=0,
        finding_count=0,
        rule_scope={},
        audit_as_of=date.today(),
        created_at=now,
        updated_at=now,
        error=None,
        started_at=None,
        completed_at=None,
    )


def make_app(service, *, rules_available=lambda: None):
    app = create_app(settings=SimpleNamespace(APP_NAME="test", CORS_ALLOWED_ORIGINS=[]))
    app.dependency_overrides[get_jwt_user] = lambda: CurrentUser(
        user_id=USER_ID, username="tester"
    )
    app.dependency_overrides[get_audit_workflow_service] = lambda: service
    app.dependency_overrides[require_regulation_rules_available] = rules_available
    return app


def test_create_audit_upload_schedules_pipeline(monkeypatch) -> None:
    task = make_task()
    service = SimpleNamespace(create_from_upload=AsyncMock(return_value=task))
    enqueue = AsyncMock(return_value="message-id")
    monkeypatch.setattr("app.api.audit_workflow.enqueue_audit_pipeline", enqueue)

    response = TestClient(make_app(service)).post(
        "/audit/tasks",
        files={"file": ("待审计.pdf", b"%PDF-1.7\n", "application/pdf")},
        data={"ruleScope": '{"jurisdictions":["CN"]}'},
    )

    assert response.status_code == 202
    assert response.json()["data"]["documentFilename"] == "待审计.pdf"
    service.create_from_upload.assert_awaited_once()
    call = service.create_from_upload.await_args.kwargs
    assert call["user_id"] == USER_ID
    assert call["rule_scope_json"] == '{"jurisdictions":["CN"]}'
    enqueue.assert_awaited_once()
    enqueue_call = enqueue.await_args.kwargs
    assert enqueue_call["task_id"] == task.id
    assert enqueue_call["user_id"] == USER_ID
    assert enqueue_call["request_id"] == response.headers["X-Request-ID"]


def test_create_markdown_audit_schedules_pipeline(monkeypatch) -> None:
    task = make_task()
    task.document_filename = "隐私政策.md"
    service = SimpleNamespace(create_from_markdown=AsyncMock(return_value=task))
    enqueue = AsyncMock(return_value="message-id")
    monkeypatch.setattr("app.api.audit_workflow.enqueue_audit_pipeline", enqueue)

    response = TestClient(make_app(service)).post(
        "/audit/tasks/markdown",
        data={
            "title": "隐私政策",
            "content": "# 隐私政策\n\n我们收集手机号。",
            "ruleScope": '{"jurisdictions":["CN"]}',
        },
    )

    assert response.status_code == 202
    assert response.json()["data"]["documentFilename"] == "隐私政策.md"
    service.create_from_markdown.assert_awaited_once_with(
        title="隐私政策",
        content="# 隐私政策\n\n我们收集手机号。",
        user_id=USER_ID,
        rule_scope_json='{"jurisdictions":["CN"]}',
    )
    enqueue.assert_awaited_once()
    enqueue_call = enqueue.await_args.kwargs
    assert enqueue_call["task_id"] == task.id
    assert enqueue_call["user_id"] == USER_ID
    assert enqueue_call["request_id"] == response.headers["X-Request-ID"]


def test_create_audit_rejects_regulation_maintenance_before_upload() -> None:
    service = SimpleNamespace(create_from_upload=AsyncMock())

    async def rules_maintaining() -> None:
        raise BusinessException(
            REGULATION_RULES_MAINTAINING,
            "规则正在维护，请稍后再试",
        )

    response = TestClient(make_app(service, rules_available=rules_maintaining)).post(
        "/audit/tasks",
        files={"file": ("待审计.pdf", b"%PDF-1.7\n", "application/pdf")},
    )

    # 项目约定 BusinessException 统一返回 HTTP 400，业务码保留具体语义。
    assert response.status_code == 503
    assert response.json()["code"] == REGULATION_RULES_MAINTAINING
    assert response.json()["message"] == "规则正在维护，请稍后再试"
    service.create_from_upload.assert_not_awaited()


def test_retry_schedules_pipeline_without_preclaiming_version(monkeypatch) -> None:
    task = make_task()
    service = SimpleNamespace(retry_task=AsyncMock(return_value=(task, True)))
    enqueue = AsyncMock(return_value="message-id")
    monkeypatch.setattr("app.api.audit_workflow.enqueue_audit_pipeline", enqueue)

    response = TestClient(make_app(service)).post(f"/audit/tasks/{task.id}/retry")

    assert response.status_code == 202
    enqueue.assert_awaited_once()
    enqueue_call = enqueue.await_args.kwargs
    assert enqueue_call["task_id"] == task.id
    assert enqueue_call["user_id"] == USER_ID
    assert enqueue_call["request_id"] == response.headers["X-Request-ID"]


def test_create_audit_marks_dispatch_failure(monkeypatch) -> None:
    task = make_task()
    failed_task = make_task()
    failed_task.id = task.id
    failed_task.document_id = task.document_id
    failed_task.status = AuditStatus.FAILED
    failed_task.error = AUDIT_DISPATCH_FAILED_MESSAGE
    service = SimpleNamespace(
        create_from_upload=AsyncMock(return_value=task),
        mark_dispatch_failed=AsyncMock(return_value=failed_task),
    )
    enqueue = AsyncMock(side_effect=ConnectionError("redis unavailable"))
    monkeypatch.setattr("app.api.audit_workflow.enqueue_audit_pipeline", enqueue)

    response = TestClient(make_app(service)).post(
        "/audit/tasks",
        files={"file": ("待审计.pdf", b"%PDF-1.7\n", "application/pdf")},
    )

    assert response.status_code == 202
    assert response.json()["data"]["status"] == AuditStatus.FAILED.value
    assert response.json()["data"]["error"] == AUDIT_DISPATCH_FAILED_MESSAGE
    service.mark_dispatch_failed.assert_awaited_once_with(
        task=task,
        user_id=USER_ID,
    )


def test_audit_task_detail_is_user_scoped() -> None:
    task = make_task()
    service = SimpleNamespace(get_task=AsyncMock(return_value=task))

    response = TestClient(make_app(service)).get(f"/audit/tasks/{task.id}")

    assert response.status_code == 200
    service.get_task.assert_awaited_once_with(task_id=task.id, user_id=USER_ID)


def test_rule_scope_rejects_unknown_fields() -> None:
    try:
        AuditWorkflowService._parse_rule_scope('{"unknown":true}')
    except Exception as exc:
        assert getattr(exc, "code", None) == 40006
    else:
        raise AssertionError("unknown rule scope field must be rejected")
