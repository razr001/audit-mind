from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import logger
from app.core.request_context import bind_current_user, get_request_user
from app.infrastructure.db.session import get_db
from app.infrastructure.db.unit_of_work import UnitOfWork, get_uow
from app.infrastructure.refresh_token_store import refresh_token_store
from app.repositories.user_repository import UserRepository
from app.schemas.response import Response
from app.schemas.user import UserCreateRequest, UserResponse
from app.services.user_management_service import UserManagementService

router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(bind_current_user)],
)


def get_user_management_service(
    session: AsyncSession = Depends(get_db),
    uow: UnitOfWork = Depends(get_uow),
) -> UserManagementService:
    return UserManagementService(
        uow=uow,
        repository=UserRepository(session),
        refresh_store=refresh_token_store,
    )


@router.get("", response_model=Response[list[UserResponse]])
async def list_users(
    service: UserManagementService = Depends(get_user_management_service),
) -> Response[list[UserResponse]]:
    users = await service.list_users()
    return Response[list[UserResponse]](
        data=[UserResponse.model_validate(user) for user in users]
    )


@router.post(
    "",
    response_model=Response[UserResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    request: UserCreateRequest,
    service: UserManagementService = Depends(get_user_management_service),
) -> Response[UserResponse]:
    user = await service.create(request.username, request.password)
    current_user = get_request_user()
    logger.info(
        "user.management.created",
        actor_user_id=str(current_user.user_id),
        target_user_id=str(user.id),
    )
    return Response[UserResponse](data=UserResponse.model_validate(user))


@router.delete("/{user_id}", response_model=Response[None])
async def delete_user(
    user_id: UUID,
    service: UserManagementService = Depends(get_user_management_service),
) -> Response[None]:
    current_user = get_request_user()
    await service.delete_by_id(user_id, actor_user_id=current_user.user_id)
    logger.info(
        "user.management.deleted",
        actor_user_id=str(current_user.user_id),
        target_user_id=str(user_id),
    )
    return Response[None](data=None)
