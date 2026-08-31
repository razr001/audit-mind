from datetime import timedelta
from typing import BinaryIO
from uuid import UUID

from fastapi import Depends
from structlog.contextvars import bound_contextvars

from app.ai.visual_analyzer import (
    RegulationVisualAnalyzer,
    get_regulation_visual_analyzer,
)
from app.core.config import get_settings
from app.core.error_codes import (
    REGULATION_NOT_FOUND,
    REGULATION_STATUS_INVALID,
)
from app.core.exceptions import BusinessException
from app.core.regulation_failure import (
    REGULATION_FAILURE_CODES,
    log_regulation_failure,
)
from app.infrastructure.db.session import async_session_factory
from app.infrastructure.db.unit_of_work import (
    UnitOfWork,
    get_uow,
)
from app.infrastructure.mineru_client import (
    MinerUClient,
    mineru_client,
)
from app.infrastructure.redis_lock import run_with_lease_guard
from app.infrastructure.regulation_pipeline_lock import (
    acquire_regulation_pipeline_lease,
)
from app.models import RegulationParseBlock
from app.models.regulation import (
    Regulation,
    RegulationStatus,
)
from app.repositories.regulation_parse_block_repository import (
    RegulationParseBlockRepository,
)
from app.repositories.regulation_repository import (
    RegulationRepository,
)
from app.services.regulation_parse_archive_service import RegulationParseArchiveService
from app.services.regulation_parse_block_builder import RegulationParseBlockBuilder
from app.services.regulation_parse_result_service import RegulationParseResultService
from app.services.regulation_storage_service import (
    RegulationStorageService,
)
from app.unit.date import utc_now

settings = get_settings()
MAX_ZIP_COMPRESSION_RATIO = 100


class RegulationParseService(RegulationParseBlockBuilder):
    """编排法规文件的 MinerU 任务和原始 ParseBlock 持久化。"""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        repository: RegulationRepository,
        parse_block_repository: RegulationParseBlockRepository,
        storage: RegulationStorageService,
        mineru: MinerUClient,
        visual_analyzer: RegulationVisualAnalyzer | None,
    ) -> None:
        self.uow = uow
        self.repository = repository
        self.parse_block_repository = parse_block_repository
        self.storage = storage
        self.mineru = mineru
        self.visual_analyzer = visual_analyzer
        self.archive_service = RegulationParseArchiveService(
            storage=storage,
            visual_analyzer=visual_analyzer,
        )
        self.result_service = RegulationParseResultService(
            uow=uow,
            repository=repository,
            parse_block_repository=parse_block_repository,
            mineru=mineru,
        )

    async def start_parse(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
    ) -> Regulation:
        """原子抢占解析权，随后把 MinIO 文件流提交给 MinerU。"""
        started_at = utc_now()
        stale_before = started_at - timedelta(
            seconds=settings.REGULATION_PARSE_STALE_SECONDS
        )
        async with self.uow:
            regulation = await self.repository.claim_for_parse(
                regulation_id=regulation_id,
                user_id=user_id,
                started_at=started_at,
                stale_before=stale_before,
            )

            if regulation is None:
                existing = await self.repository.find_by_id_and_user(
                    regulation_id=regulation_id,
                    user_id=user_id,
                )

                if existing is None:
                    raise BusinessException(
                        REGULATION_NOT_FOUND,
                        "regulation not found",
                    )

                # PARSING 但尚无 task_id 表示另一个提交尝试仍在超时窗口内。
                # 此时不能再次调用 MinerU；流水线保持当前状态，等待下次恢复。
                if (
                    existing.status == RegulationStatus.PARSING
                    and existing.parse_task_id is None
                ):
                    return existing

                raise BusinessException(
                    REGULATION_STATUS_INVALID,
                    (f"regulation cannot be parsed in status {existing.status.value}"),
                )
        expected_lock_version = regulation.lock_version

        try:
            content_stream = self.storage.stream(regulation.storage_key)

            task_id = await self.mineru.create_task(
                filename=regulation.original_filename,
                content=content_stream,
                content_type=regulation.content_type,
                content_length=regulation.file_size,
                backend=settings.MINERU_BACKEND,
                server_url=settings.MINERU_SERVER_URL,
                effort=settings.MINERU_EFFORT,
                parse_method=settings.MINERU_PARSE_METHOD,
                formula_enable=settings.MINERU_FORMULA_ENABLE,
                table_enable=settings.MINERU_TABLE_ENABLE,
                image_analysis=settings.MINERU_IMAGE_ANALYSIS,
                return_images=True,
                response_format_zip=True,
            )
        except Exception as exc:
            log_regulation_failure(
                "regulation_mineru_task_creation_failed",
                regulation_id=regulation_id,
                error=exc,
            )
            return await self._mark_start_failed_if_owned(
                regulation_id=regulation_id,
                user_id=user_id,
                expected_lock_version=expected_lock_version,
            )

        try:
            async with self.uow:
                locked = await self.repository.find_by_id_and_user_for_update(
                    regulation_id=regulation_id,
                    user_id=user_id,
                )
                if locked is None:
                    raise BusinessException(REGULATION_NOT_FOUND, "regulation not found")
                if not self._owns_start_fence(
                    locked,
                    expected_lock_version=expected_lock_version,
                ):
                    raise BusinessException(
                        REGULATION_STATUS_INVALID,
                        "regulation parse execution was superseded",
                    )
                locked.parse_task_id = task_id
            return locked
        except BusinessException:
            raise
        except Exception:
            await self._mark_start_failed_if_owned(
                regulation_id=regulation_id,
                user_id=user_id,
                expected_lock_version=expected_lock_version,
            )
            raise

    async def _mark_start_failed_if_owned(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
        expected_lock_version: int,
    ) -> Regulation:
        """只有创建 MinerU 任务的当前版本可以写入解析失败。"""
        async with self.uow:
            regulation = await self.repository.find_by_id_and_user_for_update(
                regulation_id=regulation_id,
                user_id=user_id,
            )
            if regulation is None:
                raise BusinessException(REGULATION_NOT_FOUND, "regulation not found")
            if self._owns_start_fence(
                regulation,
                expected_lock_version=expected_lock_version,
            ):
                regulation.status = RegulationStatus.FAILED
                regulation.parse_error = REGULATION_FAILURE_CODES["parse"]
                regulation.parse_completed_at = utc_now()
            return regulation

    @staticmethod
    def _owns_start_fence(
        regulation: Regulation,
        *,
        expected_lock_version: int,
    ) -> bool:
        return (
            regulation.lock_version == expected_lock_version
            and regulation.status == RegulationStatus.PARSING
        )

    async def sync_parse_result(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
    ) -> Regulation:
        """同步 MinerU 结果；并发保护由最外层法规流水线总锁统一负责。"""
        return await self._sync_parse_result(
            regulation_id=regulation_id,
            user_id=user_id,
        )

    async def queue_sync_parse(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
    ) -> tuple[Regulation, bool]:
        """校验同步条件并返回是否需要安排后台处理。"""
        async with self.uow:
            regulation = await self.repository.find_by_id_and_user(
                regulation_id=regulation_id,
                user_id=user_id,
            )
            if regulation is None:
                raise BusinessException(
                    REGULATION_NOT_FOUND,
                    "regulation not found",
                )
            # 已完成时保持接口幂等，不再创建无意义的后台任务。
            if regulation.status == RegulationStatus.READY:
                return regulation, False
            if regulation.status == RegulationStatus.PARSING and regulation.parse_task_id:
                return regulation, True
            raise BusinessException(
                REGULATION_STATUS_INVALID,
                "regulation is not parsing",
            )

    async def _sync_parse_result(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
    ) -> Regulation:
        """Compatibility seam delegating fenced synchronization to its component."""
        return await self.result_service.sync(
            regulation_id=regulation_id,
            user_id=user_id,
            archive_builder=self._build_parse_blocks_from_archive,
        )

    async def _build_parse_blocks_from_archive(
        self,
        *,
        regulation_id: UUID,
        parse_task_id: str,
        archive_file: BinaryIO,
    ) -> list[RegulationParseBlock]:
        """Compatibility seam for focused archive tests and result synchronization."""
        return await self.archive_service.build(
            regulation_id=regulation_id,
            parse_task_id=parse_task_id,
            archive_file=archive_file,
        )


def get_regulation_parse_service(
    uow: UnitOfWork = Depends(get_uow),
) -> RegulationParseService:
    return RegulationParseService(
        uow=uow,
        repository=RegulationRepository(uow.session),
        parse_block_repository=RegulationParseBlockRepository(uow.session),
        storage=RegulationStorageService(),
        mineru=mineru_client,
        visual_analyzer=get_regulation_visual_analyzer(),
    )


async def run_regulation_parse_sync(
    *,
    regulation_id: UUID,
    user_id: UUID,
) -> None:
    """使用独立 Session 在响应返回后同步 MinerU 结果。"""
    with bound_contextvars(user_id=str(user_id)):
        async with acquire_regulation_pipeline_lease(regulation_id) as acquired:
            if not acquired:
                return
            async with async_session_factory() as session:
                service = RegulationParseService(
                    uow=UnitOfWork(session),
                    repository=RegulationRepository(session),
                    parse_block_repository=RegulationParseBlockRepository(session),
                    storage=RegulationStorageService(),
                    mineru=mineru_client,
                    visual_analyzer=get_regulation_visual_analyzer(),
                )
                try:
                    await run_with_lease_guard(
                        acquired,
                        service.sync_parse_result(
                            regulation_id=regulation_id,
                            user_id=user_id,
                        ),
                    )
                except Exception as exc:
                    # HTTP 已经返回 202。Service 负责持久化可处理的失败状态，
                    # 后台入口只记录未被业务流程吸收的异常。
                    log_regulation_failure(
                        "regulation_parse_background_sync_failed",
                        regulation_id=regulation_id,
                        error=exc,
                    )
