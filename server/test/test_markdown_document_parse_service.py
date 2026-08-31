import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from app.models.document import DocumentSourceType, DocumentStatus
from app.services.markdown_document_parse_service import MarkdownDocumentParseService


class FakeUow:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False


def make_storage(content: bytes):
    async def stream(_object_name: str):
        yield content[:4]
        yield content[4:]

    return SimpleNamespace(stream=stream)


def test_markdown_parse_service_builds_units_without_mineru() -> None:
    document = SimpleNamespace(
        id=uuid4(),
        storage_key="documents/source.md",
        source_type=DocumentSourceType.MARKDOWN,
        status=DocumentStatus.PARSING,
        lock_version=1,
        parse_error=None,
        parse_completed_at=None,
    )
    repository = SimpleNamespace(
        claim_for_parse=AsyncMock(return_value=document),
        find_by_id_and_user_for_update=AsyncMock(return_value=document),
    )
    page_repository = SimpleNamespace(replace_by_document=AsyncMock())
    block_repository = SimpleNamespace(replace_by_document=AsyncMock())
    service = MarkdownDocumentParseService(
        uow=FakeUow(),  # type: ignore[arg-type]
        repository=repository,
        storage=make_storage("# 标题\n\n正文".encode()),  # type: ignore[arg-type]
        page_repository=page_repository,
        parse_block_repository=block_repository,
    )

    result = asyncio.run(service.parse(document_id=document.id, user_id=uuid4()))

    assert result.status == DocumentStatus.READY
    assert result.parse_error is None
    assert result.parse_completed_at is not None
    pages = page_repository.replace_by_document.await_args.kwargs["pages"]
    blocks = block_repository.replace_by_document.await_args.kwargs["blocks"]
    assert pages[0].content == "# 标题\n\n正文"
    assert [block.block_type for block in blocks] == ["heading", "paragraph"]


def test_markdown_parse_failure_is_persisted_without_deleting_source() -> None:
    document = SimpleNamespace(
        id=uuid4(),
        storage_key="documents/source.md",
        status=DocumentStatus.PARSING,
        lock_version=1,
        parse_error=None,
        parse_completed_at=None,
    )
    repository = SimpleNamespace(
        claim_for_parse=AsyncMock(return_value=document),
        find_by_id_and_user_for_update=AsyncMock(return_value=document),
    )
    storage = make_storage(b"\xff")
    service = MarkdownDocumentParseService(
        uow=FakeUow(),  # type: ignore[arg-type]
        repository=repository,
        storage=storage,  # type: ignore[arg-type]
        page_repository=SimpleNamespace(replace_by_document=AsyncMock()),
        parse_block_repository=SimpleNamespace(replace_by_document=AsyncMock()),
    )

    result = asyncio.run(service.parse(document_id=document.id, user_id=uuid4()))

    assert result.status == DocumentStatus.FAILED
    assert result.parse_error == "DOCUMENT_PARSE_FAILED"
    assert not hasattr(storage, "remove")
