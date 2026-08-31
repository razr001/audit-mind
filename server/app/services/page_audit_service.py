import json
from collections.abc import Sequence
from typing import cast
from uuid import UUID

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.ai.page_audit.prompts import PAGE_AUDIT_SYSTEM_PROMPT, PAGE_AUDIT_USER_PROMPT
from app.ai.page_audit.schemas import PageAuditFindingOutput, PageAuditOutput
from app.core.audit_failure import (
    AUDIT_EXECUTION_FAILED_MESSAGE,
    AUDIT_RULES_NOT_FOUND_MESSAGE,
)
from app.core.logger import logger
from app.infrastructure.db.unit_of_work import UnitOfWork
from app.models.audit_task import AuditStage, AuditTask
from app.models.audit_task_page import AuditTaskPage
from app.repositories.audit_result_repository import AuditResultRepository
from app.repositories.document_parse_block_repository import DocumentParseBlockRepository
from app.schemas.audit_task import AuditRuleScope
from app.services.audit_progress_service import AuditProgressService
from app.services.audit_rule_retrieval_service import AuditRuleCandidate, AuditRuleRetrievalService
from app.services.page_audit_input import (
    IGNORED_AUDIT_BLOCK_TYPES,
    AuditInputBlock,
    build_adjacent_context,
    build_audit_batches,
    build_batch_contexts,
)
from app.services.page_audit_logging import log_page_audit_event
from app.services.page_audit_model_output import StructuredPageAuditModel, invoke_page_audit_model
from app.services.page_audit_result_service import PageAuditResultService
from app.services.page_audit_rule_snapshot import build_rule_payload
from app.services.page_audit_validation import (
    ValidatedPageFinding,
    validate_and_deduplicate_findings,
)
from app.unit.date import utc_now


class AuditRuleCandidatesNotFoundError(RuntimeError):
    """候选为空时不能把页面误判为合规。"""


class PageAuditService:
    """逐页审计并把模型选择的 ID 转换为可信、可定位的数据库记录。"""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        result_repository: AuditResultRepository,
        block_repository: DocumentParseBlockRepository,
        rule_retrieval: AuditRuleRetrievalService,
        progress_service: AuditProgressService,
        result_service: PageAuditResultService,
        model: BaseChatModel,
    ) -> None:
        self.uow = uow
        self.result_repository = result_repository
        self.block_repository = block_repository
        self.rule_retrieval = rule_retrieval
        self.progress_service = progress_service
        self.result_service = result_service
        # LangChain 的公开返回类型无法保留传入的 Pydantic Schema；运行时仍由
        # invoke_page_audit_model 校验结果类型，因此仅在第三方类型边界收窄。
        self.structured_model = cast(
            StructuredPageAuditModel,
            model.with_structured_output(
                PageAuditOutput,
                method="json_mode",
            ),
        )

    async def audit_pending_pages(
        self,
        *,
        task: AuditTask,
        user_id: UUID,
        expected_lock_version: int,
    ) -> AuditTask:
        # 事务回滚会使 SQLAlchemy 实体属性过期。异常路径只使用提前保存的
        # 标量，避免日志或失败状态处理再次触发异步懒加载并掩盖原始异常。
        task_id = task.id
        scope = AuditRuleScope.model_validate(task.rule_scope or {})
        async with self.uow:
            pages = await self.result_repository.find_retryable_pages(task_id)
        for page in pages:
            page_number = page.page_number
            claimed = await self._claim_page(
                task_id=task_id,
                page=page,
                expected_lock_version=expected_lock_version,
            )
            if claimed is None:
                continue
            try:
                await self._audit_claimed_page(
                    task=task,
                    page=claimed,
                    user_id=user_id,
                    scope=scope,
                    expected_lock_version=expected_lock_version,
                )
            except Exception as exc:
                logger.error(
                    "audit.page.failed",
                    task_id=str(task_id),
                    page_number=page_number,
                    stage=AuditStage.AUDITING.value,
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                error = (
                    AUDIT_RULES_NOT_FOUND_MESSAGE
                    if isinstance(exc, AuditRuleCandidatesNotFoundError)
                    else AUDIT_EXECUTION_FAILED_MESSAGE
                )
                await self.result_service.fail(
                    task_id=task_id,
                    page=claimed,
                    error=error,
                    expected_lock_version=expected_lock_version,
                )
            await self.progress_service.refresh(
                task=task,
                user_id=user_id,
                expected_lock_version=expected_lock_version,
            )
        return await self.progress_service.finalize(
            task=task,
            user_id=user_id,
            expected_lock_version=expected_lock_version,
        )

    async def _claim_page(
        self,
        *,
        task_id: UUID,
        page: AuditTaskPage,
        expected_lock_version: int,
    ) -> AuditTaskPage | None:
        async with self.uow:
            return await self.result_repository.claim_page(
                page_id=page.id,
                task_id=task_id,
                expected_lock_version=expected_lock_version,
                started_at=utc_now(),
            )

    async def _audit_claimed_page(
        self,
        *,
        task: AuditTask,
        page: AuditTaskPage,
        user_id: UUID,
        scope: AuditRuleScope,
        expected_lock_version: int,
    ) -> None:
        async with self.uow:
            blocks = await self.block_repository.find_by_document_and_page(
                document_id=task.document_id,
                page_number=page.page_number,
            )
            previous_blocks, next_blocks = (
                await self.block_repository.find_adjacent_page_blocks(
                    document_id=task.document_id,
                    page_number=page.page_number,
                )
            )
        blocks = [
            block
            for block in blocks
            if block.block_type not in IGNORED_AUDIT_BLOCK_TYPES
            and block.content.strip()
        ]
        if not blocks:
            await self.result_service.complete(
                task=task,
                page=page,
                findings=[],
                expected_lock_version=expected_lock_version,
            )
            return

        context_before, context_after = build_adjacent_context(
            previous_blocks=previous_blocks,
            next_blocks=next_blocks,
        )
        validated_findings: list[ValidatedPageFinding] = []
        output_count = 0
        batches = build_audit_batches(blocks)
        batch_contexts = build_batch_contexts(
            batches=batches,
            page_context_before=context_before,
            page_context_after=context_after,
        )
        for batch_index, (batch, batch_context) in enumerate(
            zip(batches, batch_contexts, strict=True),
            start=1,
        ):
            batch_context_before, batch_context_after = batch_context
            batch_query = "\n\n".join(
                value
                for value in (
                    batch_context_before,
                    *(block.content for block in batch),
                    batch_context_after,
                )
                if value
            )
            log_page_audit_event(
                "audit.page.rules_retrieval_started",
                task_id=task.id,
                page_number=page.page_number,
                batch_index=batch_index,
                batch_count=len(batches),
                block_count=len(batch),
            )
            candidates = await self.rule_retrieval.retrieve(
                user_id=user_id,
                task_id=task.id,
                page_number=page.page_number,
                batch_index=batch_index,
                page_query=batch_query,
                scope=scope,
                audit_as_of=task.audit_as_of,
            )
            log_page_audit_event(
                "audit.page.rules_retrieval_completed",
                task_id=task.id,
                page_number=page.page_number,
                batch_index=batch_index,
                candidate_count=len(candidates),
            )
            if not candidates:
                raise AuditRuleCandidatesNotFoundError(
                    f"no applicable regulation rules were retrieved for batch {batch_index}"
                )
            log_page_audit_event(
                "audit.page.model_batch_started",
                task_id=task.id,
                page_number=page.page_number,
                batch_index=batch_index,
                batch_count=len(batches),
                block_count=len(batch),
            )
            batch_outputs = await self._invoke_model(
                task_id=task.id,
                page_number=page.page_number,
                batch_index=batch_index,
                blocks=batch,
                candidates=candidates,
                context_before=batch_context_before,
                context_after=batch_context_after,
            )
            # 每批立即校验引用范围，模型不能引用其他批次的块或规则 ID。
            validated_findings.extend(
                validate_and_deduplicate_findings(
                    outputs=batch_outputs,
                    blocks=batch,
                    candidates=candidates,
                )
            )
            output_count += len(batch_outputs)
            log_page_audit_event(
                "audit.page.model_batch_completed",
                task_id=task.id,
                page_number=page.page_number,
                batch_index=batch_index,
                finding_candidate_count=output_count,
            )
        await self.result_service.complete(
            task=task,
            page=page,
            findings=validated_findings,
            expected_lock_version=expected_lock_version,
        )
        log_page_audit_event(
            "audit.page.completed",
            task_id=task.id,
            page_number=page.page_number,
            finding_count=len(validated_findings),
        )

    async def _invoke_model(
        self,
        *,
        task_id: UUID,
        page_number: int,
        batch_index: int,
        blocks: Sequence[AuditInputBlock],
        candidates: Sequence[AuditRuleCandidate],
        context_before: str,
        context_after: str,
    ) -> list[PageAuditFindingOutput]:
        payload = {
            "contextBefore": context_before,
            "documentBlocks": [
                {
                    "id": str(block.id),
                    "type": block.block_type,
                    "content": block.content,
                }
                for block in blocks
            ],
            "contextAfter": context_after,
            "candidateRules": [build_rule_payload(candidate.rule) for candidate in candidates],
        }
        messages = [
            SystemMessage(content=PAGE_AUDIT_SYSTEM_PROMPT),
            HumanMessage(
                content=PAGE_AUDIT_USER_PROMPT.format(
                    page_number=page_number,
                    payload=json.dumps(payload, ensure_ascii=False),
                )
            ),
        ]
        result = await invoke_page_audit_model(
            model=self.structured_model,
            messages=messages,
            task_id=task_id,
            page_number=page_number,
            batch_index=batch_index,
        )
        return result.findings
