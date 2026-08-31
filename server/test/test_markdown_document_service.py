import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.exceptions import BusinessException
from app.models.document import DocumentSourceType
from app.services.document_service import DocumentService


class FakeUow:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False


def make_service(*, max_bytes: int = 1024):
    repository = SimpleNamespace(save=AsyncMock(side_effect=lambda document: document))
    storage = SimpleNamespace(
        upload_bytes=AsyncMock(return_value="documents/source.md"),
        remove=AsyncMock(),
    )
    service = DocumentService(None, FakeUow(), repository, storage)  # type: ignore[arg-type]
    return service, repository, storage


def test_create_markdown_document_normalizes_and_uploads_utf8(monkeypatch) -> None:
    service, repository, storage = make_service()
    monkeypatch.setattr(
        "app.services.document_service.get_settings",
        lambda: SimpleNamespace(AUDIT_MARKDOWN_MAX_BYTES=1024),
    )

    document = asyncio.run(
        service.create_markdown_document(
            title="隐私政策",
            content="\ufeff# 标题\r\n\r\n正文",
            user_id=uuid4(),
        )
    )

    assert document.source_type == DocumentSourceType.MARKDOWN
    assert document.original_filename == "隐私政策.md"
    assert document.file_size == len("# 标题\n\n正文".encode("utf-8"))
    assert storage.upload_bytes.await_args.kwargs["content"] == "# 标题\n\n正文".encode("utf-8")
    repository.save.assert_awaited_once_with(document)


def test_create_markdown_document_rejects_utf8_payload_over_limit(monkeypatch) -> None:
    service, repository, storage = make_service()
    monkeypatch.setattr(
        "app.services.document_service.get_settings",
        lambda: SimpleNamespace(AUDIT_MARKDOWN_MAX_BYTES=5),
    )

    with pytest.raises(BusinessException) as captured:
        asyncio.run(
            service.create_markdown_document(
                title="规则",
                content="中文",
                user_id=uuid4(),
            )
        )

    assert captured.value.code == 41301
    storage.upload_bytes.assert_not_awaited()
    repository.save.assert_not_awaited()
