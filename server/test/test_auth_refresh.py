import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.auth import get_auth_service
from app.core import security
from app.core.config import get_settings
from app.core.passwords import hash_password
from app.main import create_app
from app.models.user import User
from app.schemas.auth import TokenResponse
from app.services.auth_service import AuthService, IssuedTokens


class FakeUnitOfWork:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False


class FakeUserRepository:
    def __init__(self, user: User) -> None:
        self.user = user

    async def find_by_username(self, username: str) -> User | None:
        return self.user if username.lower() == self.user.username.lower() else None

    async def find_by_id(self, user_id: UUID) -> User | None:
        return self.user if user_id == self.user.id else None



class FakeRefreshTokenStore:
    def __init__(self) -> None:
        self.tokens: dict[UUID, str] = {}

    async def store(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        token: str,
        expires_at: datetime,
    ) -> None:
        self.tokens[session_id] = token

    async def rotate(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        current_token: str,
        rotated_token: str,
        expires_at: datetime,
    ) -> bool:
        if self.tokens.get(session_id) != current_token:
            return False
        del self.tokens[session_id]
        self.tokens[session_id] = rotated_token
        return True

    async def revoke(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        token: str,
    ) -> None:
        if self.tokens.get(session_id) == token:
            del self.tokens[session_id]


def auth_settings() -> SimpleNamespace:
    return SimpleNamespace(
        JWT_SECRET_KEY=SecretStr("test-secret-that-is-not-used-in-production"),
        JWT_ALGORITHM="HS256",
        JWT_ISSUER="audit-mind-test",
        JWT_AUDIENCE="audit-mind-api-test",
        JWT_EXPIRATION_DELTA=30,
        JWT_REFRESH_EXPIRATION_DAYS=7,
        AUTH_REFRESH_COOKIE_NAME="auditmind-refresh-token",
        AUTH_COOKIE_SECURE=False,
        AUTH_COOKIE_SAMESITE="lax",
    )


def test_login_and_refresh_rotate_refresh_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = auth_settings()
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    user = User(
        id=uuid4(),
        username="admin",
        password_hash=hash_password("correct-password"),
    )
    repository = FakeUserRepository(user)
    refresh_store = FakeRefreshTokenStore()
    service = AuthService(
        uow=FakeUnitOfWork(),
        repository=repository,  # type: ignore[arg-type]
        refresh_store=refresh_store,  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
    )

    async def scenario() -> None:
        issued = await service.login(
            SimpleNamespace(username="ADMIN", password="correct-password")
        )
        assert issued.response.access_token
        original_refresh = issued.refresh_token

        rotated = await service.refresh(original_refresh)
        assert rotated.refresh_token != original_refresh
        assert rotated.refresh_token in refresh_store.tokens.values()

        with pytest.raises(HTTPException) as rejected:
            await service.refresh(original_refresh)
        assert rejected.value.status_code == 401

    asyncio.run(scenario())


def test_login_sets_http_only_refresh_cookie() -> None:
    settings = auth_settings()
    app = create_app(
        settings=SimpleNamespace(
            APP_NAME="AuditMind Test",
            CORS_ALLOWED_ORIGINS=[],
            ENVIRONMENT="local",
        )
    )
    app.dependency_overrides[get_settings] = lambda: settings

    class FakeAuthService:
        async def login(self, _request: object) -> IssuedTokens:
            return IssuedTokens(
                response=TokenResponse(access_token="access", expires_in=1800),
                refresh_token="refresh",
                refresh_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            )

    app.dependency_overrides[get_auth_service] = FakeAuthService
    response = TestClient(app).post(
        "/auth/login",
        json={"username": "admin", "password": "correct-password"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["accessToken"] == "access"
    cookie = response.headers["set-cookie"]
    assert "auditmind-refresh-token=refresh" in cookie
    assert "HttpOnly" in cookie
    assert "Path=/" in cookie
