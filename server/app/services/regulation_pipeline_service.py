import asyncio
from collections.abc import Callable
from time import monotonic
from uuid import UUID

from structlog.contextvars import bound_contextvars

from app.core.config import get_settings
from app.core.error_codes import REGULATION_NOT_FOUND, REGULATION_STATUS_INVALID
from app.core.exceptions import BusinessException
from app.core.logger import logger
from app.core.regulation_failure import log_regulation_failure
from app.infrastructure.db.session import async_session_factory
from app.infrastructure.db.unit_of_work import UnitOfWork
from app.infrastructure.redis_lock import run_with_lease_guard
from app.infrastructure.regulation_pipeline_lock import (
    acquire_regulation_pipeline_lease,
)
from app.models.regulation import (
    Regulation,
    RegulationChunkStatus,
    RegulationIndexStatus,
    RegulationRuleStatus,
    RegulationStatus,
)
from app.repositories.regulation_repository import RegulationRepository
from app.services.regulation_index_service import RegulationIndexService
from app.services.regulation_knowledge_service import RegulationKnowledgeService
from app.services.regulation_parse_service import RegulationParseService
from app.services.regulation_rule_orchestrator import RegulationRuleService

settings = get_settings()


class RegulationPipelineService:
    """按现有状态串联法规解析、Chunk、索引和规则构建。"""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        repository: RegulationRepository,
        parse_service: RegulationParseService,
        knowledge_service: RegulationKnowledgeService,
        index_service_provider: Callable[[], RegulationIndexService],
        rule_service: RegulationRuleService,
        poll_interval_seconds: float,
        wait_timeout_seconds: float,
    ) -> None:
        self.uow = uow
        self.repository = repository
        self.parse_service = parse_service
        self.knowledge_service = knowledge_service
        self.index_service_provider = index_service_provider
        self.rule_service = rule_service
        self.poll_interval_seconds = poll_interval_seconds
        self.wait_timeout_seconds = wait_timeout_seconds

    async def get_current(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
    ) -> Regulation:
        """校验上传者身份并返回流水线当前状态。"""
        async with self.uow:
            regulation = await self.repository.find_by_id_and_user(
                regulation_id=regulation_id,
                user_id=user_id,
            )
        if regulation is None:
            raise BusinessException(REGULATION_NOT_FOUND, "regulation not found")
        if not regulation.enabled:
            raise BusinessException(
                REGULATION_STATUS_INVALID,
                "disabled regulation cannot be processed",
            )
        return regulation

    async def run(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
    ) -> Regulation:
        """跳过 READY 步骤；FAILED/PENDING 步骤由原 Service 重新抢占。"""
        stage = "load"
        started_at = monotonic()
        try:
            regulation = await self.get_current(
                regulation_id=regulation_id,
                user_id=user_id,
            )
            self._log_state("regulation.pipeline.started", regulation)

            stage = "parse"
            if regulation.status in {RegulationStatus.UPLOADED, RegulationStatus.FAILED} or (
                regulation.status == RegulationStatus.PARSING
                and hasattr(regulation, "parse_task_id")
                and not regulation.parse_task_id
            ):
                self._log_state("regulation.pipeline.parse.started", regulation)
                regulation = await self.parse_service.start_parse(
                    regulation_id=regulation_id,
                    user_id=user_id,
                )
                self._log_state("regulation.pipeline.parse.task_created", regulation)
                if regulation.status == RegulationStatus.PARSING:
                    if hasattr(regulation, "parse_task_id") and not regulation.parse_task_id:
                        self._log_state(
                            "regulation.pipeline.parse.awaiting_stale_takeover",
                            regulation,
                        )
                        return regulation
                    await asyncio.sleep(self.poll_interval_seconds)
            elif regulation.status == RegulationStatus.READY:
                self._log_skipped(regulation, stage=stage)

            deadline = monotonic() + self.wait_timeout_seconds
            sync_attempt = 0
            next_wait_log_at = monotonic()
            while regulation.status == RegulationStatus.PARSING:
                sync_attempt += 1
                regulation = await self.parse_service.sync_parse_result(
                    regulation_id=regulation_id,
                    user_id=user_id,
                )
                if regulation.status != RegulationStatus.PARSING:
                    self._log_state(
                        "regulation.pipeline.parse.completed",
                        regulation,
                        sync_attempt=sync_attempt,
                    )
                    break

                now = monotonic()
                # 首次以及每 30 秒记录一次等待状态，避免轮询刷屏。
                if now >= next_wait_log_at:
                    self._log_state(
                        "regulation.pipeline.parse.waiting",
                        regulation,
                        sync_attempt=sync_attempt,
                    )
                    next_wait_log_at = now + 30
                if now >= deadline:
                    self._log_state(
                        "regulation.pipeline.parse.wait_timeout",
                        regulation,
                        sync_attempt=sync_attempt,
                    )
                    return regulation
                await asyncio.sleep(self.poll_interval_seconds)

            if regulation.status != RegulationStatus.READY:
                self._log_state(
                    "regulation.pipeline.stopped",
                    regulation,
                    stopped_stage=stage,
                )
                return regulation

            stage = "chunk"
            if regulation.chunk_status != RegulationChunkStatus.READY:
                self._log_state("regulation.pipeline.chunk.started", regulation)
                regulation = await self.knowledge_service.build(
                    regulation_id=regulation_id,
                    user_id=user_id,
                )
                self._log_state("regulation.pipeline.chunk.completed", regulation)
            else:
                self._log_skipped(regulation, stage=stage)

            if regulation.chunk_status != RegulationChunkStatus.READY:
                self._log_state(
                    "regulation.pipeline.paused",
                    regulation,
                    stopped_stage=stage,
                )
                return regulation

            stage = "index"
            if regulation.index_status != RegulationIndexStatus.READY:
                self._log_state("regulation.pipeline.index.started", regulation)
                # Provider 只在确实需要索引时调用，避免解析阶段提前初始化 Embedding。
                index_service = self.index_service_provider()
                regulation = await index_service.index(
                    regulation_id=regulation_id,
                    user_id=user_id,
                )
                self._log_state("regulation.pipeline.index.completed", regulation)
            else:
                self._log_skipped(regulation, stage=stage)

            if regulation.index_status != RegulationIndexStatus.READY:
                self._log_state(
                    "regulation.pipeline.paused",
                    regulation,
                    stopped_stage=stage,
                )
                return regulation

            stage = "rule"
            if regulation.rule_status != RegulationRuleStatus.READY:
                self._log_state("regulation.pipeline.rule.started", regulation)
                regulation = await self.rule_service.build(
                    regulation_id=regulation_id,
                    user_id=user_id,
                )
                self._log_state("regulation.pipeline.rule.completed", regulation)
            else:
                self._log_skipped(regulation, stage=stage)

            event = (
                "regulation.pipeline.completed"
                if self._is_completed(regulation)
                else "regulation.pipeline.paused"
            )
            self._log_state(
                event,
                regulation,
                duration_ms=round((monotonic() - started_at) * 1000),
            )
            return regulation

        except Exception as exc:
            log_regulation_failure(
                f"regulation.pipeline.{stage}.failed",
                regulation_id=regulation_id,
                error=exc,
            )
            raise

    @staticmethod
    def _is_completed(regulation: Regulation) -> bool:
        return (
            regulation.status == RegulationStatus.READY
            and regulation.chunk_status == RegulationChunkStatus.READY
            and regulation.index_status == RegulationIndexStatus.READY
            and regulation.rule_status == RegulationRuleStatus.READY
        )

    @staticmethod
    def _log_state(
        event: str,
        regulation: Regulation,
        **fields: object,
    ) -> None:
        """只记录状态枚举，不写法规正文、文件名或第三方响应内容。"""
        logger.info(
            event,
            regulation_id=str(regulation.id),
            parse_status=regulation.status.value,
            chunk_status=regulation.chunk_status.value,
            index_status=regulation.index_status.value,
            rule_status=regulation.rule_status.value,
            **fields,
        )

    @classmethod
    def _log_skipped(cls, regulation: Regulation, *, stage: str) -> None:
        cls._log_state(
            "regulation.pipeline.step_skipped",
            regulation,
            skipped_stage=stage,
            reason="ready",
        )


async def get_regulation_pipeline_state(
    *,
    regulation_id: UUID,
    user_id: UUID,
) -> Regulation:
    """请求返回前只读取当前状态，不提前初始化 Embedding 等下游客户端。"""
    async with async_session_factory() as session:
        uow = UnitOfWork(session)
        async with uow:
            regulation = await RegulationRepository(session).find_by_id_and_user(
                regulation_id=regulation_id,
                user_id=user_id,
            )
        if regulation is None:
            raise BusinessException(REGULATION_NOT_FOUND, "regulation not found")
        if not regulation.enabled:
            raise BusinessException(
                REGULATION_STATUS_INVALID,
                "disabled regulation cannot be processed",
            )
        return regulation


async def run_regulation_pipeline(
    *,
    regulation_id: UUID,
    user_id: UUID,
) -> None:
    """持有自动续租的总锁执行整条流水线，同一法规重复请求直接返回。"""
    # Worker 是独立进程，不能继承 FastAPI 的请求 ContextVar；Actor 会恢复
    # request_id，此处补充 user_id，使所有流水线日志都带上业务身份。
    with bound_contextvars(user_id=str(user_id)):
        try:
            async with acquire_regulation_pipeline_lease(regulation_id) as acquired:
                if not acquired:
                    logger.info(
                        "regulation.pipeline.lock_conflict",
                        regulation_id=str(regulation_id),
                    )
                    return

                logger.info(
                    "regulation.pipeline.lock_acquired",
                    regulation_id=str(regulation_id),
                )
                try:
                    async with async_session_factory() as session:
                        try:
                            # 局部导入避免 Factory 在模块初始化阶段反向引用 Service。
                            from app.services.regulation_pipeline_factory import (
                                build_regulation_pipeline_service,
                            )

                            service = build_regulation_pipeline_service(session)
                        except Exception as exc:
                            log_regulation_failure(
                                "regulation.pipeline.initialization.failed",
                                regulation_id=regulation_id,
                                error=exc,
                            )
                            return
                        await run_with_lease_guard(
                            acquired,
                            service.run(
                                regulation_id=regulation_id,
                                user_id=user_id,
                            ),
                        )
                except Exception:
                    # run() 已记录具体失败步骤及安全调用栈，避免重复错误日志。
                    return
                finally:
                    logger.info(
                        "regulation.pipeline.lock_releasing",
                        regulation_id=str(regulation_id),
                    )
        except Exception as exc:
            # Redis 不可用时后台响应已经返回，必须在这里留下可检索的失败事件，
            # 不能只依赖 ASGI 输出一条缺少业务阶段的异常。
            log_regulation_failure(
                "regulation.pipeline.lock_failed",
                regulation_id=regulation_id,
                error=exc,
            )
