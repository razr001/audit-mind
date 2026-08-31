import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api import regulation as regulation_api
from app.core.error_codes import INVALID_REGULATION_PAGE, REGULATION_NOT_FOUND
from app.core.exceptions import BusinessException
from app.core.security import get_jwt_user
from app.main import create_app
from app.models.regulation import (
    KnowledgeCategory,
    KnowledgeVisibility,
    Regulation,
    RegulationChunkStatus,
    RegulationIndexStatus,
    RegulationRuleStatus,
    RegulationSourceType,
    RegulationStatus,
)
from app.models.regulation_parse_block import RegulationParseBlock
from app.repositories.regulation_parse_block_repository import (
    RegulationParseBlockRepository,
)
from app.repositories.regulation_repository import RegulationRepository
from app.schemas.auth import CurrentUser
from app.schemas.regulation import RegulationSourceDownloadResponse
from app.services.regulation_asset_service import (
    RegulationAssetService,
    get_regulation_asset_service,
)
from app.services.regulation_detail_service import RegulationDetailService

USER_ID = UUID("9efa0f2d-7e1f-4204-8d0e-f254e36c8e59")


class FakeUnitOfWork:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False


def test_detail_service_returns_accessible_regulation_and_page_count() -> None:
    regulation_id = uuid4()
    regulation = SimpleNamespace(id=regulation_id)
    regulation_repository = SimpleNamespace(
        find_accessible_by_id=AsyncMock(return_value=regulation),
    )
    block_repository = SimpleNamespace(
        find_max_page_number=AsyncMock(return_value=18),
    )
    service = RegulationDetailService(
        uow=FakeUnitOfWork(),
        regulation_repository=regulation_repository,
        block_repository=block_repository,
    )

    result = asyncio.run(
        service.get_accessible_detail(
            regulation_id=regulation_id,
            user_id=USER_ID,
        )
    )

    assert result == (regulation, 18)
    regulation_repository.find_accessible_by_id.assert_awaited_once_with(
        regulation_id=regulation_id,
        user_id=USER_ID,
    )
    block_repository.find_max_page_number.assert_awaited_once_with(regulation_id)


def test_detail_service_hides_inaccessible_regulation_before_page_lookup() -> None:
    regulation_id = uuid4()
    block_repository = SimpleNamespace(find_max_page_number=AsyncMock())
    service = RegulationDetailService(
        uow=FakeUnitOfWork(),
        regulation_repository=SimpleNamespace(
            find_accessible_by_id=AsyncMock(return_value=None),
        ),
        block_repository=block_repository,
    )

    with pytest.raises(BusinessException) as captured:
        asyncio.run(
            service.get_accessible_detail(
                regulation_id=regulation_id,
                user_id=USER_ID,
            )
        )

    assert captured.value.code == REGULATION_NOT_FOUND
    block_repository.find_max_page_number.assert_not_awaited()


def test_source_url_checks_access_and_returns_reader_metadata() -> None:
    regulation_id = uuid4()
    regulation = SimpleNamespace(
        storage_key=f"regulations/{regulation_id}.pdf",
        original_filename="网络交易监督管理办法.pdf",
        content_type="application/pdf",
    )
    regulation_repository = SimpleNamespace(
        find_accessible_by_id=AsyncMock(return_value=regulation),
    )
    block_repository = SimpleNamespace(
        find_max_page_number=AsyncMock(return_value=18),
    )
    storage = SimpleNamespace(
        create_source_download_url=AsyncMock(return_value="https://storage.example.com/source"),
    )
    service = RegulationAssetService(
        uow=FakeUnitOfWork(),
        regulation_repository=regulation_repository,
        block_repository=block_repository,
        storage=storage,
    )

    result = asyncio.run(
        service.create_source_download_url(
            regulation_id=regulation_id,
            user_id=USER_ID,
        )
    )

    assert result == RegulationSourceDownloadResponse(
        regulation_id=regulation_id,
        url="https://storage.example.com/source",
        expires_in=600,
        original_filename="网络交易监督管理办法.pdf",
        content_type="application/pdf",
        page_count=18,
    )
    regulation_repository.find_accessible_by_id.assert_awaited_once_with(
        regulation_id=regulation_id,
        user_id=USER_ID,
    )
    block_repository.find_max_page_number.assert_awaited_once_with(regulation_id)
    storage.create_source_download_url.assert_awaited_once_with(
        object_name=regulation.storage_key,
        expires_in=600,
    )


def test_source_url_hides_private_regulation_existence() -> None:
    regulation_id = uuid4()
    storage = SimpleNamespace(create_source_download_url=AsyncMock())
    service = RegulationAssetService(
        uow=FakeUnitOfWork(),
        regulation_repository=SimpleNamespace(
            find_accessible_by_id=AsyncMock(return_value=None),
        ),
        block_repository=SimpleNamespace(
            find_max_page_number=AsyncMock(),
        ),
        storage=storage,
    )

    with pytest.raises(BusinessException) as captured:
        asyncio.run(
            service.create_source_download_url(
                regulation_id=regulation_id,
                user_id=USER_ID,
            )
        )

    assert captured.value.code == REGULATION_NOT_FOUND
    storage.create_source_download_url.assert_not_awaited()


def test_source_download_endpoint_precedes_dynamic_get_route() -> None:
    regulation_id = uuid4()
    result = RegulationSourceDownloadResponse(
        regulation_id=regulation_id,
        url="https://storage.example.com/source",
        expires_in=600,
        original_filename="policy.pdf",
        content_type="application/pdf",
        page_count=3,
    )
    service = SimpleNamespace(
        create_source_download_url=AsyncMock(return_value=result),
    )
    application = create_app(
        settings=SimpleNamespace(
            APP_NAME="AuditMind Test",
            CORS_ALLOWED_ORIGINS=[],
        )
    )
    application.dependency_overrides[get_jwt_user] = lambda: CurrentUser(
        user_id=USER_ID,
        username="admin",
    )
    application.dependency_overrides[get_regulation_asset_service] = lambda: service

    response = TestClient(application).get(f"/regulation/get/download-url/{regulation_id}")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "regulationId": str(regulation_id),
        "url": "https://storage.example.com/source",
        "expiresIn": 600,
        "originalFilename": "policy.pdf",
        "contentType": "application/pdf",
        "pageCount": 3,
    }
    service.create_source_download_url.assert_awaited_once_with(
        regulation_id=regulation_id,
        user_id=USER_ID,
    )


def test_page_blocks_endpoint_never_serializes_internal_asset_locations() -> None:
    regulation_id = uuid4()
    block_id = uuid4()
    service = SimpleNamespace(
        get_page_blocks=AsyncMock(
            return_value=[
                SimpleNamespace(
                    id=block_id,
                    block_index=0,
                    block_type="image",
                    content="架构图",
                    page_number=1,
                    bbox=[0, 0, 100, 100],
                    text_level=None,
                    char_start=0,
                    char_end=3,
                    block_metadata={
                        "mineru_image_path": "private/mineru/internal.png",
                        "asset": {
                            "storage_key": "regulation-assets/secret.png",
                            "content_hash": "private-content-hash",
                            "content_type": "image/png",
                            "file_size": 1024,
                        },
                        "image_caption": ["架构图"],
                    },
                )
            ]
        ),
    )
    application = create_app(
        settings=SimpleNamespace(
            APP_NAME="AuditMind Test",
            CORS_ALLOWED_ORIGINS=[],
        )
    )
    application.dependency_overrides[get_jwt_user] = lambda: CurrentUser(
        user_id=USER_ID,
        username="admin",
    )
    application.dependency_overrides[get_regulation_asset_service] = lambda: service

    response = TestClient(application).get(f"/regulation/blocks/{regulation_id}?pageNumber=1")

    assert response.status_code == 200
    raw_body = response.text
    assert "mineruImagePath" not in raw_body
    assert "private/mineru/internal.png" not in raw_body
    assert "storageKey" not in raw_body
    assert "regulation-assets/secret.png" not in raw_body
    assert "contentHash" not in raw_body
    assert "private-content-hash" not in raw_body
    service.get_page_blocks.assert_awaited_once_with(
        regulation_id=regulation_id,
        page_number=1,
        user_id=USER_ID,
    )


def test_page_blocks_service_rejects_aggregate_content_over_budget() -> None:
    regulation_id = uuid4()
    blocks = [SimpleNamespace(content="x" * 100_000, block_metadata=None) for _ in range(11)]
    block_repository = SimpleNamespace(
        find_by_regulation_and_page=AsyncMock(return_value=blocks),
    )
    service = RegulationAssetService(
        uow=FakeUnitOfWork(),
        regulation_repository=SimpleNamespace(
            find_accessible_by_id=AsyncMock(return_value=SimpleNamespace()),
        ),
        block_repository=block_repository,
        storage=SimpleNamespace(),
    )

    with pytest.raises(BusinessException) as captured:
        asyncio.run(
            service.get_page_blocks(
                regulation_id=regulation_id,
                page_number=1,
                user_id=USER_ID,
            )
        )

    assert captured.value.code == INVALID_REGULATION_PAGE
    block_repository.find_by_regulation_and_page.assert_awaited_once_with(
        regulation_id=regulation_id,
        page_number=1,
        limit=251,
    )


def test_existing_parse_sync_route_remains_bound_to_sync_handler() -> None:
    parse_route = next(
        route
        for route in regulation_api.router.routes
        if route.path == "/regulation/parse/sync/{regulation_id}" and "POST" in route.methods
    )
    source_route = next(
        route
        for route in regulation_api.router.routes
        if route.path == "/regulation/get/download-url/{regulation_id}" and "GET" in route.methods
    )

    assert parse_route.endpoint is regulation_api.sync_regulation_parse
    assert parse_route.status_code == 202
    assert source_route.endpoint is regulation_api.get_regulation_source_download_url


def test_real_repositories_enforce_access_and_page_scope() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Regulation.__table__.create(engine)
    RegulationParseBlock.__table__.create(engine)
    shared_id = uuid4()
    own_private_id = uuid4()
    other_private_id = uuid4()
    disabled_id = uuid4()
    empty_id = uuid4()
    other_user_id = uuid4()

    def regulation_row(
        regulation_id,
        *,
        visibility,
        uploaded_by,
        enabled=True,
    ):
        return {
            "id": regulation_id,
            "title": f"Regulation {regulation_id}",
            "source_type": RegulationSourceType.INTERNAL_POLICY,
            "category": KnowledgeCategory.COMPANY_RULE,
            "visibility": visibility,
            "language": "zh-CN",
            "jurisdiction": "CN",
            "storage_key": f"regulations/{regulation_id}.pdf",
            "original_filename": f"{regulation_id}.pdf",
            "content_type": "application/pdf",
            "file_size": 100,
            "content_hash": str(regulation_id).replace("-", "").ljust(64, "0"),
            "uploaded_by": uploaded_by,
            "enabled": enabled,
            "status": RegulationStatus.READY,
            "lock_version": 0,
            "chunk_status": RegulationChunkStatus.READY,
            "index_status": RegulationIndexStatus.READY,
            "rule_status": RegulationRuleStatus.READY,
        }

    def block_row(regulation_id, block_index, page_number):
        return {
            "id": uuid4(),
            "regulation_id": regulation_id,
            "block_index": block_index,
            "block_type": "text",
            "content": "content",
            "page_number": page_number,
            "char_start": block_index * 10,
            "char_end": block_index * 10 + 7,
        }

    with Session(engine) as session:
        session.execute(
            Regulation.__table__.insert(),
            [
                regulation_row(
                    shared_id,
                    visibility=KnowledgeVisibility.SHARED,
                    uploaded_by=other_user_id,
                ),
                regulation_row(
                    own_private_id,
                    visibility=KnowledgeVisibility.PRIVATE,
                    uploaded_by=USER_ID,
                ),
                regulation_row(
                    other_private_id,
                    visibility=KnowledgeVisibility.PRIVATE,
                    uploaded_by=other_user_id,
                ),
                regulation_row(
                    disabled_id,
                    visibility=KnowledgeVisibility.SHARED,
                    uploaded_by=USER_ID,
                    enabled=False,
                ),
                regulation_row(
                    empty_id,
                    visibility=KnowledgeVisibility.SHARED,
                    uploaded_by=USER_ID,
                ),
            ],
        )
        session.execute(
            RegulationParseBlock.__table__.insert(),
            [
                block_row(shared_id, 0, 1),
                block_row(shared_id, 1, 4),
                block_row(own_private_id, 0, 2),
                block_row(other_private_id, 0, 99),
                block_row(disabled_id, 0, 88),
            ],
        )
        session.commit()

        class AsyncSessionAdapter:
            async def execute(self, statement):
                return session.execute(statement)

            async def scalar(self, statement):
                return session.scalar(statement)

        adapter = AsyncSessionAdapter()
        storage = SimpleNamespace(
            create_source_download_url=AsyncMock(
                side_effect=lambda **kwargs: f"https://storage.example.com/{kwargs['object_name']}"
            )
        )
        service = RegulationAssetService(
            uow=FakeUnitOfWork(),
            regulation_repository=RegulationRepository(adapter),
            block_repository=RegulationParseBlockRepository(adapter),
            storage=storage,
        )

        shared = asyncio.run(
            service.create_source_download_url(
                regulation_id=shared_id,
                user_id=USER_ID,
            )
        )
        own_private = asyncio.run(
            service.create_source_download_url(
                regulation_id=own_private_id,
                user_id=USER_ID,
            )
        )
        empty = asyncio.run(
            service.create_source_download_url(
                regulation_id=empty_id,
                user_id=USER_ID,
            )
        )
        for hidden_id in (other_private_id, disabled_id, uuid4()):
            with pytest.raises(BusinessException) as captured:
                asyncio.run(
                    service.create_source_download_url(
                        regulation_id=hidden_id,
                        user_id=USER_ID,
                    )
                )
            assert captured.value.code == REGULATION_NOT_FOUND

    assert shared.page_count == 4
    assert own_private.page_count == 2
    assert empty.page_count == 0
    assert storage.create_source_download_url.await_count == 3
