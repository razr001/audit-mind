from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi import Response as HttpResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import create_token, get_jwt_user
from app.infrastructure.db.session import get_db
from app.infrastructure.db.unit_of_work import UnitOfWork, get_uow
from app.infrastructure.refresh_token_store import refresh_token_store
from app.repositories.user_repository import UserRepository
from app.schemas.auth import CurrentUser, LoginRequest, TokenResponse
from app.schemas.response import Response
from app.services.auth_service import AuthService, IssuedTokens

router = APIRouter(
    prefix="/auth",
    tags=["auth"],
)

def get_auth_service(
    session: AsyncSession = Depends(get_db),
    uow: UnitOfWork = Depends(get_uow),
    settings: Settings = Depends(get_settings),
) -> AuthService:
    return AuthService(
        uow=uow,
        repository=UserRepository(session),
        refresh_store=refresh_token_store,
        settings=settings,
    )


def set_refresh_cookie(
    response: HttpResponse,
    issued: IssuedTokens,
    settings: Settings,
) -> None:
    response.set_cookie(
        key=settings.AUTH_REFRESH_COOKIE_NAME,
        value=issued.refresh_token,
        expires=issued.refresh_expires_at,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite=settings.AUTH_COOKIE_SAMESITE,
        path=settings.AUTH_REFRESH_COOKIE_PATH,
    )


@router.post("/login", response_model=Response[TokenResponse])
async def login(
    request: LoginRequest,
    response: HttpResponse,
    service: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> Response[TokenResponse]:
    issued = await service.login(request)
    set_refresh_cookie(response, issued, settings)
    return Response[TokenResponse](data=issued.response)


@router.post("/refresh", response_model=Response[TokenResponse])
async def refresh_access_token(
    request: Request,
    response: HttpResponse,
    service: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> Response[TokenResponse]:
    refresh_token = request.cookies.get(settings.AUTH_REFRESH_COOKIE_NAME)
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Missing refresh token")
    issued = await service.refresh(refresh_token)
    set_refresh_cookie(response, issued, settings)
    return Response[TokenResponse](data=issued.response)


@router.post("/logout", response_model=Response[None])
async def logout(
    request: Request,
    response: HttpResponse,
    service: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> Response[None]:
    refresh_token = request.cookies.get(settings.AUTH_REFRESH_COOKIE_NAME)
    await service.logout(refresh_token)
    response.delete_cookie(
        settings.AUTH_REFRESH_COOKIE_NAME,
        path=settings.AUTH_REFRESH_COOKIE_PATH,
        secure=settings.AUTH_COOKIE_SECURE,
        httponly=True,
        samesite=settings.AUTH_COOKIE_SAMESITE,
    )
    return Response[None](data=None)


@router.post(
    "/create-token",
    response_model=Response[str],
    include_in_schema=False,
)
async def create_development_token(
    settings: Settings = Depends(get_settings),
):
    """仅在本地开发环境为固定测试管理员生成短期 Token。"""
    if settings.ENVIRONMENT.lower() not in {
        "local",
        "dev",
        "development",
    }:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    token = create_token(UUID("9efa0f2d-7e1f-4204-8d0e-f254e36c8e59"), "admin")
    return Response[str](
        data=token,
    )


@router.get(
    "/me",
    response_model=Response[CurrentUser],
)
async def get_current_user(
    current_user: CurrentUser = Depends(get_jwt_user),
) -> Response[CurrentUser]:
    """返回 JWT 校验后的当前用户，不暴露原始令牌载荷。"""
    return Response[CurrentUser](data=current_user)
