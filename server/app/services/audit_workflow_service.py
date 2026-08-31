import json
from datetime import date
from uuid import UUID

from fastapi import Depends, UploadFile
from pydantic import ValidationError

from app.ai.agent.services.agent_tool_fence import require_running_agent_tool_call
from app.core.audit_failure import AUDIT_DISPATCH_FAILED_MESSAGE
from app.core.error_codes import AUDIT_TASK_NOT_FOUND, INVALID_AUDIT_RULE_SCOPE
from app.core.exceptions import BusinessException
from app.core.logger import logger
from app.infrastructure.db.unit_of_work import UnitOfWork, get_uow
from app.models.audit_task import AuditStage, AuditStatus, AuditTask
from app.models.document import DocumentSourceType
from app.repositories.audit_result_repository import AuditResultRepository
from app.repositories.audit_task_repository import AuditTaskRepository
from app.repositories.document_page_repository import DocumentPageRepository
from app.repositories.document_parse_block_repository import DocumentParseBlockRepository
from app.repositories.document_repository import DocumentRepository
from app.schemas.audit_finding import (
    AuditEvidenceResponse,
    AuditFindingResponse,
    AuditTaskPageResponse,
    FindingRuleReferenceResponse,
)
from app.schemas.audit_task import AuditRuleScope
from app.services.document_service import DocumentService, get_document_service
from app.services.page_audit_display_text import sanitize_finding_display_text
from app.unit.date import utc_now


class AuditWorkflowService:
    """创建新式审计任务，并提供工作台所需的任务和结果查询。"""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        document_service: DocumentService,
        document_repository: DocumentRepository,
        task_repository: AuditTaskRepository,
        result_repository: AuditResultRepository,
        page_repository: DocumentPageRepository,
        block_repository: DocumentParseBlockRepository,
    ) -> None:
        self.uow = uow
        self.document_service = document_service
        self.document_repository = document_repository
        self.task_repository = task_repository
        self.result_repository = result_repository
        self.page_repository = page_repository
        self.block_repository = block_repository

    async def create_from_upload(
        self,
        *,
        file: UploadFile,
        user_id: UUID,
        rule_scope_json: str | None,
    ) -> AuditTask:
        """复用统一文件校验和 MinIO 上传，再创建可追踪的审计任务。"""
        scope = self._parse_rule_scope(rule_scope_json)
        document = await self.document_service.prepare_uploaded_document(
            file=file,
            user_id=user_id,
        )
        task = AuditTask(
            document_id=document.id,
            document=document,
            status=AuditStatus.CREATED,
            stage=AuditStage.PARSING,
            rule_scope=scope.model_dump(mode="json", by_alias=False),
            audit_as_of=date.today(),
            started_at=None,
        )
        # 两条记录原子提交；事务异常只回滚数据库，绝不补偿删除 MinIO。
        # 数据库提交结果可能不明确，保留少量孤立对象比误删有效文件安全。
        async with self.uow:
            await self.document_repository.save(document)
            await self.task_repository.save(task)
        logger.info(
            "audit.pipeline.created",
            task_id=str(task.id),
            document_id=str(document.id),
            stage=task.stage.value,
        )
        return task

    async def create_from_markdown(
        self,
        *,
        title: str,
        content: str,
        user_id: UUID,
        rule_scope_json: str | None,
        agent_tool_call_id: UUID | None = None,
    ) -> AuditTask:
        """保存 Markdown/纯文本原文，并创建与 PDF 相同的可重试审计任务。"""
        scope = self._parse_rule_scope(rule_scope_json)
        document = await self.document_service.prepare_markdown_document(
            title=title,
            content=content,
            user_id=user_id,
        )
        task = AuditTask(
            agent_tool_call_id=agent_tool_call_id,
            document_id=document.id,
            document=document,
            status=AuditStatus.CREATED,
            stage=AuditStage.PARSING,
            rule_scope=scope.model_dump(mode="json", by_alias=False),
            audit_as_of=date.today(),
            started_at=None,
        )
        async with self.uow:
            if agent_tool_call_id is not None:
                await require_running_agent_tool_call(
                    self.uow.session,
                    agent_tool_call_id,
                )
            await self.document_repository.save(document)
            await self.task_repository.save(task)
        logger.info(
            "audit.pipeline.created",
            task_id=str(task.id),
            document_id=str(document.id),
            source_type="MARKDOWN",
            stage=task.stage.value,
        )
        return task

    async def create_from_existing_document(
        self,
        *,
        document_id: UUID,
        user_id: UUID,
        rule_scope_json: str | None,
        agent_tool_call_id: UUID | None = None,
    ) -> AuditTask:
        """Create a fresh audit task for an existing document owned by the user."""
        scope = self._parse_rule_scope(rule_scope_json)
        document = await self.document_service.get_document(document_id, user_id)
        task = AuditTask(
            agent_tool_call_id=agent_tool_call_id,
            document_id=document.id,
            document=document,
            status=AuditStatus.CREATED,
            stage=AuditStage.PARSING,
            rule_scope=scope.model_dump(mode="json", by_alias=False),
            audit_as_of=date.today(),
            started_at=None,
        )
        async with self.uow:
            if agent_tool_call_id is not None:
                await require_running_agent_tool_call(
                    self.uow.session,
                    agent_tool_call_id,
                )
            await self.task_repository.save(task)
        logger.info(
            "audit.pipeline.created_from_existing_document",
            task_id=str(task.id),
            document_id=str(document.id),
            stage=task.stage.value,
        )
        return task

    @staticmethod
    def _parse_rule_scope(raw: str | None) -> AuditRuleScope:
        if raw is None or not raw.strip():
            return AuditRuleScope()
        if len(raw) > 20_000:
            raise BusinessException(INVALID_AUDIT_RULE_SCOPE, "rule scope is too large")
        try:
            return AuditRuleScope.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise BusinessException(INVALID_AUDIT_RULE_SCOPE, "invalid rule scope") from exc

    async def get_task(self, *, task_id: UUID, user_id: UUID) -> AuditTask:
        async with self.uow:
            task = await self.task_repository.find_by_id_and_user(
                task_id=task_id, user_id=user_id
            )
        if task is None:
            raise BusinessException(AUDIT_TASK_NOT_FOUND, "audit task not found")
        return task

    async def get_tasks(
        self,
        *,
        user_id: UUID,
        offset: int,
        limit: int,
        status: AuditStatus | None,
    ) -> tuple[list[AuditTask], int]:
        async with self.uow:
            return await self.task_repository.find_page_by_user(
                user_id=user_id, offset=offset, limit=limit, status=status
            )

    async def get_page_result(
        self, *, task_id: UUID, page_number: int, user_id: UUID
    ) -> AuditTaskPageResponse:
        # 先验证任务归属，之后才读取结果，避免通过页码探测他人任务。
        task = await self.get_task(task_id=task_id, user_id=user_id)
        async with self.uow:
            page, findings, evidences, references = (
                await self.result_repository.find_page_results(
                    task_id=task_id, page_number=page_number
                )
            )
            source_page = None
            source_blocks = []
            if task.document.source_type == DocumentSourceType.MARKDOWN:
                source_page = await self.page_repository.find_by_document_and_number(
                    document_id=task.document_id,
                    page_number=page_number,
                )
                source_blocks = await self.block_repository.find_by_document_and_page(
                    document_id=task.document_id,
                    page_number=page_number,
                )
        if page is None:
            raise BusinessException(AUDIT_TASK_NOT_FOUND, "audit task page not found")
        evidence_by_finding: dict[UUID, list[AuditEvidenceResponse]] = {}
        for evidence in evidences:
            evidence_by_finding.setdefault(evidence.finding_id, []).append(
                AuditEvidenceResponse.model_validate(evidence)
            )
        reference_by_finding: dict[UUID, list[FindingRuleReferenceResponse]] = {}
        for reference in references:
            reference_by_finding.setdefault(reference.finding_id, []).append(
                FindingRuleReferenceResponse.model_validate(reference)
            )
        finding_responses: list[AuditFindingResponse] = []
        for finding in findings:
            finding_evidences = evidence_by_finding.get(finding.id, [])
            finding_references = reference_by_finding.get(finding.id, [])
            document_block_ids = [
                evidence.document_block_id
                for evidence in finding_evidences
                if evidence.document_block_id is not None
            ]
            regulation_rule_ids = [
                reference.regulation_rule_id for reference in finding_references
            ]
            finding_responses.append(
                AuditFindingResponse(
                    id=finding.id,
                    page_number=finding.page_number,
                    level=finding.level,
                    title=sanitize_finding_display_text(
                        finding.title,
                        document_block_ids=document_block_ids,
                        regulation_rule_ids=regulation_rule_ids,
                    ),
                    description=sanitize_finding_display_text(
                        finding.description,
                        document_block_ids=document_block_ids,
                        regulation_rule_ids=regulation_rule_ids,
                    ),
                    recommendation=sanitize_finding_display_text(
                        finding.recommendation,
                        document_block_ids=document_block_ids,
                        regulation_rule_ids=regulation_rule_ids,
                    ),
                    evidences=finding_evidences,
                    rule_references=finding_references,
                )
            )
        return AuditTaskPageResponse(
            id=page.id,
            task_id=page.task_id,
            page_number=page.page_number,
            status=page.status,
            attempt_count=page.attempt_count,
            finding_count=page.finding_count,
            error=page.error,
            started_at=page.started_at,
            completed_at=page.completed_at,
            content=source_page.content if source_page is not None else None,
            content_start=source_blocks[0].char_start if source_blocks else None,
            findings=finding_responses,
        )

    async def retry_task(self, *, task_id: UUID, user_id: UUID) -> tuple[AuditTask, bool]:
        task = await self.get_task(task_id=task_id, user_id=user_id)
        if task.status == AuditStatus.COMPLETED:
            return task, False
        # 重试请求只投递任务。状态和乐观锁版本必须等后台取得 Redis 总锁后
        # 才能修改，保证锁冲突时数据库完全不变。
        return task, True

    async def mark_dispatch_failed(
        self,
        *,
        task: AuditTask,
        user_id: UUID,
    ) -> AuditTask:
        """记录队列投递失败，并保留任务供用户稍后重试。"""
        async with self.uow:
            updated = await self.task_repository.mark_dispatch_failed(
                task_id=task.id,
                user_id=user_id,
                expected_lock_version=task.lock_version,
                # 新任务从未进入 Worker，应明确标为失败；已有失败任务重试
                # 入队失败时保留原状态和已生成结果。
                failure_status=(
                    AuditStatus.FAILED
                    if task.status == AuditStatus.CREATED
                    else task.status
                ),
                error=AUDIT_DISPATCH_FAILED_MESSAGE,
                completed_at=utc_now(),
            )
        if updated is not None:
            return updated

        # Redis 写入成功但客户端未收到响应时，Worker 可能已经领取任务并递增
        # lock_version。此时不能覆盖 Worker 状态，只返回数据库中的最新结果。
        return await self.get_task(task_id=task.id, user_id=user_id)


def get_audit_workflow_service(
    uow: UnitOfWork = Depends(get_uow),
    document_service: DocumentService = Depends(get_document_service),
) -> AuditWorkflowService:
    return AuditWorkflowService(
        uow=uow,
        document_service=document_service,
        document_repository=DocumentRepository(uow.session),
        task_repository=AuditTaskRepository(uow.session),
        result_repository=AuditResultRepository(uow.session),
        page_repository=DocumentPageRepository(uow.session),
        block_repository=DocumentParseBlockRepository(uow.session),
    )
