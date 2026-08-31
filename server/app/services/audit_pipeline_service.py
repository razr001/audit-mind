import asyncio
from datetime import timedelta
from uuid import UUID

from structlog.contextvars import bound_contextvars

from app.ai.embedding import get_embedding_service
from app.ai.model import get_chat_model
from app.ai.reranking.factory import get_reranker
from app.core.audit_failure import (
    AUDIT_EXECUTION_FAILED_MESSAGE,
    AUDIT_RULES_MAINTAINING_MESSAGE,
)
from app.core.config import get_settings
from app.core.error_codes import AUDIT_TASK_NOT_FOUND
from app.core.exceptions import BusinessException
from app.core.logger import logger
from app.infrastructure.audit_pipeline_lock import acquire_audit_pipeline_lease
from app.infrastructure.db.session import async_session_factory
from app.infrastructure.db.unit_of_work import UnitOfWork
from app.infrastructure.mineru_client import mineru_client
from app.infrastructure.redis_lock import run_with_lease_guard
from app.infrastructure.regulation_pipeline_lock import (
    is_regulation_rule_index_maintenance_active,
)
from app.infrastructure.regulation_rule_vector_store import regulation_rule_vector_store
from app.models.audit_task import AuditStage, AuditStatus, AuditTask
from app.models.audit_task_page import AuditTaskPage
from app.models.document import DocumentSourceType, DocumentStatus
from app.repositories.audit_result_repository import AuditResultRepository
from app.repositories.audit_task_repository import AuditTaskRepository
from app.repositories.document_page_repository import DocumentPageRepository
from app.repositories.document_parse_block_repository import DocumentParseBlockRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.regulation_rule_repository import RegulationRuleRepository
from app.services.audit_progress_service import AuditProgressService
from app.services.audit_rule_retrieval_service import AuditRuleRetrievalService
from app.services.document_parse_service import DocumentParseService
from app.services.document_storage_service import DocumentStorageService
from app.services.markdown_document_parse_service import MarkdownDocumentParseService
from app.services.page_audit_result_service import PageAuditResultService
from app.services.page_audit_service import PageAuditService
from app.services.regulation_rule_index_service import RegulationRuleIndexService
from app.unit.date import utc_now

settings = get_settings()


class AuditPipelineService:
    """串联文档解析和逐页审计，每一步均可幂等恢复。"""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        task_repository: AuditTaskRepository,
        document_repository: DocumentRepository,
        page_repository: DocumentPageRepository,
        result_repository: AuditResultRepository,
        parse_service: DocumentParseService,
        markdown_parse_service: MarkdownDocumentParseService,
        page_audit_service: PageAuditService,
    ) -> None:
        self.uow = uow
        self.task_repository = task_repository
        self.document_repository = document_repository
        self.page_repository = page_repository
        self.result_repository = result_repository
        self.parse_service = parse_service
        self.markdown_parse_service = markdown_parse_service
        self.page_audit_service = page_audit_service

    async def run(
        self,
        *,
        task_id: UUID,
        user_id: UUID,
        expected_lock_version: int,
    ) -> None:
        task = await self._load_task(task_id=task_id, user_id=user_id)
        if task.lock_version != expected_lock_version:
            logger.info(
                "audit.pipeline.execution_superseded",
                task_id=str(task_id),
                expected_lock_version=expected_lock_version,
                actual_lock_version=task.lock_version,
            )
            return
        # 回滚会使 ORM 实体过期，异常日志不能再读取 task.stage。
        # 使用显式标量记录当前阶段，确保原始异常不会被 MissingGreenlet 覆盖。
        current_stage = task.stage
        try:
            current_stage = AuditStage.PARSING
            document = await self._ensure_parsed(
                task=task,
                user_id=user_id,
                expected_lock_version=expected_lock_version,
            )
            current_stage = AuditStage.AUDITING
            await self._prepare_page_audit(
                task=task,
                user_id=user_id,
                document_id=document.id,
                expected_lock_version=expected_lock_version,
            )
            logger.info(
                "audit.pipeline.audit_ready",
                task_id=str(task.id),
                document_id=str(document.id),
                stage=AuditStage.AUDITING.value,
            )
            await self.page_audit_service.audit_pending_pages(
                task=task,
                user_id=user_id,
                expected_lock_version=expected_lock_version,
            )
        except Exception as exc:
            logger.error(
                "audit.pipeline.failed",
                task_id=str(task_id),
                stage=current_stage.value,
                error_type=type(exc).__name__,
                exc_info=True,
            )
            async with self.uow:
                await self.task_repository.update_pipeline_state(
                    task_id=task_id,
                    user_id=user_id,
                    expected_lock_version=expected_lock_version,
                    values={
                        "status": AuditStatus.FAILED,
                        "error": AUDIT_EXECUTION_FAILED_MESSAGE,
                        "completed_at": utc_now(),
                    },
                )

    async def _load_task(self, *, task_id: UUID, user_id: UUID) -> AuditTask:
        async with self.uow:
            task = await self.task_repository.find_by_id_and_user(
                task_id=task_id, user_id=user_id
            )
        if task is None:
            raise BusinessException(AUDIT_TASK_NOT_FOUND, "audit task not found")
        return task

    async def _ensure_parsed(
        self,
        *,
        task: AuditTask,
        user_id: UUID,
        expected_lock_version: int,
    ):
        task.stage = AuditStage.PARSING
        async with self.uow:
            document = await self.document_repository.find_by_id_and_user(
                task.document_id, user_id
            )
            updated = await self.task_repository.update_pipeline_state(
                task_id=task.id,
                user_id=user_id,
                expected_lock_version=expected_lock_version,
                values={"stage": AuditStage.PARSING, "status": AuditStatus.RUNNING, "error": None},
            )
            if updated is None:
                raise RuntimeError("audit pipeline execution superseded")
        if document is None:
            raise RuntimeError("audit document disappeared")
        if document.status in {DocumentStatus.UPLOADED, DocumentStatus.FAILED}:
            if document.source_type == DocumentSourceType.MARKDOWN:
                document = await self.markdown_parse_service.parse(
                    document_id=document.id,
                    user_id=user_id,
                )
            else:
                document = await self.parse_service.start_parse(
                    document_id=document.id, user_id=user_id
                )
        if document.source_type == DocumentSourceType.MARKDOWN:
            if document.status != DocumentStatus.READY:
                raise RuntimeError("Markdown document parsing failed")
            return document
        deadline = asyncio.get_running_loop().time() + settings.AUDIT_PIPELINE_WAIT_TIMEOUT_SECONDS
        while document.status == DocumentStatus.PARSING:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("document parsing timed out")
            await asyncio.sleep(settings.AUDIT_PIPELINE_POLL_INTERVAL_SECONDS)
            document = await self.parse_service.sync_parse_result(
                document_id=document.id, user_id=user_id
            )
        if document.status != DocumentStatus.READY:
            raise RuntimeError("document parsing failed")
        return document

    async def _prepare_page_audit(
        self,
        *,
        task: AuditTask,
        user_id: UUID,
        document_id: UUID,
        expected_lock_version: int,
    ) -> None:
        task.stage = AuditStage.AUDITING
        async with self.uow:
            pages = await self.page_repository.find_by_document(document_id)
            existing = await self.result_repository.find_pages(task.id)
            if not existing:
                await self.result_repository.save_pages(
                    [AuditTaskPage(task_id=task.id, page_number=page.page_number) for page in pages]
                )
            completed = sum(1 for page in existing if page.status.value == "COMPLETED")
            findings = sum(page.finding_count for page in existing if page.status.value == "COMPLETED")
            updated = await self.task_repository.update_pipeline_state(
                task_id=task.id,
                user_id=user_id,
                expected_lock_version=expected_lock_version,
                values={
                    "stage": AuditStage.AUDITING,
                    "total_pages": len(pages),
                    "completed_pages": completed,
                    "finding_count": findings,
                },
            )
            if updated is None:
                raise RuntimeError("audit pipeline execution superseded")


def _build_audit_pipeline_service(session) -> AuditPipelineService:
    """后台任务显式组装全部依赖，确保不复用已经结束的请求 Session。"""
    uow = UnitOfWork(session)
    document_repository = DocumentRepository(session)
    page_repository = DocumentPageRepository(session)
    parse_block_repository = DocumentParseBlockRepository(session)
    parse_service = DocumentParseService(
        uow=uow,
        repository=document_repository,
        storage=DocumentStorageService(),
        mineru=mineru_client,
        page_repository=page_repository,
        parse_block_repository=parse_block_repository,
    )
    markdown_parse_service = MarkdownDocumentParseService(
        uow=uow,
        repository=document_repository,
        storage=DocumentStorageService(),
        page_repository=page_repository,
        parse_block_repository=parse_block_repository,
    )
    rule_index_service = RegulationRuleIndexService(
        embedding=get_embedding_service(),
        vector_store=regulation_rule_vector_store,
    )
    rule_retrieval = AuditRuleRetrievalService(
        search_service=rule_index_service,
        rule_repository=RegulationRuleRepository(session),
        uow=uow,
        reranker=get_reranker(),
        candidate_count=settings.AI_RERANK_CANDIDATE_COUNT,
        top_n=settings.AI_RERANK_TOP_N,
    )
    result_repository = AuditResultRepository(session)
    task_repository = AuditTaskRepository(session)
    page_audit_service = PageAuditService(
        uow=uow,
        result_repository=result_repository,
        block_repository=parse_block_repository,
        rule_retrieval=rule_retrieval,
        progress_service=AuditProgressService(
            uow=uow,
            task_repository=task_repository,
            result_repository=result_repository,
        ),
        result_service=PageAuditResultService(
            uow=uow,
            repository=result_repository,
        ),
        model=get_chat_model(),
    )
    return AuditPipelineService(
        uow=uow,
        task_repository=task_repository,
        document_repository=document_repository,
        page_repository=page_repository,
        result_repository=result_repository,
        parse_service=parse_service,
        markdown_parse_service=markdown_parse_service,
        page_audit_service=page_audit_service,
    )


async def _claim_audit_pipeline_execution(*, task_id: UUID, user_id: UUID) -> int | None:
    """领取数据库执行版本，并恢复上一执行者中断时遗留的页面。"""
    started_at = utc_now()
    stale_before = started_at - timedelta(seconds=settings.AUDIT_TASK_STALE_SECONDS)
    execution_version: int | None = None
    reset_page_count = 0
    async with async_session_factory() as session:
        uow = UnitOfWork(session)
        async with uow:
            execution_version = await AuditTaskRepository(
                session
            ).claim_pipeline_execution(
                task_id=task_id,
                user_id=user_id,
                started_at=started_at,
                stale_before=stale_before,
            )
            if execution_version is not None:
                # 只有 Redis 抢锁成功且父任务已领取新 fencing token 的执行者，
                # 才能恢复旧进程遗留的 RUNNING 页面。恢复和领取在同一事务中，
                # 避免父任务已接管而页面仍不可重试的中间状态。
                reset_page_count = await AuditResultRepository(
                    session
                ).reset_interrupted_pages(
                    task_id=task_id,
                    expected_lock_version=execution_version,
                )
    if reset_page_count:
        logger.info(
            "audit.pipeline.interrupted_pages_reset",
            task_id=str(task_id),
            execution_version=execution_version,
            page_count=reset_page_count,
        )
    return execution_version


async def _fail_audit_for_rules_maintenance(
    *,
    task_id: UUID,
    user_id: UUID,
    expected_lock_version: int,
) -> None:
    """使用独立短事务保存后台竞态产生的可重试失败状态。"""
    async with async_session_factory() as session:
        uow = UnitOfWork(session)
        async with uow:
            updated = await AuditTaskRepository(session).fail_for_rules_maintenance(
                task_id=task_id,
                user_id=user_id,
                expected_lock_version=expected_lock_version,
                error=AUDIT_RULES_MAINTAINING_MESSAGE,
                completed_at=utc_now(),
            )
    logger.info(
        "audit.pipeline.rules_maintaining",
        task_id=str(task_id),
        task_marked_failed=updated,
    )


async def run_audit_pipeline(
    *,
    task_id: UUID,
    user_id: UUID,
) -> None:
    """持有总租约运行流水线；锁冲突只返回，不修改任何业务状态。"""
    with bound_contextvars(user_id=str(user_id)):
        try:
            async with acquire_audit_pipeline_lease(task_id) as acquired:
                if not acquired:
                    logger.info("audit.pipeline.lock_conflict", task_id=str(task_id))
                    return
                execution_version = await _claim_audit_pipeline_execution(
                    task_id=task_id,
                    user_id=user_id,
                )
                if execution_version is None:
                    logger.info("audit.pipeline.not_claimed", task_id=str(task_id))
                    return
                # 普通法规处理和单条删除不会阻塞审计；仅全局规则索引维护暂停新任务。
                if await is_regulation_rule_index_maintenance_active():
                    # 请求层检查与后台真正启动之间仍有时间窗口。这里保存明确的
                    # 可重试失败原因，避免任务永久停留在 CREATED/RUNNING。
                    await _fail_audit_for_rules_maintenance(
                        task_id=task_id,
                        user_id=user_id,
                        expected_lock_version=execution_version,
                    )
                    return
                async with async_session_factory() as session:
                    await run_with_lease_guard(
                        acquired,
                        _build_audit_pipeline_service(session).run(
                            task_id=task_id,
                            user_id=user_id,
                            expected_lock_version=execution_version,
                        ),
                    )
        except Exception as exc:
            logger.error(
                "audit.pipeline.runner_failed",
                task_id=str(task_id),
                error_type=type(exc).__name__,
                exc_info=True,
            )
