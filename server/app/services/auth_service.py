from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.passwords import verify_password
from app.core.security import create_refresh_token, create_token, decode_and_verify_jwt
from app.infrastructure.db.unit_of_work import UnitOfWork
from app.infrastructure.refresh_token_store import RefreshTokenStore
from app.repositories.user_repository import UserRepository
from app.schemas.auth import LoginRequest, TokenResponse


@dataclass(frozen=True)
class IssuedTokens:
    response: TokenResponse
    refresh_token: str
    refresh_expires_at: datetime


class AuthService:
    def __init__(
        self,
        *,
        uow: UnitOfWork,
        repository: UserRepository,
        refresh_store: RefreshTokenStore,
        settings: Settings,
    ) -> None:
        self.uow = uow
        self.repository = repository
        self.refresh_store = refresh_store
        self.settings = settings

    async def login(self, request: LoginRequest) -> IssuedTokens:
        async with self.uow:
            user = await self.repository.find_by_username(request.username.strip())

        # Argon2 校验属于 CPU 工作，不应在持有数据库事务时执行。
        password_valid = await run_in_threadpool(
            verify_password,
            request.password,
            user.password_hash if user is not None else None,
        )
        if user is None or not password_valid:
            raise self._invalid_credentials()

        expires_at = datetime.now(timezone.utc) + timedelta(
            days=self.settings.JWT_REFRESH_EXPIRATION_DAYS
        )
        session_id = uuid4()
        refresh_token = create_refresh_token(
            user.id,
            user.username,
            session_id,
            expires_at=expires_at,
        )
        await self.refresh_store.store(
            session_id=session_id,
            user_id=user.id,
            token=refresh_token,
            expires_at=expires_at,
        )
        return self._issued_tokens(user.id, user.username, refresh_token, expires_at)

    async def refresh(self, refresh_token: str) -> IssuedTokens:
        try:
            payload = decode_and_verify_jwt(refresh_token, expected_type="refresh")
            user_id = UUID(payload["sub"])
            session_id = UUID(payload["sid"])
        except (HTTPException, KeyError, TypeError, ValueError) as exc:
            raise self._invalid_refresh_token() from exc

        async with self.uow:
            user = await self.repository.find_by_id(user_id)
        if user is None:
            raise self._invalid_refresh_token()

        expires_at = datetime.now(timezone.utc) + timedelta(
            days=self.settings.JWT_REFRESH_EXPIRATION_DAYS
        )
        rotated_token = create_refresh_token(
            user.id,
            user.username,
            session_id,
            expires_at=expires_at,
        )
        rotated = await self.refresh_store.rotate(
            session_id=session_id,
            user_id=user.id,
            current_token=refresh_token,
            rotated_token=rotated_token,
            expires_at=expires_at,
        )
        if not rotated:
            raise self._invalid_refresh_token()

        return self._issued_tokens(
            user.id,
            user.username,
            rotated_token,
            expires_at,
        )

    async def logout(self, refresh_token: str | None) -> None:
        if not refresh_token:
            return
        try:
            payload = decode_and_verify_jwt(refresh_token, expected_type="refresh")
            user_id = UUID(payload["sub"])
            session_id = UUID(payload["sid"])
        except (HTTPException, KeyError, TypeError, ValueError):
            return
        await self.refresh_store.revoke(
            session_id=session_id,
            user_id=user_id,
            token=refresh_token,
        )

    def _issued_tokens(
        self,
        user_id: UUID,
        username: str,
        refresh_token: str,
        refresh_expires_at: datetime,
    ) -> IssuedTokens:
        return IssuedTokens(
            response=TokenResponse(
                access_token=create_token(user_id, username),
                expires_in=self.settings.JWT_EXPIRATION_DELTA * 60,
            ),
            refresh_token=refresh_token,
            refresh_expires_at=refresh_expires_at,
        )

    @staticmethod
    def _invalid_credentials() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    @staticmethod
    def _invalid_refresh_token() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
