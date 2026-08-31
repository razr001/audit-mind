from datetime import date, datetime, timezone
from uuid import uuid4

from app.models.audit_task import AuditStage, AuditStatus, AuditTask
from app.models.audit_task_page import AuditTaskPage, AuditTaskPageStatus
from app.schemas.audit_task import AuditTaskProgressResponse


def test_audit_task_defaults_support_page_pipeline() -> None:
    task = AuditTask(
        document_id=uuid4(),
        audit_as_of=date(2026, 8, 26),
    )

    assert task.status is None  # SQLAlchemy defaults are applied during INSERT.
    assert task.stage is None
    assert task.audit_as_of == date(2026, 8, 26)


def test_audit_page_has_retryable_initial_shape() -> None:
    page = AuditTaskPage(task_id=uuid4(), page_number=3)

    assert page.page_number == 3
    assert page.status is None
    assert page.error is None


def test_progress_schema_uses_frontend_camel_case() -> None:
    now = datetime.now(timezone.utc)
    payload = AuditTaskProgressResponse.model_validate(
        {
            "id": uuid4(),
            "document_id": uuid4(),
            "document_filename": "sample.pdf",
            "status": AuditStatus.PARTIAL_FAILED,
            "stage": AuditStage.AUDITING,
            "total_pages": 10,
            "completed_pages": 9,
            "finding_count": 4,
            "rule_scope": {"jurisdictions": ["CN"]},
            "audit_as_of": date(2026, 8, 26),
            "created_at": now,
            "updated_at": now,
            "error": None,
            "started_at": now,
            "completed_at": None,
        }
    ).model_dump(mode="json", by_alias=True)

    assert payload["completedPages"] == 9
    assert payload["auditAsOf"] == "2026-08-26"
    assert payload["status"] == "PARTIAL_FAILED"


def test_page_status_values_are_stable_api_values() -> None:
    assert [status.value for status in AuditTaskPageStatus] == [
        "PENDING",
        "RUNNING",
        "COMPLETED",
        "FAILED",
    ]
