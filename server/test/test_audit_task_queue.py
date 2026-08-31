import asyncio
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.core.config import get_settings
from app.infrastructure.task_broker import task_broker
from app.tasks.audit_dispatcher import enqueue_audit_pipeline
from app.tasks.audit_tasks import execute_audit_pipeline
from app.tasks.regulation_dispatcher import enqueue_regulation_pipeline
from app.tasks.regulation_tasks import execute_regulation_pipeline


def test_audit_actor_uses_configured_broker() -> None:
    """Actor 必须显式绑定项目 Broker，不能退回 localhost 默认配置。"""
    assert execute_audit_pipeline.broker is task_broker
    assert execute_audit_pipeline.queue_name == "audit-pipeline"
    assert execute_audit_pipeline.options["max_retries"] == 0
    assert execute_audit_pipeline.options["time_limit"] == (
        get_settings().DRAMATIQ_AUDIT_PIPELINE_TIME_LIMIT_SECONDS * 1000
    )


def test_enqueue_audit_pipeline_sends_serializable_values(monkeypatch) -> None:
    task_id = uuid4()
    user_id = uuid4()
    send = Mock(return_value=SimpleNamespace(message_id="message-id"))
    monkeypatch.setattr(execute_audit_pipeline, "send", send)

    message_id = asyncio.run(
        enqueue_audit_pipeline(
            task_id=task_id,
            user_id=user_id,
            request_id="request-id",
        )
    )

    assert message_id == "message-id"
    send.assert_called_once_with(str(task_id), str(user_id), "request-id")


def test_enqueue_audit_pipeline_propagates_broker_failure(monkeypatch) -> None:
    send = Mock(side_effect=ConnectionError("redis unavailable"))
    monkeypatch.setattr(execute_audit_pipeline, "send", send)

    with pytest.raises(ConnectionError, match="redis unavailable"):
        asyncio.run(
            enqueue_audit_pipeline(
                task_id=uuid4(),
                user_id=uuid4(),
                request_id="request-id",
            )
        )


def test_regulation_actor_uses_configured_broker() -> None:
    assert execute_regulation_pipeline.broker is task_broker
    assert execute_regulation_pipeline.queue_name == "regulation-pipeline"
    assert execute_regulation_pipeline.options["max_retries"] == 0
    assert execute_regulation_pipeline.options["time_limit"] == (
        get_settings().DRAMATIQ_REGULATION_PIPELINE_TIME_LIMIT_SECONDS * 1000
    )


def test_enqueue_regulation_pipeline_sends_serializable_values(monkeypatch) -> None:
    regulation_id = uuid4()
    user_id = uuid4()
    send = Mock(return_value=SimpleNamespace(message_id="regulation-message-id"))
    monkeypatch.setattr(execute_regulation_pipeline, "send", send)

    message_id = asyncio.run(
        enqueue_regulation_pipeline(
            regulation_id=regulation_id,
            user_id=user_id,
            request_id="request-id",
        )
    )

    assert message_id == "regulation-message-id"
    send.assert_called_once_with(str(regulation_id), str(user_id), "request-id")
