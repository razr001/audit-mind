import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from app.models.audit_task import AuditStage, AuditStatus
from app.models.audit_task_page import AuditTaskPageStatus
from app.models.document import Document
from app.repositories.audit_result_repository import AuditResultRepository
from app.services.audit_workflow_service import AuditWorkflowService


class FakeUow:
    session = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False


def make_task(status: AuditStatus):
    return SimpleNamespace(
        id=uuid4(),
        status=status,
        stage=AuditStage.COMPLETED,
        lock_version=4,
    )


def make_service(*, task, pages=None):
    task_repository = SimpleNamespace(
        find_by_id_and_user=AsyncMock(return_value=task),
    )
    result_repository = SimpleNamespace(
        find_pages=AsyncMock(return_value=pages or []),
    )
    return (
        AuditWorkflowService(
            uow=FakeUow(),
            document_service=SimpleNamespace(),
            document_repository=SimpleNamespace(),
            task_repository=task_repository,
            result_repository=result_repository,
            page_repository=SimpleNamespace(),
            block_repository=SimpleNamespace(),
        ),
        task_repository,
    )


def test_create_upload_saves_document_and_task_in_one_uow() -> None:
    document = Document(
        user_id=uuid4(),
        original_filename="audit.pdf",
        storage_key="documents/audit.pdf",
        content_type="application/pdf",
        file_size=10,
    )
    document_service = SimpleNamespace(
        prepare_uploaded_document=AsyncMock(return_value=document),
    )
    document_repository = SimpleNamespace(save=AsyncMock(return_value=document))
    task_repository = SimpleNamespace(save=AsyncMock())
    uow = FakeUow()
    service = AuditWorkflowService(
        uow=uow,
        document_service=document_service,
        document_repository=document_repository,
        task_repository=task_repository,
        result_repository=SimpleNamespace(),
        page_repository=SimpleNamespace(),
        block_repository=SimpleNamespace(),
    )

    task = asyncio.run(
        service.create_from_upload(
            file=SimpleNamespace(),
            user_id=uuid4(),
            rule_scope_json=None,
        )
    )

    assert task.document is document
    document_repository.save.assert_awaited_once_with(document)
    task_repository.save.assert_awaited_once_with(task)


def test_create_upload_keeps_minio_when_task_save_fails() -> None:
    document = Document(
        user_id=uuid4(),
        original_filename="audit.pdf",
        storage_key="documents/audit.pdf",
        content_type="application/pdf",
        file_size=10,
    )
    document_service = SimpleNamespace(
        prepare_uploaded_document=AsyncMock(return_value=document),
    )
    document_repository = SimpleNamespace(save=AsyncMock(return_value=document))
    task_repository = SimpleNamespace(
        save=AsyncMock(side_effect=RuntimeError("task insert failed"))
    )
    service = AuditWorkflowService(
        uow=FakeUow(),
        document_service=document_service,
        document_repository=document_repository,
        task_repository=task_repository,
        result_repository=SimpleNamespace(),
        page_repository=SimpleNamespace(),
        block_repository=SimpleNamespace(),
    )

    try:
        asyncio.run(
            service.create_from_upload(
                file=SimpleNamespace(),
                user_id=uuid4(),
                rule_scope_json=None,
            )
        )
    except RuntimeError as exc:
        assert str(exc) == "task insert failed"
    else:
        raise AssertionError("task persistence failure must be propagated")



def test_retry_partial_task_only_schedules_background_execution() -> None:
    task = make_task(AuditStatus.PARTIAL_FAILED)
    service, repository = make_service(
        task=task,
        pages=[SimpleNamespace(status=AuditTaskPageStatus.FAILED)],
    )

    returned, should_schedule = asyncio.run(
        service.retry_task(task_id=task.id, user_id=uuid4())
    )

    assert returned is task
    assert should_schedule is True
    # HTTP 重试入口只能读取任务并投递后台任务，不能在 Redis 抢锁前写数据库。
    assert repository.find_by_id_and_user.await_count == 1


def test_retry_completed_task_is_idempotent() -> None:
    task = make_task(AuditStatus.COMPLETED)
    service, repository = make_service(task=task)

    returned, should_schedule = asyncio.run(
        service.retry_task(task_id=task.id, user_id=uuid4())
    )

    assert returned is task
    assert should_schedule is False


def test_dispatch_failure_preserves_retryable_result_status() -> None:
    for current_status, expected_status in (
        (AuditStatus.CREATED, AuditStatus.FAILED),
        (AuditStatus.FAILED, AuditStatus.FAILED),
        (AuditStatus.PARTIAL_FAILED, AuditStatus.PARTIAL_FAILED),
    ):
        task = make_task(current_status)
        updated = make_task(expected_status)
        service, repository = make_service(task=task)
        repository.mark_dispatch_failed = AsyncMock(return_value=updated)

        returned = asyncio.run(
            service.mark_dispatch_failed(task=task, user_id=uuid4())
        )

        assert returned is updated
        call = repository.mark_dispatch_failed.await_args.kwargs
        assert call["failure_status"] == expected_status


def test_page_result_groups_evidence_and_rules_by_finding() -> None:
    task = make_task(AuditStatus.COMPLETED)
    task.document = SimpleNamespace(source_type="PDF")
    finding_id = uuid4()
    document_block_id = uuid4()
    regulation_rule_id = uuid4()
    now = datetime.now(timezone.utc)
    page = SimpleNamespace(
        id=uuid4(),
        task_id=task.id,
        page_number=2,
        status=AuditTaskPageStatus.COMPLETED,
        attempt_count=1,
        finding_count=1,
        error=None,
        started_at=now,
        completed_at=now,
    )
    finding = SimpleNamespace(
        id=finding_id,
        page_number=2,
        level="HIGH",
        title="默认勾选",
        description=f"文档块“{document_block_id}”违反规则“{regulation_rule_id}”",
        recommendation=f"按照规则“{regulation_rule_id}”取消默认选中",
    )
    evidence = SimpleNamespace(
        id=uuid4(),
        finding_id=finding_id,
        document_block_id=document_block_id,
        page_number=2,
        quote="默认同意",
        bbox=[100, 100, 900, 200],
    )
    reference = SimpleNamespace(
        id=uuid4(),
        finding_id=finding_id,
        regulation_rule_id=regulation_rule_id,
        regulation_id=uuid4(),
        rule_type="PROHIBITION",
        topic="用户同意",
        rule_summary="不得默认勾选",
        source_filename="规则.pdf",
        source_content_hash="a" * 64,
        source_page_start=3,
        source_page_end=3,
        source_text="不得默认勾选。",
    )
    service, _ = make_service(task=task)
    service.result_repository.find_page_results = AsyncMock(
        return_value=(page, [finding], [evidence], [reference])
    )

    result = asyncio.run(
        service.get_page_result(task_id=task.id, page_number=2, user_id=uuid4())
    )

    assert result.findings[0].evidences[0].quote == "默认同意"
    assert result.findings[0].rule_references[0].source_text == "不得默认勾选。"
    assert result.findings[0].description == "文档内容违反相关规则"
    assert result.findings[0].recommendation == "按照相关规则取消默认选中"


def test_markdown_page_result_returns_source_and_exact_content_offset() -> None:
    task = make_task(AuditStatus.COMPLETED)
    task.document_id = uuid4()
    task.document = SimpleNamespace(source_type="MARKDOWN")
    now = datetime.now(timezone.utc)
    page = SimpleNamespace(
        id=uuid4(),
        task_id=task.id,
        page_number=2,
        status=AuditTaskPageStatus.COMPLETED,
        attempt_count=1,
        finding_count=0,
        error=None,
        started_at=now,
        completed_at=now,
    )
    service, _ = make_service(task=task)
    service.result_repository.find_page_results = AsyncMock(
        return_value=(page, [], [], [])
    )
    service.page_repository = SimpleNamespace(
        find_by_document_and_number=AsyncMock(
            return_value=SimpleNamespace(content="## 第二段\n\n需要审计的内容")
        )
    )
    service.block_repository = SimpleNamespace(
        find_by_document_and_page=AsyncMock(
            return_value=[SimpleNamespace(char_start=320)]
        )
    )

    result = asyncio.run(
        service.get_page_result(task_id=task.id, page_number=2, user_id=uuid4())
    )

    assert result.content == "## 第二段\n\n需要审计的内容"
    assert result.content_start == 320


def test_repository_orders_findings_by_first_pdf_block() -> None:
    """模型即使先返回页底问题，查询结果也应先展示页顶问题。"""
    task_id = uuid4()
    page = SimpleNamespace(id=uuid4())
    now = datetime.now(timezone.utc)
    top_finding = SimpleNamespace(id=uuid4(), created_at=now)
    bottom_finding = SimpleNamespace(id=uuid4(), created_at=now)
    top_evidence = SimpleNamespace(finding_id=top_finding.id)
    bottom_evidence = SimpleNamespace(finding_id=bottom_finding.id)
    execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: page),
            SimpleNamespace(
                scalars=lambda: SimpleNamespace(
                    all=lambda: [bottom_finding, top_finding]
                )
            ),
            SimpleNamespace(
                all=lambda: [
                    (top_evidence, 10),
                    (bottom_evidence, 90),
                ]
            ),
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),
        ]
    )
    repository = AuditResultRepository(SimpleNamespace(execute=execute))  # type: ignore[arg-type]

    _, findings, evidences, _ = asyncio.run(
        repository.find_page_results(task_id=task_id, page_number=1)
    )

    assert findings == [top_finding, bottom_finding]
    assert evidences == [top_evidence, bottom_evidence]
