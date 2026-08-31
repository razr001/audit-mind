import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.security import get_jwt_user
from app.main import create_app
from app.models.regulation_rule import RegulationRule, RegulationRuleType
from app.repositories.regulation_rule_repository import RegulationRuleRepository
from app.schemas.auth import CurrentUser
from app.services.regulation_rule_service import get_regulation_rule_service

USER_ID = UUID("9efa0f2d-7e1f-4204-8d0e-f254e36c8e59")


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
    application.dependency_overrides[get_regulation_rule_service] = lambda: service
    return TestClient(application)


def test_rule_endpoint_filters_and_returns_safe_source_references() -> None:
    regulation_id = uuid4()
    now = "2026-08-23T01:00:00Z"
    rule = SimpleNamespace(
        id=uuid4(),
        regulation_id=regulation_id,
        rule_index=0,
        rule_type=RegulationRuleType.REQUIREMENT,
        topic="审计证据",
        subject="审计人员",
        action="保存",
        object="审计证据",
        condition=None,
        time_limit=None,
        requirements=["保持完整"],
        restrictions=[],
        exceptions=[],
        consequences=[],
        source_filename="审计法.pdf",
        source_page_start=2,
        source_page_end=2,
        source_char_start=10,
        source_char_end=24,
        source_text="审计人员应保存完整审计证据。",
        created_at=now,
        updated_at=now,
        source_content_hash="private-hash",
        source_chunk_id=uuid4(),
        source_block_ids=[str(uuid4())],
        payload={"internal": "private"},
        extractor_profile="private-profile",
        extractor_version="private-version",
    )
    service = SimpleNamespace(get_rules=AsyncMock(return_value=([rule], 1)))
    response = create_client(service).get(
        f"/regulation/rules/{regulation_id}?page=2&pageSize=10&ruleType=REQUIREMENT"
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["page"] == 2
    assert data["pageSize"] == 10
    assert data["items"][0]["sourceText"] == rule.source_text
    assert data["items"][0]["sourceBlockIds"] == rule.source_block_ids
    for private_field in (
        "sourceContentHash",
        "sourceChunkId",
        "payload",
        "extractorProfile",
        "extractorVersion",
    ):
        assert private_field not in data["items"][0]
    service.get_rules.assert_awaited_once_with(
        regulation_id=regulation_id,
        user_id=USER_ID,
        offset=10,
        limit=10,
        rule_type=RegulationRuleType.REQUIREMENT,
    )


def test_rule_endpoint_rejects_unknown_rule_type() -> None:
    service = SimpleNamespace(get_rules=AsyncMock())
    response = create_client(service).get(f"/regulation/rules/{uuid4()}?ruleType=INTERNAL")

    assert response.status_code == 422
    service.get_rules.assert_not_awaited()


def test_rule_repository_applies_type_to_items_and_count() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RegulationRule.__table__.create(engine)
    regulation_id = uuid4()

    def make_rule(index: int, rule_type: RegulationRuleType):
        return RegulationRule(
            regulation_id=regulation_id,
            source_chunk_id=uuid4(),
            source_block_ids=[],
            rule_index=index,
            rule_type=rule_type,
            topic=None,
            subject=None,
            action="保存",
            object=None,
            condition=None,
            time_limit=None,
            requirements=[],
            restrictions=[],
            exceptions=[],
            consequences=[],
            payload={},
            source_filename="law.pdf",
            source_content_hash="a" * 64,
            source_page_start=1,
            source_page_end=1,
            source_char_start=index * 10,
            source_char_end=index * 10 + 5,
            source_text="保存证据",
            extractor_profile="legal",
            extractor_version="1",
        )

    with Session(engine) as session:
        session.add_all(
            [
                make_rule(0, RegulationRuleType.REQUIREMENT),
                make_rule(1, RegulationRuleType.PENALTY),
            ]
        )
        session.commit()

        class AsyncSessionAdapter:
            async def execute(self, statement):
                return session.execute(statement)

            async def scalar(self, statement):
                return session.scalar(statement)

        items, total = asyncio.run(
            RegulationRuleRepository(AsyncSessionAdapter()).find_page_by_regulation(
                regulation_id=regulation_id,
                offset=0,
                limit=10,
                rule_type=RegulationRuleType.PENALTY,
            )
        )

    assert total == 1
    assert [item.rule_type for item in items] == [RegulationRuleType.PENALTY]
