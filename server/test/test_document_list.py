import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.security import get_jwt_user
from app.main import create_app
from app.models.document import Document, DocumentSourceType, DocumentStatus
from app.repositories.document_repository import DocumentRepository
from app.schemas.auth import CurrentUser
from app.services.document_parse_service import get_document_parse_service
from app.services.document_service import get_document_service

USER_ID = UUID("9efa0f2d-7e1f-4204-8d0e-f254e36c8e59")


def make_document():
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        original_filename="control-matrix.pdf",
        content_type="application/pdf",
        file_size=2048,
        status=DocumentStatus.READY,
        created_at=now,
        updated_at=now,
        parse_error=None,
        parse_started_at=None,
        parse_completed_at=now,
        source_type=DocumentSourceType.PDF,
    )


def application_with_service(service, *, parse_service=None):
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
    application.dependency_overrides[get_document_service] = lambda: service
    if parse_service is not None:
        application.dependency_overrides[get_document_parse_service] = lambda: parse_service
    return application


def test_document_list_forwards_pagination_and_allow_listed_sort() -> None:
    document = make_document()
    service = SimpleNamespace(
        get_document_list=AsyncMock(return_value=([document], 11)),
    )

    response = TestClient(application_with_service(service)).get(
        "/document/list",
        params={"page": 2, "pageSize": 5, "sortBy": "fileSize", "sortOrder": "asc"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["total"] == 11
    assert response.json()["data"]["items"][0]["id"] == str(document.id)
    service.get_document_list.assert_awaited_once_with(
        USER_ID,
        5,
        5,
        sort_by="fileSize",
        sort_order="asc",
    )


def test_document_list_rejects_unknown_sort_input() -> None:
    response = TestClient(application_with_service(SimpleNamespace())).get(
        "/document/list",
        params={"sortBy": "storageKey", "sortOrder": "sideways"},
    )

    assert response.status_code == 422
    fields = {item["field"] for item in response.json()["data"]["errors"]}
    assert {"query.sortBy", "query.sortOrder"} <= fields


def test_document_detail_forwards_the_authenticated_user_scope() -> None:
    document = make_document()
    service = SimpleNamespace(get_document=AsyncMock(return_value=document))

    response = TestClient(application_with_service(service)).get(f"/document/get/{document.id}")

    assert response.status_code == 200
    assert response.json()["data"]["id"] == str(document.id)
    service.get_document.assert_awaited_once_with(document.id, USER_ID)


def test_document_detail_redacts_legacy_internal_failures() -> None:
    document = make_document()
    document.status = DocumentStatus.FAILED
    document.parse_error = "provider api_key=secret"
    service = SimpleNamespace(get_document=AsyncMock(return_value=document))

    response = TestClient(application_with_service(service)).get(f"/document/get/{document.id}")

    assert response.status_code == 200
    assert response.json()["data"]["parseError"] == "DOCUMENT_PARSE_FAILED"
    assert "secret" not in response.text


def test_document_download_url_forwards_user_scope_and_short_expiry() -> None:
    document = make_document()
    service = SimpleNamespace(
        create_download_url=AsyncMock(
            return_value=(
                "https://files.example.com/document.pdf?signature=test",
                600,
            )
        ),
    )

    response = TestClient(application_with_service(service)).get(
        f"/document/get/download-url/{document.id}"
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "url": "https://files.example.com/document.pdf?signature=test",
        "expiresIn": 600,
    }
    service.create_download_url.assert_awaited_once_with(
        document_id=document.id,
        user_id=USER_ID,
    )


def test_document_processing_endpoints_forward_authenticated_user_scope() -> None:
    document = make_document()
    parse_service = SimpleNamespace(
        start_parse=AsyncMock(return_value=document),
        sync_parse_result=AsyncMock(return_value=document),
    )
    client = TestClient(
        application_with_service(
            SimpleNamespace(),
            parse_service=parse_service,
        )
    )

    start_response = client.post(f"/document/parse/{document.id}")
    sync_response = client.post(f"/document/parse/sync/{document.id}")

    assert start_response.status_code == 202
    assert sync_response.status_code == 200
    parse_service.start_parse.assert_awaited_once_with(
        document_id=document.id,
        user_id=USER_ID,
    )
    parse_service.sync_parse_result.assert_awaited_once_with(
        document_id=document.id,
        user_id=USER_ID,
    )


def test_repository_uses_requested_stable_sort_and_user_scope() -> None:
    class ScalarRows:
        def scalars(self):
            return self

        def all(self):
            return []

    session = SimpleNamespace(
        execute=AsyncMock(return_value=ScalarRows()),
        scalar=AsyncMock(return_value=0),
    )
    repository = DocumentRepository(session)

    asyncio.run(
        repository.find_page_by_user(
            user_id=USER_ID,
            offset=10,
            limit=10,
            sort_by="originalFilename",
            sort_order="asc",
        )
    )

    page_statement = str(session.execute.await_args.args[0])
    count_statement = str(session.scalar.await_args.args[0])
    assert "document.user_id" in page_statement
    assert "document.user_id" in count_statement
    assert "document.original_filename ASC" in page_statement
    assert "document.id ASC" in page_statement


def test_repository_returns_isolated_pages_for_every_sort_and_direction() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Document.__table__.create(engine)
    base_time = datetime(2026, 8, 20, tzinfo=timezone.utc)
    ids = [
        UUID(f"{prefix}0000000-0000-0000-0000-00000000000{index}")
        for index, prefix in enumerate("abcde", 1)
    ]
    rows = []
    values = [
        ("zeta.pdf", 300, DocumentStatus.READY, 2),
        ("alpha.pdf", 100, DocumentStatus.FAILED, 1),
        ("same.pdf", 200, DocumentStatus.UPLOADED, 3),
        ("same.pdf", 200, DocumentStatus.UPLOADED, 3),
        ("middle.pdf", 400, DocumentStatus.PARSING, 4),
    ]
    for document_id, (filename, size, status, day) in zip(ids, values, strict=True):
        created_at = base_time + timedelta(days=day)
        rows.append(
            {
                "id": document_id,
                "user_id": USER_ID,
                "original_filename": filename,
                "storage_key": f"documents/{document_id}",
                "content_type": "application/pdf",
                "file_size": size,
                "status": status,
                "lock_version": 0,
                "source_type": DocumentSourceType.PDF,
                "created_at": created_at,
                "updated_at": created_at,
            }
        )
    other_id = uuid4()
    other_row = {
        **rows[0],
        "id": other_id,
        "user_id": uuid4(),
        "storage_key": f"documents/{other_id}",
    }

    with Session(engine) as session:
        session.execute(Document.__table__.insert(), [*rows, other_row])
        session.commit()

        class AsyncSessionAdapter:
            async def execute(self, statement):
                return session.execute(statement)

            async def scalar(self, statement):
                return session.scalar(statement)

        repository = DocumentRepository(AsyncSessionAdapter())
        key_functions = {
            "createdAt": lambda row: row["created_at"],
            "originalFilename": lambda row: row["original_filename"],
            "fileSize": lambda row: row["file_size"],
            "status": lambda row: row["status"].value,
        }
        for sort_by, field_key in key_functions.items():
            for sort_order in ("asc", "desc"):
                documents, total = asyncio.run(
                    repository.find_page_by_user(
                        user_id=USER_ID,
                        offset=1,
                        limit=2,
                        sort_by=sort_by,
                        sort_order=sort_order,
                    )
                )
                expected = sorted(
                    rows,
                    key=lambda row: (field_key(row), str(row["id"])),
                    reverse=sort_order == "desc",
                )[1:3]
                assert total == len(rows)
                assert [document.id for document in documents] == [row["id"] for row in expected]

    index_names = {index.name for index in Document.__table__.indexes}
    assert {
        "ix_document_user_created_id",
        "ix_document_user_filename_id",
        "ix_document_user_size_id",
        "ix_document_user_status_id",
    } <= index_names


def test_document_parse_claim_recovers_only_stale_jobs_without_external_task() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Document.__table__.create(engine)
    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    stale_before = now - timedelta(hours=1)
    stale_id, active_id, external_id = uuid4(), uuid4(), uuid4()

    def row(document_id: UUID, *, started_at: datetime, task_id: str | None = None):
        return {
            "id": document_id,
            "user_id": USER_ID,
            "original_filename": f"{document_id}.pdf",
            "storage_key": f"documents/{document_id}",
            "content_type": "application/pdf",
            "file_size": 100,
            "source_type": DocumentSourceType.PDF,
            "status": DocumentStatus.PARSING,
            "lock_version": 1,
            "parse_task_id": task_id,
            "parse_started_at": started_at,
            "created_at": started_at,
            "updated_at": started_at,
        }

    with Session(engine) as session:
        session.execute(
            Document.__table__.insert(),
            [
                row(stale_id, started_at=now - timedelta(hours=2)),
                row(active_id, started_at=now - timedelta(minutes=5)),
                row(
                    external_id,
                    started_at=now - timedelta(hours=2),
                    task_id="mineru-task",
                ),
            ],
        )
        session.commit()

        class AsyncSessionAdapter:
            async def execute(self, statement):
                return session.execute(statement)

        repository = DocumentRepository(AsyncSessionAdapter())
        claimed = asyncio.run(
            repository.claim_for_parse(
                document_id=stale_id,
                user_id=USER_ID,
                started_at=now,
                stale_before=stale_before,
            )
        )
        active = asyncio.run(
            repository.claim_for_parse(
                document_id=active_id,
                user_id=USER_ID,
                started_at=now,
                stale_before=stale_before,
            )
        )
        external = asyncio.run(
            repository.claim_for_parse(
                document_id=external_id,
                user_id=USER_ID,
                started_at=now,
                stale_before=stale_before,
            )
        )

        assert claimed is not None
        assert claimed.lock_version == 2
        assert active is None
        assert external is None
