import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from app.infrastructure.assistant_conversation_cache import AssistantConversationCache
from app.models.assistant import AssistantConversation
from app.services.assistant_service import AssistantService


class FakeUnitOfWork:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def conversation(*, user_id=None) -> AssistantConversation:
    now = datetime.now(timezone.utc)
    return AssistantConversation(
        id=uuid4(),
        user_id=user_id or uuid4(),
        title="审计会话",
        last_message_at=now,
        created_at=now,
        updated_at=now,
    )


def test_conversation_cache_round_trips_snapshot_with_ttl() -> None:
    values: dict[str, str] = {}

    async def set_value(key, value, *, ex):
        assert ex == 60
        values[key] = value

    async def increment(key):
        values[key] = str(int(values.get(key, "0")) + 1)
        return int(values[key])

    backend = SimpleNamespace(
        get=AsyncMock(side_effect=lambda key: values.get(key)),
        set=AsyncMock(side_effect=set_value),
        delete=AsyncMock(side_effect=lambda key: values.pop(key, None)),
        incr=AsyncMock(side_effect=increment),
        expire=AsyncMock(return_value=True),
    )
    cache = AssistantConversationCache(
        client=SimpleNamespace(client=backend),
        ttl_seconds=60,
    )
    source = conversation()

    asyncio.run(cache.set(source))
    cached = asyncio.run(cache.get(source.id, source.user_id))

    assert cached is not None
    assert cached.id == source.id
    assert cached.user_id == source.user_id
    assert cached.title == source.title


def test_conversation_list_cache_uses_versioned_invalidation() -> None:
    values: dict[str, str] = {}

    async def set_value(key, value, *, ex):
        values[key] = value

    async def increment(key):
        values[key] = str(int(values.get(key, "0")) + 1)
        return int(values[key])

    backend = SimpleNamespace(
        get=AsyncMock(side_effect=lambda key: values.get(key)),
        set=AsyncMock(side_effect=set_value),
        delete=AsyncMock(side_effect=lambda key: values.pop(key, None)),
        incr=AsyncMock(side_effect=increment),
        expire=AsyncMock(return_value=True),
    )
    cache = AssistantConversationCache(
        client=SimpleNamespace(client=backend),
        ttl_seconds=60,
    )
    user_id = uuid4()
    items = [conversation(user_id=user_id), conversation(user_id=user_id)]

    asyncio.run(
        cache.set_list(user_id=user_id, offset=0, limit=20, items=items, total=2)
    )
    cached = asyncio.run(cache.get_list(user_id=user_id, offset=0, limit=20))
    asyncio.run(cache.invalidate_list(user_id))
    invalidated = asyncio.run(cache.get_list(user_id=user_id, offset=0, limit=20))

    assert cached is not None
    assert [item.id for item in cached[0]] == [item.id for item in items]
    assert cached[1] == 2
    assert invalidated is None


def test_list_conversations_cache_hit_skips_database() -> None:
    user_id = uuid4()
    cached = ([conversation(user_id=user_id)], 1)
    repository = SimpleNamespace(list_conversations=AsyncMock())
    cache = SimpleNamespace(get_list=AsyncMock(return_value=cached))
    service = AssistantService(
        session=AsyncMock(),
        uow=FakeUnitOfWork(),
        repository=repository,
        conversation_cache=cache,
    )

    result = asyncio.run(service.list_conversations(user_id, 0, 20))

    assert result == cached
    repository.list_conversations.assert_not_awaited()


def test_list_conversations_cache_miss_populates_page() -> None:
    user_id = uuid4()
    persisted = [conversation(user_id=user_id)]
    repository = SimpleNamespace(
        list_conversations=AsyncMock(return_value=(persisted, 1))
    )
    cache = SimpleNamespace(
        get_list=AsyncMock(return_value=None),
        set_list=AsyncMock(),
    )
    service = AssistantService(
        session=AsyncMock(),
        uow=FakeUnitOfWork(),
        repository=repository,
        conversation_cache=cache,
    )

    result = asyncio.run(service.list_conversations(user_id, 20, 20))

    assert result == (persisted, 1)
    cache.set_list.assert_awaited_once_with(
        user_id=user_id,
        offset=20,
        limit=20,
        items=persisted,
        total=1,
    )


def test_get_conversation_cache_hit_skips_database() -> None:
    cached = conversation()
    repository = SimpleNamespace(find_conversation=AsyncMock())
    cache = SimpleNamespace(
        get=AsyncMock(return_value=cached),
        set=AsyncMock(),
        invalidate=AsyncMock(),
    )
    service = AssistantService(
        session=AsyncMock(),
        uow=FakeUnitOfWork(),
        repository=repository,
        conversation_cache=cache,
    )

    result = asyncio.run(service.get_conversation(cached.id, cached.user_id))

    assert result is cached
    repository.find_conversation.assert_not_awaited()


def test_get_conversation_cache_miss_populates_from_database() -> None:
    persisted = conversation()
    repository = SimpleNamespace(find_conversation=AsyncMock(return_value=persisted))
    cache = SimpleNamespace(
        get=AsyncMock(return_value=None),
        set=AsyncMock(),
        invalidate=AsyncMock(),
    )
    service = AssistantService(
        session=AsyncMock(),
        uow=FakeUnitOfWork(),
        repository=repository,
        conversation_cache=cache,
    )

    result = asyncio.run(service.get_conversation(persisted.id, persisted.user_id))

    assert result is persisted
    repository.find_conversation.assert_awaited_once_with(
        persisted.id,
        persisted.user_id,
    )
    cache.set.assert_awaited_once_with(persisted)


def test_delete_conversation_bypasses_snapshot_and_invalidates_both_sides() -> None:
    persisted = conversation()
    session = SimpleNamespace(delete=AsyncMock())
    repository = SimpleNamespace(
        session=session,
        find_conversation=AsyncMock(return_value=persisted),
    )
    cache = SimpleNamespace(
        get=AsyncMock(return_value=conversation(user_id=persisted.user_id)),
        set=AsyncMock(),
        invalidate=AsyncMock(),
        invalidate_list=AsyncMock(),
    )
    service = AssistantService(
        session=AsyncMock(),
        uow=FakeUnitOfWork(),
        repository=repository,
        conversation_cache=cache,
    )

    asyncio.run(service.delete_conversation(persisted.id, persisted.user_id))

    repository.find_conversation.assert_awaited_once_with(
        persisted.id,
        persisted.user_id,
    )
    cache.get.assert_not_awaited()
    assert cache.invalidate.await_count == 2
    assert cache.invalidate_list.await_count == 2
    session.delete.assert_awaited_once_with(persisted)
