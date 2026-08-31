import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.regulation_failure import log_regulation_failure
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
from app.repositories.regulation_repository import RegulationRepository
from app.schemas.auth import CurrentUser
from app.schemas.regulation import RegulationUploadForm
from app.services.regulation_detail_service import get_regulation_detail_service
from app.services.regulation_index_service import get_regulation_index_service
from app.services.regulation_knowledge_service import get_regulation_knowledge_service
from app.services.regulation_parse_service import get_regulation_parse_service
from app.services.regulation_rule_service import get_regulation_rule_service
from app.services.regulation_service import get_regulation_service

USER_ID = UUID("9efa0f2d-7e1f-4204-8d0e-f254e36c8e59")
OTHER_USER_ID = UUID("52ea426a-89b5-464f-b55d-74f77af029ac")


class AsyncSessionAdapter:
    def __init__(self, session: Session):
        self.session = session

    async def execute(self, statement):
        return self.session.execute(statement)

    async def scalar(self, statement):
        return self.session.scalar(statement)


def regulation_row(
    regulation_id: UUID,
    *,
    category: KnowledgeCategory,
    created_at: datetime,
    enabled: bool = True,
    uploaded_by: UUID = OTHER_USER_ID,
    visibility: KnowledgeVisibility = KnowledgeVisibility.SHARED,
):
    source_type = (
        RegulationSourceType.LAW
        if category == KnowledgeCategory.PUBLIC_KNOWLEDGE
        else RegulationSourceType.INTERNAL_POLICY
    )
    return {
        "id": regulation_id,
        "title": f"Regulation {regulation_id}",
        "source_type": source_type,
        "category": category,
        "visibility": visibility,
        "language": "zh-CN",
        "jurisdiction": "CN",
        "storage_key": f"regulations/{regulation_id}.pdf",
        "original_filename": f"{regulation_id}.pdf",
        "content_type": "application/pdf",
        "file_size": 100,
        "content_hash": regulation_id.hex.ljust(64, "0"),
        "uploaded_by": uploaded_by,
        "enabled": enabled,
        "status": RegulationStatus.READY,
        "parse_error": None,
        "lock_version": 0,
        "chunk_status": RegulationChunkStatus.READY,
        "chunk_error": None,
        "index_status": RegulationIndexStatus.READY,
        "index_error": None,
        "rule_status": RegulationRuleStatus.READY,
        "rule_error": None,
        "created_at": created_at,
        "updated_at": created_at,
    }


def test_accessible_page_enforces_user_visibility_filter_count_and_stable_order():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Regulation.__table__.create(engine)
    now = datetime(2026, 8, 23, tzinfo=timezone.utc)
    newest_shared = UUID("fa000000-0000-4000-8000-000000000001")
    tie_high = UUID("ea000000-0000-4000-8000-000000000001")
    tie_low = UUID("ba000000-0000-4000-8000-000000000001")
    old_shared = UUID("ca000000-0000-4000-8000-000000000001")
    other_private = UUID("da000000-0000-4000-8000-000000000001")
    disabled = UUID("aa000000-0000-4000-8000-000000000001")
    public = UUID("f5000000-0000-4000-8000-000000000001")

    with Session(engine) as session:
        session.execute(
            Regulation.__table__.insert(),
            [
                regulation_row(
                    newest_shared, category=KnowledgeCategory.COMPANY_RULE, created_at=now
                ),
                regulation_row(
                    tie_high,
                    category=KnowledgeCategory.COMPANY_RULE,
                    created_at=now - timedelta(hours=1),
                    visibility=KnowledgeVisibility.PRIVATE,
                    uploaded_by=USER_ID,
                ),
                regulation_row(
                    tie_low,
                    category=KnowledgeCategory.COMPANY_RULE,
                    created_at=now - timedelta(hours=1),
                ),
                regulation_row(
                    old_shared,
                    category=KnowledgeCategory.COMPANY_RULE,
                    created_at=now - timedelta(hours=2),
                ),
                regulation_row(
                    other_private,
                    category=KnowledgeCategory.COMPANY_RULE,
                    created_at=now + timedelta(hours=2),
                    visibility=KnowledgeVisibility.PRIVATE,
                ),
                regulation_row(
                    disabled,
                    category=KnowledgeCategory.COMPANY_RULE,
                    created_at=now + timedelta(hours=3),
                    enabled=False,
                ),
                regulation_row(
                    public,
                    category=KnowledgeCategory.PUBLIC_KNOWLEDGE,
                    created_at=now + timedelta(hours=1),
                ),
            ],
        )
        session.commit()
        repository = RegulationRepository(AsyncSessionAdapter(session))

        first_page, total = asyncio.run(
            repository.find_accessible_page(user_id=USER_ID, offset=0, limit=2)
        )
        second_page, repeated_total = asyncio.run(
            repository.find_accessible_page(user_id=USER_ID, offset=2, limit=3)
        )
        company_items, company_total = asyncio.run(
            repository.find_accessible_page(
                user_id=USER_ID, offset=0, limit=10, category=KnowledgeCategory.COMPANY_RULE
            )
        )

    assert total == repeated_total == 5
    assert [item.id for item in first_page + second_page] == [
        public,
        newest_shared,
        tie_high,
        tie_low,
        old_shared,
    ]
    assert company_total == 4
    assert [item.id for item in company_items] == [newest_shared, tie_high, tie_low, old_shared]
    assert other_private not in {item.id for item in first_page + second_page}
    assert disabled not in {item.id for item in first_page + second_page}


def test_uploaded_page_isolates_owner_but_includes_disabled_and_failed_records():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Regulation.__table__.create(engine)
    now = datetime(2026, 8, 23, tzinfo=timezone.utc)
    own_disabled = UUID("fa000000-0000-4000-8000-000000000011")
    own_public = UUID("ea000000-0000-4000-8000-000000000011")
    other_shared = UUID("da000000-0000-4000-8000-000000000011")
    with Session(engine) as session:
        rows = [
            regulation_row(
                own_disabled,
                category=KnowledgeCategory.COMPANY_RULE,
                created_at=now,
                enabled=False,
                uploaded_by=USER_ID,
                visibility=KnowledgeVisibility.PRIVATE,
            ),
            regulation_row(
                own_public,
                category=KnowledgeCategory.PUBLIC_KNOWLEDGE,
                created_at=now - timedelta(hours=1),
                uploaded_by=USER_ID,
            ),
            regulation_row(
                other_shared,
                category=KnowledgeCategory.PUBLIC_KNOWLEDGE,
                created_at=now + timedelta(hours=1),
            ),
        ]
        rows[0]["rule_status"] = RegulationRuleStatus.FAILED
        rows[0]["rule_error"] = "rule extraction failed"
        session.execute(Regulation.__table__.insert(), rows)
        session.commit()
        repository = RegulationRepository(AsyncSessionAdapter(session))
        all_items, total = asyncio.run(
            repository.find_uploaded_page(user_id=USER_ID, offset=0, limit=10)
        )
        public_items, public_total = asyncio.run(
            repository.find_uploaded_page(
                user_id=USER_ID, offset=0, limit=10, category=KnowledgeCategory.PUBLIC_KNOWLEDGE
            )
        )

    assert total == 2
    assert [item.id for item in all_items] == [own_disabled, own_public]
    assert all_items[0].enabled is False
    assert all_items[0].rule_status == RegulationRuleStatus.FAILED
    assert all_items[0].rule_error == "rule extraction failed"
    assert public_total == 1
    assert [item.id for item in public_items] == [own_public]
    assert other_shared not in {item.id for item in all_items}


def test_uploaded_list_http_response_is_minimal_and_sanitizes_legacy_failure():
    now = datetime(2026, 8, 23, tzinfo=timezone.utc)
    item = SimpleNamespace(
        id=UUID("fa000000-0000-4000-8000-000000000021"),
        title="Internal policy",
        source_type=RegulationSourceType.INTERNAL_POLICY,
        category=KnowledgeCategory.COMPANY_RULE,
        original_filename="policy.pdf",
        file_size=100,
        enabled=True,
        status=RegulationStatus.READY,
        parse_error=None,
        parse_started_at=now,
        parse_completed_at=now,
        chunk_status=RegulationChunkStatus.READY,
        chunk_error=None,
        chunk_started_at=now,
        chunk_completed_at=now,
        created_at=now,
        updated_at=now,
        index_status=RegulationIndexStatus.READY,
        index_error=None,
        index_started_at=now,
        index_completed_at=now,
        rule_status=RegulationRuleStatus.FAILED,
        rule_error="api_key=super-secret internal-url=http://private",
        rule_started_at=now,
        rule_completed_at=now,
        content_hash="a" * 64,
        uploaded_by=USER_ID,
        storage_key="regulations/private.pdf",
        source_url="http://private",
    )
    service = SimpleNamespace(get_uploaded_page=AsyncMock(return_value=([item], 1)))
    application = create_app(
        settings=SimpleNamespace(APP_NAME="AuditMind Test", CORS_ALLOWED_ORIGINS=[])
    )
    application.dependency_overrides[get_jwt_user] = lambda: CurrentUser(
        user_id=USER_ID, username="admin"
    )
    application.dependency_overrides[get_regulation_service] = lambda: service

    response = TestClient(application).get(
        "/regulation/my/list", params={"page": 1, "pageSize": 20}
    )
    body = response.json()["data"]["items"][0]

    assert response.status_code == 200
    assert body["ruleError"] == "REGULATION_RULE_FAILED"
    assert "super-secret" not in response.text
    assert "contentHash" not in body
    assert "uploadedBy" not in body
    assert "storageKey" not in body
    assert "sourceUrl" not in body


def pipeline_regulation(*, uploaded_by=USER_ID):
    """Build a complete row-shaped object for detail and processing contracts."""
    now = datetime(2026, 8, 23, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=UUID("fa000000-0000-4000-8000-000000000041"),
        title="Data Security Law",
        source_type=RegulationSourceType.LAW,
        category=KnowledgeCategory.PUBLIC_KNOWLEDGE,
        visibility=KnowledgeVisibility.SHARED,
        language="zh-CN",
        document_number="Order 1",
        authority="Authority",
        jurisdiction="CN",
        effective_date=None,
        expiration_date=None,
        version="2026",
        source_url="https://example.com/source",
        original_filename="law.pdf",
        content_type="application/pdf",
        file_size=100,
        enabled=True,
        status=RegulationStatus.READY,
        parse_error="provider api_key=secret",
        parse_started_at=now,
        parse_completed_at=now,
        chunk_status=RegulationChunkStatus.READY,
        chunk_error=None,
        chunk_started_at=now,
        chunk_completed_at=now,
        index_status=RegulationIndexStatus.READY,
        index_error=None,
        index_started_at=now,
        index_completed_at=now,
        rule_status=RegulationRuleStatus.FAILED,
        rule_error="private traceback",
        rule_started_at=now,
        rule_completed_at=now,
        created_at=now,
        updated_at=now,
        content_hash="b" * 64,
        uploaded_by=uploaded_by,
        storage_key="regulations/private-law.pdf",
    )


def test_regulation_detail_exposes_capability_and_only_sanitized_failures():
    item = pipeline_regulation()
    service = SimpleNamespace(
        get_accessible_detail=AsyncMock(return_value=(item, 12)),
    )
    application = create_app(
        settings=SimpleNamespace(APP_NAME="AuditMind Test", CORS_ALLOWED_ORIGINS=[])
    )
    application.dependency_overrides[get_jwt_user] = lambda: CurrentUser(
        user_id=USER_ID, username="admin"
    )
    application.dependency_overrides[get_regulation_detail_service] = lambda: service

    response = TestClient(application).get(f"/regulation/get/{item.id}")
    body = response.json()["data"]

    assert response.status_code == 200
    assert body["canManage"] is True
    assert body["pageCount"] == 12
    assert body["parseError"] == "REGULATION_PARSE_FAILED"
    assert body["ruleError"] == "REGULATION_RULE_FAILED"
    assert "secret" not in response.text
    assert "traceback" not in response.text
    assert "uploadedBy" not in body
    assert "contentHash" not in body
    assert "storageKey" not in body


def test_shared_regulation_detail_is_read_only_for_non_owner():
    item = pipeline_regulation(uploaded_by=OTHER_USER_ID)
    service = SimpleNamespace(
        get_accessible_detail=AsyncMock(return_value=(item, 0)),
    )
    application = create_app(
        settings=SimpleNamespace(APP_NAME="AuditMind Test", CORS_ALLOWED_ORIGINS=[])
    )
    application.dependency_overrides[get_jwt_user] = lambda: CurrentUser(
        user_id=USER_ID, username="admin"
    )
    application.dependency_overrides[get_regulation_detail_service] = lambda: service

    response = TestClient(application).get(f"/regulation/get/{item.id}")

    assert response.status_code == 200
    assert response.json()["data"]["canManage"] is False
    assert response.json()["data"]["pageCount"] == 0


def test_pipeline_action_responses_are_minimal_and_do_not_leak_internal_fields():
    item = pipeline_regulation()
    parse_service = SimpleNamespace(
        start_parse=AsyncMock(return_value=item),
        queue_sync_parse=AsyncMock(return_value=(item, False)),
    )
    knowledge_service = SimpleNamespace(build=AsyncMock(return_value=item))
    index_service = SimpleNamespace(index=AsyncMock(return_value=item))
    rule_service = SimpleNamespace(queue_build=AsyncMock(return_value=(item, False)))
    application = create_app(
        settings=SimpleNamespace(APP_NAME="AuditMind Test", CORS_ALLOWED_ORIGINS=[])
    )
    application.dependency_overrides[get_jwt_user] = lambda: CurrentUser(
        user_id=USER_ID, username="admin"
    )
    application.dependency_overrides[get_regulation_parse_service] = lambda: parse_service
    application.dependency_overrides[get_regulation_knowledge_service] = lambda: knowledge_service
    application.dependency_overrides[get_regulation_index_service] = lambda: index_service
    application.dependency_overrides[get_regulation_rule_service] = lambda: rule_service
    client = TestClient(application)

    class AcquiredLease:
        async def __aenter__(self):
            return True

        async def __aexit__(self, exc_type, exc_value, traceback):
            return False

    with patch(
        "app.api.regulation_pipeline.acquire_regulation_pipeline_lease",
        return_value=AcquiredLease(),
    ):
        responses = [
            client.post(f"/regulation/parse/{item.id}"),
            client.post(f"/regulation/parse/sync/{item.id}"),
            client.post(f"/regulation/chunks/build/{item.id}"),
            client.post(f"/regulation/index/{item.id}"),
            client.post(f"/regulation/rules/build/{item.id}"),
        ]

    expected_keys = {
        "id",
        "title",
        "sourceType",
        "category",
        "originalFilename",
        "fileSize",
        "enabled",
        "status",
        "parseError",
        "parseStartedAt",
        "parseCompletedAt",
        "chunkStatus",
        "chunkError",
        "chunkStartedAt",
        "chunkCompletedAt",
        "createdAt",
        "updatedAt",
        "indexStatus",
        "indexError",
        "indexStartedAt",
        "indexCompletedAt",
        "ruleStatus",
        "ruleError",
        "ruleStartedAt",
        "ruleCompletedAt",
    }
    assert [response.status_code for response in responses] == [202, 202, 200, 200, 202]
    for response in responses:
        body = response.json()["data"]
        assert set(body) == expected_keys
        assert "secret" not in response.text
        assert "uploadedBy" not in body
        assert "contentHash" not in body
        assert "storageKey" not in body


def test_regulation_failure_log_excludes_message_and_traceback():
    secret = "api_key=super-secret internal-url=http://private"
    with patch("app.core.regulation_failure.logger.error") as safe_log:
        log_regulation_failure(
            "regulation.rule.build_failed",
            regulation_id=USER_ID,
            error=RuntimeError(secret),
        )

    safe_log.assert_called_once_with(
        "regulation.rule.build_failed",
        regulation_id=str(USER_ID),
        error_type="RuntimeError",
    )
    assert secret not in repr(safe_log.call_args_list)


def test_regulation_failure_log_keeps_safe_stack_locations():
    """已捕获异常保留代码位置，但不能把异常消息中的密钥写入日志。"""
    secret = "api_key=super-secret internal-url=http://private"
    with patch("app.core.regulation_failure.logger.error") as safe_log:
        try:
            raise RuntimeError(secret)
        except RuntimeError as exc:
            log_regulation_failure(
                "regulation.rule.build_failed",
                regulation_id=USER_ID,
                error=exc,
            )

    fields = safe_log.call_args.kwargs
    assert fields["error_type"] == "RuntimeError"
    assert fields["error_stack"]
    assert fields["error_stack"][-1]["function"] == (
        "test_regulation_failure_log_keeps_safe_stack_locations"
    )
    assert secret not in repr(safe_log.call_args_list)


def test_regulation_upload_form_enforces_cross_field_and_url_constraints():
    valid = RegulationUploadForm(
        title="  Data Security Policy  ",
        source_type=RegulationSourceType.INTERNAL_POLICY,
        visibility=KnowledgeVisibility.PRIVATE,
        jurisdiction=" CN ",
        source_url=" https://example.com ",
    )
    assert valid.title == "Data Security Policy"
    assert valid.jurisdiction == "CN"
    assert valid.source_url == "https://example.com/"

    invalid_values = [
        {"source_type": RegulationSourceType.LAW, "visibility": KnowledgeVisibility.PRIVATE},
        {"effective_date": "2026-08-20", "expiration_date": "2026-08-19"},
        {"source_url": "javascript:alert(1)"},
        {"source_url": "https://user:secret@example.com/source"},
        {"source_url": "https://example.com/source#private"},
        {"authority": "issuer\nspoofed"},
        {"document_number": "law\t1"},
        {"version": "v1\x00"},
        {"title": "law\x85"},
    ]
    for values in invalid_values:
        try:
            RegulationUploadForm(**{"title": "Law", "jurisdiction": "CN", **values})
        except ValueError:
            continue
        raise AssertionError(f"unsafe metadata was accepted: {values}")


def test_regulation_upload_http_response_is_minimal():
    item = SimpleNamespace(
        id=UUID("fa000000-0000-4000-8000-000000000031"),
        title="Data Security Law",
        category=KnowledgeCategory.PUBLIC_KNOWLEDGE,
        visibility=KnowledgeVisibility.SHARED,
        original_filename="law.pdf",
        status=RegulationStatus.UPLOADED,
        content_hash="a" * 64,
        uploaded_by=USER_ID,
        storage_key="regulations/private.pdf",
    )
    service = SimpleNamespace(upload=AsyncMock(return_value=item))
    application = create_app(
        settings=SimpleNamespace(APP_NAME="AuditMind Test", CORS_ALLOWED_ORIGINS=[])
    )
    application.dependency_overrides[get_jwt_user] = lambda: CurrentUser(
        user_id=USER_ID, username="admin"
    )
    application.dependency_overrides[get_regulation_service] = lambda: service

    with patch(
        "app.api.regulation_sources.schedule_regulation_pipeline",
        new=AsyncMock(return_value=True),
    ):
        response = TestClient(application).post(
            "/regulation/upload",
            data={"title": "Data Security Law", "sourceType": "LAW", "visibility": "SHARED"},
            files={"file": ("law.pdf", b"%PDF-1.7", "application/pdf")},
        )
    body = response.json()["data"]

    assert response.status_code == 200
    assert set(body) == {"id", "title", "category", "visibility", "originalFilename", "status"}
    assert "contentHash" not in response.text
    assert "uploadedBy" not in response.text


def test_regulation_upload_url_rechecks_normalized_length_boundary():
    prefix = "https://example.com/"
    exact = prefix + "a" * (1000 - len(prefix))
    assert (
        RegulationUploadForm(title="Law", jurisdiction="CN", source_url=exact).source_url == exact
    )
    for source_url in (exact + "a", prefix + "法" * 200):
        try:
            RegulationUploadForm(title="Law", jurisdiction="CN", source_url=source_url)
        except ValueError:
            continue
        raise AssertionError("URL exceeding the normalized storage limit was accepted")
