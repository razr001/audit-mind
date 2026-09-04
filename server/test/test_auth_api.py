from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import auth as auth_api
from app.core import security
from app.core.config import Settings, get_settings
from app.core.security import get_jwt_user
from app.main import create_app
from app.schemas.auth import CurrentUser


def make_app(
    *,
    environment: str = "local",
    origins: list[str] | None = None,
):
    settings = SimpleNamespace(
        APP_NAME="AuditMind Test",
        CORS_ALLOWED_ORIGINS=origins or [],
    )
    application = create_app(settings=settings)
    application.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        ENVIRONMENT=environment,
        AUTH_REFRESH_COOKIE_NAME="auditmind-refresh-token",
        AUTH_REFRESH_COOKIE_PATH="/api/auth",
        AUTH_COOKIE_SECURE=False,
        AUTH_COOKIE_SAMESITE="lax",
    )
    return application


def test_me_returns_authenticated_user() -> None:
    application = make_app()
    application.dependency_overrides[get_jwt_user] = lambda: CurrentUser(
        user_id=UUID("9efa0f2d-7e1f-4204-8d0e-f254e36c8e59"),
        username="admin",
    )

    response = TestClient(application).get("/auth/me")

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "success",
        "data": {
            "userId": "9efa0f2d-7e1f-4204-8d0e-f254e36c8e59",
            "username": "admin",
        },
    }


def test_create_token_is_available_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = make_app(environment="local")
    monkeypatch.setattr(auth_api, "create_token", lambda *_: "local-token")

    response = TestClient(application).post("/auth/create-token")

    assert response.status_code == 200
    assert response.json()["data"] == "local-token"


def test_create_token_is_hidden_outside_development() -> None:
    application = make_app(environment="production")

    response = TestClient(application).post("/auth/create-token")

    assert response.status_code == 404
    assert response.json()["message"] == "Not found"


def test_environment_defaults_to_production() -> None:
    assert Settings.model_fields["ENVIRONMENT"].default == "production"


def test_me_rejects_missing_token_with_bearer_challenge() -> None:
    response = TestClient(make_app()).get("/auth/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_accepts_a_real_signed_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jwt_settings = SimpleNamespace(
        JWT_SECRET_KEY=SecretStr("test-secret-that-is-not-used-in-production"),
        JWT_ALGORITHM="HS256",
        JWT_ISSUER="audit-mind-test",
        JWT_AUDIENCE="audit-mind-api-test",
        JWT_EXPIRATION_DELTA=30,
    )
    monkeypatch.setattr(security, "get_settings", lambda: jwt_settings)
    token = security.create_token(
        UUID("9efa0f2d-7e1f-4204-8d0e-f254e36c8e59"),
        "admin",
    )

    response = TestClient(make_app()).get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["username"] == "admin"


def test_me_rejects_a_signed_token_with_an_excessive_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    jwt_settings = SimpleNamespace(
        JWT_SECRET_KEY=SecretStr("test-secret-that-is-not-used-in-production"),
        JWT_ALGORITHM="HS256",
        JWT_ISSUER="audit-mind-test",
        JWT_AUDIENCE="audit-mind-api-test",
        JWT_EXPIRATION_DELTA=30,
    )
    monkeypatch.setattr(security, "get_settings", lambda: jwt_settings)
    token = jwt.encode(
        {
            "sub": "9efa0f2d-7e1f-4204-8d0e-f254e36c8e59",
            "username": "admin",
            "iat": now,
            "exp": now + timedelta(days=20),
            "iss": jwt_settings.JWT_ISSUER,
            "aud": jwt_settings.JWT_AUDIENCE,
        },
        jwt_settings.JWT_SECRET_KEY.get_secret_value(),
        algorithm=jwt_settings.JWT_ALGORITHM,
    )

    response = TestClient(make_app()).get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_cors_allows_only_configured_origin() -> None:
    application = make_app(origins=["https://audit.example.com"])
    client = TestClient(application)
    headers = {
        "Origin": "https://audit.example.com",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization",
    }

    allowed = client.options("/auth/me", headers=headers)
    denied = client.options(
        "/auth/me",
        headers={**headers, "Origin": "https://attacker.example.com"},
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://audit.example.com"
    assert "access-control-allow-origin" not in denied.headers


def test_cors_origin_configuration_is_normalized() -> None:
    assert Settings.validate_cors_allowed_origins(
        [" https://audit.example.com/ ", "https://audit.example.com"]
    ) == ["https://audit.example.com"]


@pytest.mark.parametrize(
    "origin",
    ["*", "https://audit.example.com/api", "ftp://audit.example.com"],
)
def test_cors_origin_configuration_rejects_unsafe_values(
    origin: str,
) -> None:
    with pytest.raises(ValueError):
        Settings.validate_cors_allowed_origins([origin])
