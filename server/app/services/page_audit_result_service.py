from uuid import UUID, uuid4

from app.infrastructure.db.unit_of_work import UnitOfWork
from app.models.audit_task import AuditTask
from app.models.audit_task_page import AuditTaskPage
from app.models.evidence import Evidence
from app.models.finding import Finding
from app.models.finding_rule_reference import FindingRuleReference
from app.repositories.audit_result_repository import AuditResultRepository
from app.services.page_audit_rule_snapshot import build_rule_reference
from app.services.page_audit_validation import ValidatedPageFinding
from app.unit.date import utc_now


class PageAuditResultService:
    """原子保存单页发现，并使用任务版本和页面 token 双重防并发覆盖。"""

    def __init__(self, *, uow: UnitOfWork, repository: AuditResultRepository) -> None:
        self.uow = uow
        self.repository = repository

    async def complete(
        self,
        *,
        task: AuditTask,
        page: AuditTaskPage,
        findings: list[ValidatedPageFinding],
        expected_lock_version: int,
    ) -> None:
        finding_rows: list[Finding] = []
        evidence_rows: list[Evidence] = []
        reference_rows: list[FindingRuleReference] = []
        for output, blocks, rules in findings:
            finding_id = uuid4()
            finding_rows.append(
                Finding(
                    id=finding_id,
                    task_id=task.id,
                    task_page_id=page.id,
                    page_number=page.page_number,
                    level=output.level,
                    title=output.title.strip(),
                    description=output.reason.strip(),
                    recommendation=(output.recommendation or "").strip() or None,
                )
            )
            evidence_rows.extend(
                Evidence(
                    id=uuid4(),
                    finding_id=finding_id,
                    document_block_id=block.id,
                    page_number=page.page_number,
                    quote=block.content,
                    bbox=block.bbox,
                    char_start=block.char_start,
                    char_end=block.char_end,
                )
                for block in blocks
            )
            reference_rows.extend(
                build_rule_reference(finding_id=finding_id, rule=rule) for rule in rules
            )

        async with self.uow:
            await self.repository.replace_page_findings(
                task_id=task.id,
                task_page_id=page.id,
                findings=finding_rows,
                evidences=evidence_rows,
                rule_references=reference_rows,
            )
            completed = await self.repository.complete_page(
                page_id=page.id,
                task_id=task.id,
                expected_lock_version=expected_lock_version,
                expected_started_at=page.started_at,
                finding_count=len(finding_rows),
                completed_at=utc_now(),
            )
            if completed is None:
                # UoW 会回滚本事务先写入的 Finding，旧执行者不会污染结果。
                raise RuntimeError("audit page completion state conflict")

    async def fail(
        self,
        *,
        task_id: UUID,
        page: AuditTaskPage,
        error: str,
        expected_lock_version: int,
    ) -> None:
        async with self.uow:
            await self.repository.fail_page(
                page_id=page.id,
                task_id=task_id,
                expected_lock_version=expected_lock_version,
                expected_started_at=page.started_at,
                error=error,
                completed_at=utc_now(),
            )
