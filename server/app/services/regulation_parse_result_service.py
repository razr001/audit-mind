import tempfile
from collections.abc import Awaitable, Callable
from typing import BinaryIO, cast
from uuid import UUID

import aiohttp

from app.core.config import get_settings
from app.core.error_codes import REGULATION_NOT_FOUND, REGULATION_STATUS_INVALID
from app.core.exceptions import BusinessException
from app.core.language_detection import (
    default_language_for_jurisdiction,
    detect_content_language,
)
from app.core.logger import logger
from app.core.regulation_failure import REGULATION_FAILURE_CODES, log_regulation_failure
from app.infrastructure.db.unit_of_work import UnitOfWork
from app.infrastructure.mineru_client import MinerUClient, MinerUTransientError
from app.models import RegulationParseBlock
from app.models.regulation import Regulation, RegulationStatus
from app.repositories.regulation_parse_block_repository import (
    RegulationParseBlockRepository,
)
from app.repositories.regulation_repository import RegulationRepository
from app.unit.date import utc_now

settings = get_settings()
ArchiveBuilder = Callable[..., Awaitable[list[RegulationParseBlock]]]


class RegulationParseResultService:
    """Synchronize MinerU state without holding transactions across network I/O."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        repository: RegulationRepository,
        parse_block_repository: RegulationParseBlockRepository,
        mineru: MinerUClient,
    ) -> None:
        self.uow = uow
        self.repository = repository
        self.parse_block_repository = parse_block_repository
        self.mineru = mineru

    async def sync(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
        archive_builder: ArchiveBuilder,
    ) -> Regulation:
        """Apply one fenced, idempotent synchronization attempt."""
        regulation, lock_version, task_id = await self._claim(
            regulation_id=regulation_id,
            user_id=user_id,
        )
        if task_id is None or lock_version is None:
            return regulation

        try:
            task = await self.mineru.get_task(task_id)
        except (TimeoutError, aiohttp.ClientError, MinerUTransientError) as exc:
            logger.warning(
                "regulation_mineru_task_query_transient_failure",
                regulation_id=str(regulation_id),
                error_type=type(exc).__name__,
            )
            return regulation
        except Exception as exc:
            log_regulation_failure(
                "regulation_mineru_task_query_failed",
                regulation_id=regulation_id,
                error=exc,
            )
            failed = await self._mark_failed_if_owned(
                regulation_id=regulation_id,
                user_id=user_id,
                lock_version=lock_version,
                task_id=task_id,
                require_record=True,
            )
            assert failed is not None
            return failed

        mineru_status = task.get("status")
        if mineru_status in {"pending", "processing"}:
            return regulation
        if mineru_status != "completed":
            event = (
                "regulation_mineru_task_failed"
                if mineru_status == "failed"
                else "regulation_mineru_unknown_task_status"
            )
            error = "MinerUTaskFailed" if mineru_status == "failed" else "UnknownMinerUTaskStatus"
            log_regulation_failure(event, regulation_id=regulation_id, error=error)
            failed = await self._mark_failed_if_owned(
                regulation_id=regulation_id,
                user_id=user_id,
                lock_version=lock_version,
                task_id=task_id,
                require_record=mineru_status == "failed",
            )
            return failed or regulation

        try:
            parse_blocks = await self._download_blocks(
                regulation_id=regulation_id,
                task_id=task_id,
                archive_builder=archive_builder,
            )
        except (TimeoutError, aiohttp.ClientError, MinerUTransientError) as exc:
            logger.warning(
                "regulation_mineru_result_download_transient_failure",
                regulation_id=str(regulation_id),
                error_type=type(exc).__name__,
            )
            return regulation
        except Exception as exc:
            log_regulation_failure(
                "regulation_mineru_result_processing_failed",
                regulation_id=regulation_id,
                error=exc,
            )
            failed = await self._mark_failed_if_owned(
                regulation_id=regulation_id,
                user_id=user_id,
                lock_version=lock_version,
                task_id=task_id,
                require_record=True,
            )
            assert failed is not None
            return failed

        return await self._commit_blocks(
            regulation_id=regulation_id,
            user_id=user_id,
            lock_version=lock_version,
            task_id=task_id,
            parse_blocks=parse_blocks,
        )

    async def _claim(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
    ) -> tuple[Regulation, int | None, str | None]:
        async with self.uow:
            regulation = await self.repository.find_by_id_and_user_for_update(
                regulation_id=regulation_id,
                user_id=user_id,
            )
            if regulation is None:
                raise BusinessException(REGULATION_NOT_FOUND, "regulation not found")
            if regulation.status == RegulationStatus.READY:
                return regulation, None, None
            if regulation.status != RegulationStatus.PARSING or not regulation.parse_task_id:
                raise BusinessException(REGULATION_STATUS_INVALID, "regulation is not parsing")

            regulation.lock_version += 1
            return regulation, regulation.lock_version, regulation.parse_task_id

    async def _mark_failed_if_owned(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
        lock_version: int,
        task_id: str,
        require_record: bool,
    ) -> Regulation | None:
        async with self.uow:
            regulation = await self.repository.find_by_id_and_user_for_update(
                regulation_id=regulation_id,
                user_id=user_id,
            )
            if regulation is None:
                if require_record:
                    raise BusinessException(REGULATION_NOT_FOUND, "regulation not found")
                return None
            if self._owns_fence(regulation, lock_version=lock_version, task_id=task_id):
                regulation.status = RegulationStatus.FAILED
                regulation.parse_error = REGULATION_FAILURE_CODES["parse"]
                regulation.parse_completed_at = utc_now()
            return regulation

    async def _download_blocks(
        self,
        *,
        regulation_id: UUID,
        task_id: str,
        archive_builder: ArchiveBuilder,
    ) -> list[RegulationParseBlock]:
        with tempfile.TemporaryFile(mode="w+b") as archive_file:
            # typeshed 在 Windows 上把 TemporaryFile 标成内部包装类型；它在
            # 运行时完整实现 BinaryIO，这里只收窄第三方类型声明。
            archive_stream = cast(BinaryIO, archive_file)
            await self.mineru.download_task_result_zip(
                task_id=task_id,
                destination=archive_stream,
                max_bytes=settings.MINERU_MAX_RESULT_ARCHIVE_SIZE,
            )
            blocks = await archive_builder(
                regulation_id=regulation_id,
                parse_task_id=task_id,
                archive_file=archive_stream,
            )
        if not blocks:
            raise RuntimeError("MinerU completed without parseable content")
        return blocks

    async def _commit_blocks(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
        lock_version: int,
        task_id: str,
        parse_blocks: list[RegulationParseBlock],
    ) -> Regulation:
        async with self.uow:
            regulation = await self.repository.find_by_id_and_user_for_update(
                regulation_id=regulation_id,
                user_id=user_id,
            )
            if regulation is None:
                raise BusinessException(REGULATION_NOT_FOUND, "regulation not found")
            if regulation.status == RegulationStatus.READY:
                return regulation
            if not self._owns_fence(
                regulation,
                lock_version=lock_version,
                task_id=task_id,
            ):
                return regulation

            await self.parse_block_repository.replace_by_regulation(
                regulation_id=regulation_id,
                blocks=parse_blocks,
            )
            # 上传阶段尚无正文；MinerU 完成后再从真实内容识别，确保后续规则
            # Profile、索引过滤和展示使用的不是前端猜测值。
            if regulation.language.lower() == "auto":
                regulation.language = detect_content_language(
                    (block.content for block in parse_blocks),
                    fallback=default_language_for_jurisdiction(regulation.jurisdiction),
                )
            regulation.status = RegulationStatus.READY
            regulation.parse_error = None
            regulation.parse_completed_at = utc_now()
            return regulation

    @staticmethod
    def _owns_fence(
        regulation: Regulation,
        *,
        lock_version: int,
        task_id: str,
    ) -> bool:
        return (
            regulation.lock_version == lock_version
            and regulation.status == RegulationStatus.PARSING
            and regulation.parse_task_id == task_id
        )
