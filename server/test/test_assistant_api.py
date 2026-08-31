import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest

from app.ai.regulation_qa.errors import RegulationCitationVerificationError
from app.api import assistant as assistant_api
from app.api import assistant_stream
from app.core.exceptions import BusinessException
from app.models.assistant import (
    AssistantAgentRunStatus,
    AssistantConversation,
    AssistantMessage,
    AssistantMessageRole,
    AssistantMessageStatus,
)
from app.schemas.assistant import AssistantMessageRequest
from app.services.assistant_service import AssistantService, AssistantTurn

USER_ID = UUID("9efa0f2d-7e1f-4204-8d0e-f254e36c8e59")


class FakeRequest:
    async def is_disconnected(self) -> bool:
        return False


class FakeAnswerService:
    async def stream(self, **_kwargs):
        yield {"type": "phase", "data": {"phase": "retrieving"}}
        yield {"type": "text-delta", "data": {"textDelta": "完整回答"}}
        yield {"type": "sources", "data": {"sources": []}}
        yield {"type": "verified", "data": {"answered": True}}
        yield {"type": "done", "data": {}}


class FakeUnitOfWork:
    entered = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *_args):
        return None


def make_turn() -> AssistantTurn:
    conversation_id = uuid4()
    conversation = AssistantConversation(
        id=conversation_id,
        user_id=USER_ID,
        title="测试对话",
    )
    user_message = AssistantMessage(
        id=uuid4(),
        conversation_id=conversation_id,
        role=AssistantMessageRole.USER,
        content="问题",
        status=AssistantMessageStatus.COMPLETED,
        sources=[],
    )
    assistant_message = AssistantMessage(
        id=uuid4(),
        conversation_id=conversation_id,
        role=AssistantMessageRole.ASSISTANT,
        content="",
        status=AssistantMessageStatus.GENERATING,
        sources=[],
    )
    return AssistantTurn(conversation, user_message, assistant_message, [])


def test_assistant_stream_forwards_complete_provider_delta_without_sleep(monkeypatch) -> None:
    turn = make_turn()
    service = SimpleNamespace(finish_turn=AsyncMock(), fail_turn=AsyncMock())
    log_info = Mock()
    monkeypatch.setattr(assistant_stream.logger, "info", log_info)

    async def collect() -> list[str]:
        return [
            frame
            async for frame in assistant_stream.assistant_event_stream(
                turn=turn,
                question="问题",
                request=FakeRequest(),
                assistant_service=service,
                answer_service=FakeAnswerService(),
                user_id=USER_ID,
            )
        ]

    frames = asyncio.run(collect())

    assert sum("event: text-delta" in frame for frame in frames) == 1
    assert any('"textDelta":"完整回答"' in frame for frame in frames)
    service.finish_turn.assert_awaited_once_with(
        turn.assistant_message,
        content="完整回答",
        sources=[],
        answered=True,
    )
    log_info.assert_any_call(
        "assistant.chat.turn_started",
        question_length=2,
        conversation_id=str(turn.conversation.id),
        user_message_id=str(turn.user_message.id),
        assistant_message_id=str(turn.assistant_message.id),
    )
    log_info.assert_any_call(
        "assistant.chat.turn_completed",
        answer_length=4,
        source_count=0,
        answered=True,
        conversation_id=str(turn.conversation.id),
        assistant_message_id=str(turn.assistant_message.id),
    )


def test_assistant_stream_stops_before_answer_when_redis_lease_is_lost() -> None:
    turn = make_turn()
    service = SimpleNamespace(finish_turn=AsyncMock(), fail_turn=AsyncMock())
    lease = SimpleNamespace(is_owned=AsyncMock(return_value=False), release=AsyncMock())

    async def collect() -> list[str]:
        return [
            frame
            async for frame in assistant_stream.assistant_event_stream(
                turn=turn,
                question="问题",
                request=FakeRequest(),
                assistant_service=service,
                answer_service=FakeAnswerService(),
                user_id=USER_ID,
                lease=lease,
            )
        ]

    frames = asyncio.run(collect())

    assert all("event: text-delta" not in frame for frame in frames)
    assert frames[-2].startswith("event: error")
    assert frames[-1].startswith("event: done")
    service.finish_turn.assert_not_awaited()
    service.fail_turn.assert_awaited_once_with(
        turn.assistant_message,
        AssistantMessageStatus.FAILED,
    )
    lease.release.assert_awaited_once()


def test_assistant_stream_does_not_finish_after_database_fencing_rejects_write() -> None:
    turn = make_turn()
    service = SimpleNamespace(
        finish_turn=AsyncMock(return_value=False),
        fail_turn=AsyncMock(),
    )
    lease = SimpleNamespace(is_owned=AsyncMock(return_value=True), release=AsyncMock())

    async def collect() -> list[str]:
        return [
            frame
            async for frame in assistant_stream.assistant_event_stream(
                turn=turn,
                question="问题",
                request=FakeRequest(),
                assistant_service=service,
                answer_service=FakeAnswerService(),
                user_id=USER_ID,
                lease=lease,
            )
        ]

    frames = asyncio.run(collect())

    assert any("event: text-delta" in frame for frame in frames)
    assert frames[-2].startswith("event: error")
    assert frames[-1].startswith("event: done")
    service.fail_turn.assert_awaited_once_with(
        turn.assistant_message,
        AssistantMessageStatus.FAILED,
    )
    lease.release.assert_awaited_once()


def test_assistant_stream_fails_turn_when_lease_is_lost_before_commit() -> None:
    turn = make_turn()
    service = SimpleNamespace(
        finish_turn=AsyncMock(),
        fail_turn=AsyncMock(),
    )
    lease = SimpleNamespace(
        is_owned=AsyncMock(side_effect=[True, False]),
        release=AsyncMock(),
    )

    async def collect() -> list[str]:
        return [
            frame
            async for frame in assistant_stream.assistant_event_stream(
                turn=turn,
                question="问题",
                request=FakeRequest(),
                assistant_service=service,
                answer_service=FakeAnswerService(),
                user_id=USER_ID,
                lease=lease,
            )
        ]

    frames = asyncio.run(collect())

    assert any("event: text-delta" in frame for frame in frames)
    assert frames[-2].startswith("event: error")
    assert frames[-1].startswith("event: done")
    service.finish_turn.assert_not_awaited()
    service.fail_turn.assert_awaited_once_with(
        turn.assistant_message,
        AssistantMessageStatus.FAILED,
    )
    lease.release.assert_awaited_once()


def test_assistant_stream_logs_citation_verification_failure(monkeypatch) -> None:
    class InvalidCitationAnswerService:
        async def stream(self, **_kwargs):
            if False:
                yield None
            raise RegulationCitationVerificationError("AI cited an unknown regulation chunk")

    turn = make_turn()
    service = SimpleNamespace(finish_turn=AsyncMock(), fail_turn=AsyncMock())
    log_error = Mock()
    monkeypatch.setattr(assistant_stream.logger, "error", log_error)

    async def collect() -> list[str]:
        return [
            frame
            async for frame in assistant_stream.assistant_event_stream(
                turn=turn,
                question="问题",
                request=FakeRequest(),
                assistant_service=service,
                answer_service=InvalidCitationAnswerService(),
                user_id=USER_ID,
            )
        ]

    frames = asyncio.run(collect())

    service.fail_turn.assert_awaited_once_with(
        turn.assistant_message,
        AssistantMessageStatus.FAILED,
    )
    log_error.assert_called_once_with(
        "assistant.chat.citation_verification_failed",
        error_type="RegulationCitationVerificationError",
        conversation_id=str(turn.conversation.id),
        assistant_message_id=str(turn.assistant_message.id),
    )
    assert frames[-2].startswith("event: error")
    assert frames[-1].startswith("event: done")


def test_assistant_stream_logs_unexpected_failure_without_sensitive_traceback(
    monkeypatch,
) -> None:
    class FailingAnswerService:
        async def stream(self, **_kwargs):
            if False:
                yield None
            raise RuntimeError("unexpected failure")

    turn = make_turn()
    service = SimpleNamespace(finish_turn=AsyncMock(), fail_turn=AsyncMock())
    log_error = Mock()
    monkeypatch.setattr(assistant_stream.logger, "error", log_error)

    async def collect() -> list[str]:
        return [
            frame
            async for frame in assistant_stream.assistant_event_stream(
                turn=turn,
                question="问题",
                request=FakeRequest(),
                assistant_service=service,
                answer_service=FailingAnswerService(),
                user_id=USER_ID,
            )
        ]

    asyncio.run(collect())

    log_error.assert_called_once_with(
        "assistant.chat.stream_failed",
        error_type="RuntimeError",
        conversation_id=str(turn.conversation.id),
        assistant_message_id=str(turn.assistant_message.id),
    )


def test_new_conversation_is_initialized_and_locked_before_streaming_response(
    monkeypatch,
) -> None:
    turn = make_turn()
    service = SimpleNamespace(begin_new_turn=AsyncMock(return_value=turn))
    lease = SimpleNamespace(acquire=AsyncMock(return_value=True), release=AsyncMock())
    monkeypatch.setattr(assistant_api, "RedisLease", lambda **_kwargs: lease)
    monkeypatch.setattr(
        assistant_api,
        "get_request_user",
        lambda: SimpleNamespace(user_id=USER_ID),
    )

    async def call_endpoint():
        return await assistant_api.stream_new_conversation(
            AssistantMessageRequest(question="首个问题"),
            FakeRequest(),
            service,
            FakeAnswerService(),
        )

    response = asyncio.run(call_endpoint())

    service.begin_new_turn.assert_awaited_once_with(USER_ID, "首个问题")
    lease.acquire.assert_awaited_once()
    assert response.media_type == "text/event-stream"


def test_conversation_lease_has_a_bounded_maximum_hold(monkeypatch) -> None:
    captured: dict = {}

    class CapturingLease:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def acquire(self) -> bool:
            return True

    monkeypatch.setattr(assistant_api, "RedisLease", CapturingLease)

    lease = asyncio.run(
        assistant_api._acquire_conversation_lease(
            user_id=USER_ID,
            conversation_id=uuid4(),
        )
    )

    assert isinstance(lease, CapturingLease)
    assert captured["max_hold_seconds"] == assistant_api.settings.ASSISTANT_TURN_TIMEOUT_SECONDS


def test_initialization_failure_happens_before_streaming_response(monkeypatch) -> None:
    service = SimpleNamespace(begin_new_turn=AsyncMock(side_effect=RuntimeError("db failed")))
    monkeypatch.setattr(
        assistant_api,
        "get_request_user",
        lambda: SimpleNamespace(user_id=USER_ID),
    )

    async def call_endpoint():
        return await assistant_api.stream_new_conversation(
            AssistantMessageRequest(question="首个问题"),
            FakeRequest(),
            service,
            FakeAnswerService(),
        )

    with pytest.raises(RuntimeError, match="db failed"):
        asyncio.run(call_endpoint())


def test_begin_new_turn_persists_conversation_and_messages_atomically() -> None:
    uow = FakeUnitOfWork()

    async def save_conversation(conversation):
        conversation.id = uuid4()
        return conversation

    async def save_message(message):
        message.id = uuid4()
        return message

    repository = SimpleNamespace(
        save_conversation=AsyncMock(side_effect=save_conversation),
        save_message=AsyncMock(side_effect=save_message),
    )
    service = AssistantService(
        session=AsyncMock(),
        uow=uow,
        repository=repository,
    )

    turn = asyncio.run(service.begin_new_turn(USER_ID, "需要审计哪些电商事项？"))

    assert uow.entered is True
    assert turn.conversation.title == "需要审计哪些电商事项？"
    assert turn.conversation.last_message_at.tzinfo is not None
    assert turn.user_message.conversation_id == turn.conversation.id
    assert turn.assistant_message.conversation_id == turn.conversation.id
    assert repository.save_message.await_count == 2


def test_finish_turn_commits_message_and_agent_run_together() -> None:
    message = SimpleNamespace(id=uuid4())
    repository = SimpleNamespace(complete_generating_message=AsyncMock(return_value=True))
    run_repository = SimpleNamespace(set_status_for_message=AsyncMock(return_value=True))
    service = AssistantService(
        session=AsyncMock(),
        uow=FakeUnitOfWork(),
        repository=repository,
        run_repository=run_repository,
    )

    completed = asyncio.run(
        service.finish_turn(
            message,
            content="完整回答",
            sources=[],
            answered=True,
        )
    )

    assert completed is True
    assert run_repository.set_status_for_message.await_args.kwargs["status"] == (
        AssistantAgentRunStatus.COMPLETED
    )


def test_stream_failure_does_not_cancel_a_durable_pending_approval() -> None:
    message = SimpleNamespace(id=uuid4(), status=AssistantMessageStatus.WAITING_APPROVAL)
    session = SimpleNamespace(
        rollback=AsyncMock(),
        get=AsyncMock(return_value=message),
    )
    repository = SimpleNamespace(fail_generating_message=AsyncMock())
    run_repository = SimpleNamespace(set_status_for_message=AsyncMock())
    service = AssistantService(
        session=session,
        uow=FakeUnitOfWork(),
        repository=repository,
        run_repository=run_repository,
    )

    asyncio.run(service.fail_turn(message, AssistantMessageStatus.FAILED))

    repository.fail_generating_message.assert_not_awaited()
    run_repository.set_status_for_message.assert_not_awaited()


def test_existing_conversation_rejects_a_concurrent_turn(monkeypatch) -> None:
    class BusyLease:
        def __init__(self, **_kwargs):
            pass

        async def acquire(self) -> bool:
            return False

    service = SimpleNamespace(begin_turn=AsyncMock())
    monkeypatch.setattr(assistant_api, "RedisLease", BusyLease)
    monkeypatch.setattr(
        assistant_api,
        "get_request_user",
        lambda: SimpleNamespace(user_id=USER_ID),
    )

    async def call_endpoint():
        return await assistant_api.stream_message(
            uuid4(),
            AssistantMessageRequest(question="并发问题"),
            FakeRequest(),
            service,
            FakeAnswerService(),
        )

    with pytest.raises(BusinessException, match="conversation is generating"):
        asyncio.run(call_endpoint())
    service.begin_turn.assert_not_awaited()
