import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.api.users import get_user_management_service
from app.core.error_codes import CANNOT_DELETE_CURRENT_USER, USERNAME_ALREADY_EXISTS
from app.core.exceptions import BusinessException
from app.core.security import get_jwt_user
from app.main import create_app
from app.models.user import User
from app.schemas.auth import CurrentUser
from app.services import user_management_service as service_module
from app.services.user_management_service import UserManagementService


class FakeUnitOfWork:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False


class FakeRepository:
    def __init__(self, users: list[User] | None = None) -> None:
        self.users = users or []

    async def find_by_username(self, username: str) -> User | None:
        return next(
            (user for user in self.users if user.username.lower() == username.lower()),
            None,
        )

    async def find_by_id(self, user_id: UUID) -> User | None:
        return next((user for user in self.users if user.id == user_id), None)

    async def list_users(self) -> list[User]:
        return sorted(self.users, key=lambda user: user.username)

    async def save(self, user: User) -> User:
        self.users.append(user)
        return user

    async def delete_user(self, user: User) -> None:
        self.users.remove(user)


class FakeRefreshStore:
    def __init__(self) -> None:
        self.revoked: list[UUID] = []

    async def revoke_user(self, user_id: UUID) -> None:
        self.revoked.append(user_id)


@asynccontextmanager
async def acquired_lease(**_: object):
    yield True


def build_app(service: object, current_user_id: UUID):
    app = create_app(
        settings=SimpleNamespace(
            APP_NAME="AuditMind Test",
            CORS_ALLOWED_ORIGINS=[],
            ENVIRONMENT="local",
        )
    )
    app.dependency_overrides[get_jwt_user] = lambda: CurrentUser(
        user_id=current_user_id,
        username="admin",
    )
    app.dependency_overrides[get_user_management_service] = lambda: service
    return app


def test_user_management_api_lists_creates_and_deletes_users() -> None:
    now = datetime.now(timezone.utc)
    admin_id = uuid4()
    member_id = uuid4()
    users = [
        User(
            id=admin_id,
            username="admin",
            password_hash="hidden",
            created_at=now,
            updated_at=now,
        ),
        User(
            id=member_id,
            username="member",
            password_hash="also-hidden",
            created_at=now,
            updated_at=now,
        ),
    ]

    class FakeService:
        async def list_users(self) -> list[User]:
            return users

        async def create(self, username: str, _password: str) -> User:
            user = User(
                id=uuid4(),
                username=username,
                password_hash="never-returned",
                created_at=now,
                updated_at=now,
            )
            users.append(user)
            return user

        async def delete_by_id(self, user_id: UUID, *, actor_user_id: UUID) -> None:
            assert actor_user_id == admin_id
            users[:] = [user for user in users if user.id != user_id]

    client = TestClient(build_app(FakeService(), admin_id))
    listed = client.get("/users")
    created = client.post(
        "/users",
        json={"username": "auditor", "password": "strong-password"},
    )
    deleted = client.delete(f"/users/{member_id}")

    assert listed.status_code == 200
    assert [item["username"] for item in listed.json()["data"]] == ["admin", "member"]
    assert "passwordHash" not in created.json()["data"]
    assert created.status_code == 201
    assert deleted.status_code == 200


def test_service_rejects_duplicate_username_and_self_deletion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service_module, "acquire_redis_lease", acquired_lease)
    user = User(id=uuid4(), username="admin", password_hash="hidden")
    repository = FakeRepository([user])
    service = UserManagementService(
        uow=FakeUnitOfWork(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        refresh_store=FakeRefreshStore(),  # type: ignore[arg-type]
        redis=object(),  # type: ignore[arg-type]
    )

    async def scenario() -> None:
        with pytest.raises(BusinessException) as duplicate:
            await service.create("ADMIN", "strong-password")
        assert duplicate.value.code == USERNAME_ALREADY_EXISTS

        with pytest.raises(BusinessException) as self_delete:
            await service.delete_by_id(user.id, actor_user_id=user.id)
        assert self_delete.value.code == CANNOT_DELETE_CURRENT_USER

    asyncio.run(scenario())


def test_user_model_has_case_insensitive_unique_username_index() -> None:
    index = next(
        index
        for index in User.__table__.indexes
        if index.name == "ux_app_user_username_lower"
    )

    assert index.unique is True
    assert "lower" in str(next(iter(index.expressions))).lower()


def test_service_translates_database_username_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service_module, "acquire_redis_lease", acquired_lease)

    class ConflictingUnitOfWork(FakeUnitOfWork):
        async def __aexit__(self, *_: object) -> bool:
            raise IntegrityError("insert app_user", {}, RuntimeError("duplicate"))

    service = UserManagementService(
        uow=ConflictingUnitOfWork(),  # type: ignore[arg-type]
        repository=FakeRepository(),  # type: ignore[arg-type]
        refresh_store=FakeRefreshStore(),  # type: ignore[arg-type]
        redis=object(),  # type: ignore[arg-type]
    )

    with pytest.raises(BusinessException) as conflict:
        asyncio.run(service.create("Auditor", "strong-password"))

    assert conflict.value.code == USERNAME_ALREADY_EXISTS


def test_service_deletes_other_user_and_revokes_refresh_sessions() -> None:
    admin = User(id=uuid4(), username="admin", password_hash="hidden")
    member = User(id=uuid4(), username="member", password_hash="hidden")
    repository = FakeRepository([admin, member])
    refresh_store = FakeRefreshStore()
    service = UserManagementService(
        uow=FakeUnitOfWork(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        refresh_store=refresh_store,  # type: ignore[arg-type]
        redis=object(),  # type: ignore[arg-type]
    )

    asyncio.run(service.delete_by_id(member.id, actor_user_id=admin.id))

    assert refresh_store.revoked == [member.id]
    assert repository.users == [admin]
