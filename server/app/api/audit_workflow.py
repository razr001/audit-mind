from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Path, Request, UploadFile

from app.api.dependencies import PaginationDep
from app.core.config import get_settings
from app.core.request_context import bind_current_user, get_request_user
from app.models.audit_task import AuditStatus, AuditTask
from app.schemas.audit_finding import AuditTaskPageResponse
from app.schemas.audit_task import AuditTaskProgressResponse
from app.schemas.page_result import PageResult
from app.schemas.response import Response
from app.services.audit_workflow_service import (
    AuditWorkflowService,
    get_audit_workflow_service,
)
from app.services.regulation_availability_service import (
    require_regulation_rules_available,
)
from app.tasks.audit_dispatcher import enqueue_audit_pipeline

router = APIRouter(
    prefix="/audit/tasks",
    tags=["audit-workflow"],
    dependencies=[Depends(bind_current_user)],
)

settings = get_settings()


@router.post("", response_model=Response[AuditTaskProgressResponse], status_code=202)
async def create_audit_task_from_upload(
    request: Request,
    file: UploadFile,
    rule_scope: Annotated[str | None, Form(alias="ruleScope")] = None,
    service: AuditWorkflowService = Depends(get_audit_workflow_service),
    _rules_available: None = Depends(require_regulation_rules_available),
) -> Response[AuditTaskProgressResponse]:
    """上传 PDF 并安排完整审计流水线，客户端不再拼接中间接口。"""
    current_user = get_request_user()
    task = await service.create_from_upload(
        file=file,
        user_id=current_user.user_id,
        rule_scope_json=rule_scope,
    )
    task = await _dispatch_task(
        request=request,
        task=task,
        user_id=current_user.user_id,
        service=service,
    )
    return Response(data=AuditTaskProgressResponse.model_validate(task))


@router.post(
    "/markdown",
    response_model=Response[AuditTaskProgressResponse],
    status_code=202,
)
async def create_audit_task_from_markdown(
    request: Request,
    title: Annotated[str, Form(min_length=1, max_length=252)],
    content: Annotated[
        str,
        Form(min_length=1, max_length=settings.AUDIT_MARKDOWN_MAX_BYTES),
    ],
    rule_scope: Annotated[str | None, Form(alias="ruleScope")] = None,
    service: AuditWorkflowService = Depends(get_audit_workflow_service),
    _rules_available: None = Depends(require_regulation_rules_available),
) -> Response[AuditTaskProgressResponse]:
    """接收 Markdown（普通文本也有效）并安排相同的后台审计流水线。"""
    current_user = get_request_user()
    task = await service.create_from_markdown(
        title=title,
        content=content,
        user_id=current_user.user_id,
        rule_scope_json=rule_scope,
    )
    task = await _dispatch_task(
        request=request,
        task=task,
        user_id=current_user.user_id,
        service=service,
    )
    return Response(data=AuditTaskProgressResponse.model_validate(task))


@router.get("", response_model=Response[PageResult[AuditTaskProgressResponse]])
async def list_audit_tasks(
    pagination: PaginationDep,
    status: AuditStatus | None = None,
    service: AuditWorkflowService = Depends(get_audit_workflow_service),
) -> Response[PageResult[AuditTaskProgressResponse]]:
    current_user = get_request_user()
    tasks, total = await service.get_tasks(
        user_id=current_user.user_id,
        offset=pagination.offset,
        limit=pagination.limit,
        status=status,
    )
    return Response(
        data=PageResult(
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
            items=[AuditTaskProgressResponse.model_validate(task) for task in tasks],
        )
    )


@router.get("/{task_id}", response_model=Response[AuditTaskProgressResponse])
async def get_audit_task_detail(
    task_id: UUID,
    service: AuditWorkflowService = Depends(get_audit_workflow_service),
) -> Response[AuditTaskProgressResponse]:
    current_user = get_request_user()
    task = await service.get_task(task_id=task_id, user_id=current_user.user_id)
    return Response(data=AuditTaskProgressResponse.model_validate(task))


@router.get(
    "/{task_id}/pages/{page_number}",
    response_model=Response[AuditTaskPageResponse],
)
async def get_audit_task_page(
    task_id: UUID,
    page_number: Annotated[int, Path(ge=1)],
    service: AuditWorkflowService = Depends(get_audit_workflow_service),
) -> Response[AuditTaskPageResponse]:
    current_user = get_request_user()
    result = await service.get_page_result(
        task_id=task_id,
        page_number=page_number,
        user_id=current_user.user_id,
    )
    return Response(data=result)


@router.post(
    "/{task_id}/retry",
    response_model=Response[AuditTaskProgressResponse],
    status_code=202,
)
async def retry_audit_task(
    request: Request,
    task_id: UUID,
    service: AuditWorkflowService = Depends(get_audit_workflow_service),
    _rules_available: None = Depends(require_regulation_rules_available),
) -> Response[AuditTaskProgressResponse]:
    current_user = get_request_user()
    task, should_schedule = await service.retry_task(
        task_id=task_id,
        user_id=current_user.user_id,
    )
    if should_schedule:
        task = await _dispatch_task(
            request=request,
            task=task,
            user_id=current_user.user_id,
            service=service,
        )
    return Response(data=AuditTaskProgressResponse.model_validate(task))


async def _dispatch_task(
    *,
    request: Request,
    task: AuditTask,
    user_id: UUID,
    service: AuditWorkflowService,
) -> AuditTask:
    """统一处理新建和重试任务的 Dramatiq 投递。"""
    try:
        await enqueue_audit_pipeline(
            task_id=task.id,
            user_id=user_id,
            request_id=request.state.request_id,
        )
    except Exception:
        return await service.mark_dispatch_failed(
            task=task,
            user_id=user_id,
        )

    return task
