from uuid import UUID

from fastapi import Depends

from app.core.error_codes import REGULATION_NOT_FOUND
from app.core.exceptions import BusinessException
from app.infrastructure.db.unit_of_work import UnitOfWork, get_uow
from app.models.regulation import Regulation
from app.repositories.regulation_parse_block_repository import (
    RegulationParseBlockRepository,
)
from app.repositories.regulation_repository import RegulationRepository


class RegulationDetailService:
    """Load an access-safe regulation detail and its parsed PDF metadata."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        regulation_repository: RegulationRepository,
        block_repository: RegulationParseBlockRepository,
    ) -> None:
        self.uow = uow
        self.regulation_repository = regulation_repository
        self.block_repository = block_repository

    async def get_accessible_detail(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
    ) -> tuple[Regulation, int]:
        """Return not-found for inaccessible records to prevent enumeration."""
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
            page_count = await self.block_repository.find_max_page_number(
                regulation_id,
            )
        return regulation, page_count


def get_regulation_detail_service(
    uow: UnitOfWork = Depends(get_uow),
) -> RegulationDetailService:
    return RegulationDetailService(
        uow=uow,
        regulation_repository=RegulationRepository(uow.session),
        block_repository=RegulationParseBlockRepository(uow.session),
    )
