import asyncio
from uuid import uuid4

from fastapi import Request
from structlog.contextvars import get_contextvars

from app.core.request_context import bind_current_user
from app.schemas.auth import CurrentUser


def test_authenticated_request_binds_and_restores_log_user_context():
    user = CurrentUser(user_id=uuid4(), username="alice")

    async def scenario():
        original_context = get_contextvars()
        request = Request({"type": "http", "state": {}})
        dependency = bind_current_user(request, user)
        await anext(dependency)
        try:
            bound_context = get_contextvars()
            assert bound_context["user_id"] == str(user.user_id)
            assert "username" not in bound_context
            assert request.state.user_id == str(user.user_id)
        finally:
            await dependency.aclose()

        assert get_contextvars() == original_context

    asyncio.run(scenario())
