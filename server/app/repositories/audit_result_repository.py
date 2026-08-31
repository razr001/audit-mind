from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_task import AuditStatus, AuditTask
from app.models.audit_task_page import AuditTaskPage, AuditTaskPageStatus
from app.models.document_parse_block import DocumentParseBlock
from app.models.evidence import Evidence
from app.models.finding import Finding
from app.models.finding_rule_reference import FindingRuleReference


class AuditResultRepository:
    """管理逐页执行记录和已经过服务端校验的审计结果。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save_pages(self, pages: list[AuditTaskPage]) -> None:
        self.session.add_all(pages)
        await self.session.flush()

    async def find_pages(self, task_id: UUID) -> list[AuditTaskPage]:
        result = await self.session.execute(
            select(AuditTaskPage)
            .where(AuditTaskPage.task_id == task_id)
            .order_by(AuditTaskPage.page_number)
        )
        return list(result.scalars().all())

    async def find_page(self, *, task_id: UUID, page_number: int) -> AuditTaskPage | None:
        result = await self.session.execute(
            select(AuditTaskPage).where(
                AuditTaskPage.task_id == task_id,
                AuditTaskPage.page_number == page_number,
            )
        )
        return result.scalar_one_or_none()

    async def find_retryable_pages(self, task_id: UUID) -> list[AuditTaskPage]:
        result = await self.session.execute(
            select(AuditTaskPage)
            .where(
                AuditTaskPage.task_id == task_id,
                AuditTaskPage.status.in_(
                    [AuditTaskPageStatus.PENDING, AuditTaskPageStatus.FAILED]
                ),
            )
            .order_by(AuditTaskPage.page_number)
        )
        return list(result.scalars().all())

    async def reset_interrupted_pages(
        self,
        *,
        task_id: UUID,
        expected_lock_version: int,
    ) -> int:
        """把上一次异常退出遗留的 RUNNING 页面恢复为可重试状态。

        调用方必须已经持有任务 Redis 总锁并领取新的父任务执行版本。
        父任务版本条件既能防止无主恢复，也能阻止旧执行者在租约失效后
        继续写入这些页面。尝试次数保留，页面被再次领取时才递增。
        """
        result = await self.session.execute(
            update(AuditTaskPage)
            .where(
                AuditTaskPage.task_id == task_id,
                AuditTaskPage.status == AuditTaskPageStatus.RUNNING,
                self._task_is_owned(
                    task_id=task_id,
                    expected_lock_version=expected_lock_version,
                ),
            )
            .values(
                status=AuditTaskPageStatus.PENDING,
                finding_count=0,
                error=None,
                started_at=None,
                completed_at=None,
            )
            .returning(AuditTaskPage.id)
        )
        return len(result.scalars().all())

    async def claim_page(
        self,
        *,
        page_id: UUID,
        task_id: UUID,
        expected_lock_version: int,
        started_at: datetime,
    ) -> AuditTaskPage | None:
        task_is_owned = self._task_is_owned(
            task_id=task_id,
            expected_lock_version=expected_lock_version,
        )
        result = await self.session.execute(
            update(AuditTaskPage)
            .where(
                AuditTaskPage.id == page_id,
                AuditTaskPage.task_id == task_id,
                task_is_owned,
                AuditTaskPage.status.in_(
                    [AuditTaskPageStatus.PENDING, AuditTaskPageStatus.FAILED]
                ),
            )
            .values(
                status=AuditTaskPageStatus.RUNNING,
                attempt_count=AuditTaskPage.attempt_count + 1,
                error=None,
                started_at=started_at,
                completed_at=None,
            )
            .returning(AuditTaskPage)
        )
        return result.scalar_one_or_none()

    async def complete_page(
        self,
        *,
        page_id: UUID,
        task_id: UUID,
        expected_lock_version: int,
        expected_started_at: datetime | None,
        finding_count: int,
        completed_at: datetime,
    ) -> AuditTaskPage | None:
        result = await self.session.execute(
            update(AuditTaskPage)
            .where(
                AuditTaskPage.id == page_id,
                AuditTaskPage.task_id == task_id,
                self._task_is_owned(
                    task_id=task_id,
                    expected_lock_version=expected_lock_version,
                ),
                AuditTaskPage.status == AuditTaskPageStatus.RUNNING,
                AuditTaskPage.started_at == expected_started_at,
            )
            .values(
                status=AuditTaskPageStatus.COMPLETED,
                finding_count=finding_count,
                error=None,
                completed_at=completed_at,
            )
            .returning(AuditTaskPage)
        )
        return result.scalar_one_or_none()

    async def fail_page(
        self,
        *,
        page_id: UUID,
        task_id: UUID,
        expected_lock_version: int,
        expected_started_at: datetime | None,
        error: str,
        completed_at: datetime,
    ) -> AuditTaskPage | None:
        result = await self.session.execute(
            update(AuditTaskPage)
            .where(
                AuditTaskPage.id == page_id,
                AuditTaskPage.task_id == task_id,
                self._task_is_owned(
                    task_id=task_id,
                    expected_lock_version=expected_lock_version,
                ),
                AuditTaskPage.status == AuditTaskPageStatus.RUNNING,
                AuditTaskPage.started_at == expected_started_at,
            )
            .values(
                status=AuditTaskPageStatus.FAILED,
                finding_count=0,
                error=error[:2000],
                completed_at=completed_at,
            )
            .returning(AuditTaskPage)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _task_is_owned(*, task_id: UUID, expected_lock_version: int):
        """把父任务执行版本作为每次页面写入的统一 fencing token。"""
        return (
            select(AuditTask.id)
            .where(
                AuditTask.id == task_id,
                AuditTask.status == AuditStatus.RUNNING,
                AuditTask.lock_version == expected_lock_version,
            )
            .exists()
        )

    async def summarize_pages(self, task_id: UUID) -> tuple[int, int, int, int]:
        row = (
            await self.session.execute(
                select(
                    func.count(AuditTaskPage.id),
                    func.count(AuditTaskPage.id).filter(
                        AuditTaskPage.status == AuditTaskPageStatus.COMPLETED
                    ),
                    func.count(AuditTaskPage.id).filter(
                        AuditTaskPage.status == AuditTaskPageStatus.FAILED
                    ),
                    func.coalesce(func.sum(AuditTaskPage.finding_count), 0),
                ).where(AuditTaskPage.task_id == task_id)
            )
        ).one()
        return int(row[0]), int(row[1]), int(row[2]), int(row[3])

    async def find_page_results(
        self, *, task_id: UUID, page_number: int
    ) -> tuple[AuditTaskPage | None, list[Finding], list[Evidence], list[FindingRuleReference]]:
        page = await self.find_page(task_id=task_id, page_number=page_number)
        if page is None:
            return None, [], [], []
        finding_result = await self.session.execute(
            select(Finding)
            .where(Finding.task_id == task_id, Finding.task_page_id == page.id)
        )
        findings = list(finding_result.scalars().all())
        finding_ids = [finding.id for finding in findings]
        if not finding_ids:
            return page, findings, [], []
        evidence_result = await self.session.execute(
            select(Evidence, DocumentParseBlock.block_index)
            .join(
                DocumentParseBlock,
                DocumentParseBlock.id == Evidence.document_block_id,
            )
            .where(Evidence.finding_id.in_(finding_ids))
            .order_by(DocumentParseBlock.block_index, Evidence.created_at, Evidence.id)
        )
        evidence_rows = list(evidence_result.all())
        evidences = [row[0] for row in evidence_rows]
        first_block_index: dict[UUID, int] = {}
        for evidence, block_index in evidence_rows:
            first_block_index.setdefault(evidence.finding_id, block_index)
        # LLM 返回发现的顺序不等于 PDF 阅读顺序。使用每条发现最早引用的
        # MinerU 文档块排序，让页面顶部的问题始终先展示；无有效证据的
        # 历史异常数据放到末尾，并保留稳定的创建时间兜底顺序。
        findings.sort(
            key=lambda finding: (
                first_block_index.get(finding.id, 2**63 - 1),
                finding.created_at,
                str(finding.id),
            )
        )
        reference_result = await self.session.execute(
            select(FindingRuleReference)
            .where(FindingRuleReference.finding_id.in_(finding_ids))
            .order_by(FindingRuleReference.created_at, FindingRuleReference.id)
        )
        return (
            page,
            findings,
            evidences,
            list(reference_result.scalars().all()),
        )

    async def replace_page_findings(
        self,
        *,
        task_id: UUID,
        task_page_id: UUID,
        findings: list[Finding],
        evidences: list[Evidence],
        rule_references: list[FindingRuleReference],
    ) -> None:
        """只替换当前失败重试页面的结果，不触碰已经完成的其他页面。"""
        finding_ids = select(Finding.id).where(
            Finding.task_id == task_id,
            Finding.task_page_id == task_page_id,
        )
        await self.session.execute(
            delete(Evidence).where(Evidence.finding_id.in_(finding_ids))
        )
        await self.session.execute(
            delete(FindingRuleReference).where(
                FindingRuleReference.finding_id.in_(finding_ids)
            )
        )
        await self.session.execute(
            delete(Finding).where(
                Finding.task_id == task_id,
                Finding.task_page_id == task_page_id,
            )
        )
        # 这些模型只保存外键 ID，没有配置 ORM relationship。若一次性 add_all，
        # SQLAlchemy 无法从对象关系推断插入依赖，可能先写 Evidence，导致
        # finding_id 外键失败。因此先落 Finding，再写两类子记录。
        self.session.add_all(findings)
        await self.session.flush()
        self.session.add_all([*evidences, *rule_references])
        await self.session.flush()
