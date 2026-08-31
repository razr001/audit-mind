from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

from fastapi.testclient import TestClient

from app.core.security import get_jwt_user
from app.main import create_app
from app.models.regulation import KnowledgeCategory, RegulationSourceType
from app.schemas.auth import CurrentUser
from app.services.regulation_search_service import (
    RegulationSearchService,
    get_regulation_search_service,
)

USER_ID = UUID("9efa0f2d-7e1f-4204-8d0e-f254e36c8e59")


class FakeUnitOfWork:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False


def create_client(service):
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
    application.dependency_overrides[get_regulation_search_service] = lambda: service
    return TestClient(application)


def test_search_endpoint_passes_combined_filters_and_user_scope() -> None:
    service = SimpleNamespace(search=AsyncMock(return_value=[]))
    response = create_client(service).get(
        "/regulation/search?query=personal%20data&topK=20"
        "&category=PUBLIC_KNOWLEDGE&sourceType=LAW&jurisdiction=CN"
    )

    assert response.status_code == 200
    assert response.json()["data"] == []
    service.search.assert_awaited_once_with(
        user_id=USER_ID,
        query="personal data",
        top_k=20,
        category=KnowledgeCategory.PUBLIC_KNOWLEDGE,
        source_type=RegulationSourceType.LAW,
        jurisdiction="CN",
    )


def test_search_endpoint_rejects_unknown_filter_before_service() -> None:
    service = SimpleNamespace(search=AsyncMock())
    response = create_client(service).get("/regulation/search?query=data&sourceType=PRIVATE_SOURCE")

    assert response.status_code == 422
    service.search.assert_not_awaited()


def test_search_endpoint_rejects_control_characters_before_external_calls() -> None:
    embedding = SimpleNamespace(embed_query=AsyncMock())
    vector_store = SimpleNamespace(search_similar=AsyncMock())
    service = RegulationSearchService(
        embedding=embedding,
        vector_store=vector_store,
        uow=FakeUnitOfWork(),
        chunk_repository=SimpleNamespace(find_searchable_ids=AsyncMock()),
    )
    client = create_client(service)

    query_response = client.get(
        "/regulation/search",
        params={"query": "unsafe\x00query"},
    )
    jurisdiction_response = client.get(
        "/regulation/search",
        params={"query": "safe query", "jurisdiction": "CN\x01"},
    )

    assert query_response.status_code == 400
    assert jurisdiction_response.status_code == 400
    embedding.embed_query.assert_not_awaited()
    vector_store.search_similar.assert_not_awaited()
