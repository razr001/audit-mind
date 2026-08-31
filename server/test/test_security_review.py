import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.document_failure import log_document_failure
from app.core.exceptions import BusinessException
from app.core.security_headers import SECURITY_RESPONSE_HEADERS
from app.main import create_app
from app.services.document_service import DocumentService
from test.pdf_fixtures import create_test_pdf

DOCUMENT_ID = UUID("9efa0f2d-7e1f-4204-8d0e-f254e36c8e59")


def settings_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost/test",
        "REDIS_URL": "redis://localhost:6379/0",
        "MINIO_ENDPOINT": "localhost:9000",
        "MINIO_ACCESS_KEY": "test-access-key",
        "MINIO_SECRET_KEY": "test-secret-key",
        "ELASTICSEARCH_URL": "http://localhost:9200",
        "ELASTICSEARCH_API_KEY": "test-api-key",
        "JWT_SECRET_KEY": "test-only-9Gv2!qL7#sN4@wR8$kM6%zP3",
        "AI_BASE_URL": "https://ai.example.com",
        "AI_API_KEY": "test-ai-key",
        "AI_MODEL": "test-model",
    }
    values.update(overrides)
    return values


def test_jwt_configuration_rejects_weak_secrets_and_unknown_algorithms() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **settings_values(JWT_SECRET_KEY="short"))
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **settings_values(JWT_ALGORITHM="none"))
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            **settings_values(JWT_SECRET_KEY="replace-with-a-random-32-byte-secret"),
        )
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **settings_values(JWT_SECRET_KEY="x" * 32))


def test_optional_rerank_configuration_requires_explicit_provider_selection() -> None:
    disabled = Settings(
        _env_file=None,
        **settings_values(AI_RERANK_CANDIDATE_COUNT=5),
    )
    assert disabled.AI_RERANK_URL == ""

    enabled = Settings(
        _env_file=None,
        **settings_values(
            AI_RERANK_PROVIDER="bailian",
            AI_RERANK_URL="https://workspace.example.com/compatible-api/v1/reranks",
            AI_RERANK_MODEL="qwen3-rerank",
        ),
    )
    assert enabled.AI_RERANK_MODEL == "qwen3-rerank"
    assert enabled.AI_RERANK_API_KEY.get_secret_value() == ""

    # Core settings only require a provider name. The selected plugin owns its
    # endpoint, model, credential and provider-option validation rules.
    custom = Settings(
        _env_file=None,
        **settings_values(
            AI_RERANK_PROVIDER="local_plugin",
            AI_RERANK_OPTIONS={"socket_path": "local.sock"},
        ),
    )
    assert custom.AI_RERANK_PROVIDER == "local_plugin"

    with pytest.raises(ValidationError, match="AI_RERANK_PROVIDER is required"):
        Settings(
            _env_file=None,
            **settings_values(AI_RERANK_MODEL="qwen3-rerank"),
        )

    with pytest.raises(ValidationError, match="AI_RERANK_PROVIDER is required"):
        Settings(
            _env_file=None,
            **settings_values(AI_RERANK_API_KEY="orphan-key"),
        )


def test_cors_configuration_allows_internal_http_and_rejects_unsafe_origins() -> None:
    local = Settings(
        _env_file=None,
        **settings_values(
            ENVIRONMENT="local",
            CORS_ALLOWED_ORIGINS=["http://127.0.0.1:5173/"],
        ),
    )
    assert local.CORS_ALLOWED_ORIGINS == ["http://127.0.0.1:5173"]

    # API 可能只暴露给同机或内网 Nginx，因此生产环境允许 HTTP origin。
    production = Settings(
        _env_file=None,
        **settings_values(
            ENVIRONMENT="production",
            CORS_ALLOWED_ORIGINS=["http://audit.example.com"],
        ),
    )
    assert production.CORS_ALLOWED_ORIGINS == ["http://audit.example.com"]

    invalid = [
        settings_values(CORS_ALLOWED_ORIGINS=["https://user:pass@audit.example.com"]),
        settings_values(CORS_ALLOWED_ORIGINS=["https://audit.example.com/path"]),
        settings_values(CORS_ALLOWED_ORIGINS=["*"]),
    ]
    for values in invalid:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **values)

def test_production_hides_api_documentation_and_sets_security_headers() -> None:
    application = create_app(
        settings=SimpleNamespace(
            APP_NAME="AuditMind Test",
            CORS_ALLOWED_ORIGINS=[],
            ENVIRONMENT="production",
        )
    )
    response = TestClient(application).get("/openapi.json")

    assert response.status_code == 404
    for name, value in SECURITY_RESPONSE_HEADERS.items():
        assert response.headers[name] == value


def test_local_environment_keeps_openapi_available_without_cors_credentials() -> None:
    application = create_app(
        settings=SimpleNamespace(
            APP_NAME="AuditMind Test",
            CORS_ALLOWED_ORIGINS=["https://audit.example.com"],
            ENVIRONMENT="local",
        )
    )
    client = TestClient(application)

    schema = client.get("/openapi.json")
    preflight = client.options(
        "/auth/me",
        headers={
            "Access-Control-Request-Headers": "authorization",
            "Access-Control-Request-Method": "GET",
            "Origin": "https://audit.example.com",
        },
    )

    assert schema.status_code == 200
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-credentials"] == "true"


def test_document_failures_log_only_an_allow_listed_category() -> None:
    secret = "api_key=super-secret internal-url=http://private"
    with patch("app.core.document_failure.logger.error") as safe_log:
        log_document_failure(
            "document.parse.failed",
            document_id=DOCUMENT_ID,
            error=RuntimeError(secret),
        )

    safe_log.assert_called_once_with(
        "document.parse.failed",
        document_id=str(DOCUMENT_ID),
        error_type="RuntimeError",
    )
    assert secret not in repr(safe_log.call_args_list)


def test_document_upload_rejects_control_characters_in_filename() -> None:
    service = DocumentService(
        session=SimpleNamespace(),
        uow=SimpleNamespace(),
        repository=SimpleNamespace(),
        storage=SimpleNamespace(),
    )
    source = UploadFile(
        filename="unsafe\nname.pdf",
        file=io.BytesIO(b"%PDF-1.7\n"),
    )

    with pytest.raises(BusinessException, match="control characters"):
        asyncio.run(service.upload_document(source, DOCUMENT_ID))


def test_document_upload_keeps_storage_object_after_database_failure() -> None:
    class FakeUnitOfWork:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object):
            return False

    storage = SimpleNamespace(
        upload=AsyncMock(return_value="documents/private-key.pdf"),
        remove=AsyncMock(),
    )
    repository = SimpleNamespace(
        save=AsyncMock(side_effect=RuntimeError("database unavailable")),
    )
    service = DocumentService(
        session=SimpleNamespace(),
        uow=FakeUnitOfWork(),
        repository=repository,
        storage=storage,
    )
    source = UploadFile(
        filename="safe.pdf",
        file=io.BytesIO(create_test_pdf()),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(service.upload_document(source, DOCUMENT_ID))

    storage.remove.assert_not_awaited()
