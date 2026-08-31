from uuid import UUID

from fastapi import Depends

from app.core.error_codes import INVALID_REGULATION_PAGE, REGULATION_NOT_FOUND
from app.core.exceptions import BusinessException
from app.core.regulation_block_limits import (
    REGULATION_BLOCKS_PER_PAGE_LIMIT,
    REGULATION_PAGE_CONTENT_LIMIT,
    REGULATION_PAGE_METADATA_LIMIT,
)
from app.infrastructure.db.unit_of_work import UnitOfWork, get_uow
from app.models.regulation_parse_block import RegulationParseBlock
from app.repositories.regulation_parse_block_repository import (
    RegulationParseBlockRepository,
)
from app.repositories.regulation_repository import RegulationRepository
from app.schemas.regulation import (
    RegulationAssetDownloadResponse,
    RegulationSourceDownloadResponse,
)
from app.services.regulation_storage_service import RegulationStorageService


class RegulationAssetService:
    """校验法规访问范围，并为 MinerU 局部图片生成临时访问地址。"""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        regulation_repository: RegulationRepository,
        block_repository: RegulationParseBlockRepository,
        storage: RegulationStorageService,
    ) -> None:
        self.uow = uow
        self.regulation_repository = regulation_repository
        self.block_repository = block_repository
        self.storage = storage

    async def create_download_url(
        self,
        *,
        block_id: UUID,
        user_id: UUID,
    ) -> RegulationAssetDownloadResponse:
        """只允许能够访问所属法规的用户取得图片预签名地址。"""
        async with self.uow:
            block = await self.block_repository.find_by_id(block_id)
            if block is None:
                raise BusinessException(
                    REGULATION_NOT_FOUND,
                    "regulation asset not found",
                )

            regulation = await self.regulation_repository.find_accessible_by_id(
                regulation_id=block.regulation_id,
                user_id=user_id,
            )
            if regulation is None:
                # 对无权限用户也返回 not found，避免泄露私有法规及图片是否存在。
                raise BusinessException(
                    REGULATION_NOT_FOUND,
                    "regulation asset not found",
                )

            metadata = block.block_metadata
            asset = metadata.get("asset") if isinstance(metadata, dict) else None
            if not isinstance(asset, dict):
                raise BusinessException(
                    REGULATION_NOT_FOUND,
                    "regulation asset not found",
                )

            storage_key = asset.get("storage_key")
            content_type = asset.get("content_type")
            if not isinstance(storage_key, str) or not isinstance(
                content_type,
                str,
            ):
                raise BusinessException(
                    REGULATION_NOT_FOUND,
                    "regulation asset not found",
                )

        expires_in = 600
        url = await self.storage.create_asset_download_url(
            object_name=storage_key,
            expires_in=expires_in,
        )
        return RegulationAssetDownloadResponse(
            block_id=block.id,
            url=url,
            expires_in=expires_in,
            content_type=content_type,
        )

    async def create_source_download_url(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
    ) -> RegulationSourceDownloadResponse:
        """鉴权后返回法规原文件地址与当前可用的解析页数。"""
        async with self.uow:
            regulation = await self.regulation_repository.find_accessible_by_id(
                regulation_id=regulation_id,
                user_id=user_id,
            )
            if regulation is None:
                raise BusinessException(
                    REGULATION_NOT_FOUND,
                    "regulation not found",
                )
            page_count = await self.block_repository.find_max_page_number(regulation_id)
            storage_key = regulation.storage_key
            original_filename = regulation.original_filename
            content_type = regulation.content_type

        expires_in = 600
        url = await self.storage.create_source_download_url(
            object_name=storage_key,
            expires_in=expires_in,
        )
        return RegulationSourceDownloadResponse(
            regulation_id=regulation_id,
            url=url,
            expires_in=expires_in,
            original_filename=original_filename,
            content_type=content_type,
            page_count=page_count,
        )

    async def get_page_blocks(
        self,
        *,
        regulation_id: UUID,
        page_number: int,
        user_id: UUID,
    ) -> list[RegulationParseBlock]:
        """鉴权后返回指定 PDF 页的全部文本和视觉块。"""
        async with self.uow:
            regulation = await self.regulation_repository.find_accessible_by_id(
                regulation_id=regulation_id,
                user_id=user_id,
            )
            if regulation is None:
                raise BusinessException(
                    REGULATION_NOT_FOUND,
                    "regulation not found",
                )
            blocks = await self.block_repository.find_by_regulation_and_page(
                regulation_id=regulation_id,
                page_number=page_number,
                limit=REGULATION_BLOCKS_PER_PAGE_LIMIT + 1,
            )
            content_size = 0
            metadata_size = 0
            for block in blocks:
                content_size += len(block.content)
                metadata = block.block_metadata
                if isinstance(metadata, dict):
                    metadata_size += sum(len(value) for value in _iter_metadata_strings(metadata))
                if (
                    len(blocks) > REGULATION_BLOCKS_PER_PAGE_LIMIT
                    or content_size > REGULATION_PAGE_CONTENT_LIMIT
                    or metadata_size > REGULATION_PAGE_METADATA_LIMIT
                ):
                    raise BusinessException(
                        INVALID_REGULATION_PAGE,
                        "regulation page exceeds display limits",
                    )
            return blocks


def _iter_metadata_strings(value: object):
    """Yield nested strings without materializing attacker-controlled metadata."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _iter_metadata_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_metadata_strings(nested)


def get_regulation_asset_service(
    uow: UnitOfWork = Depends(get_uow),
) -> RegulationAssetService:
    return RegulationAssetService(
        uow=uow,
        regulation_repository=RegulationRepository(uow.session),
        block_repository=RegulationParseBlockRepository(uow.session),
        storage=RegulationStorageService(),
    )
