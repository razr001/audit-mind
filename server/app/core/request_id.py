import re
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.contextvars import bind_contextvars, reset_contextvars

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_MAX_LENGTH = 128
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def resolve_request_id(value: str | None) -> str:
    """接受安全的前端请求 ID，否则生成不可预测的后端 ID。"""
    if value is not None and _REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return uuid4().hex


class RequestIdMiddleware:
    """为整个 HTTP 请求绑定 request_id，并在响应头中原样返回。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied_id = Headers(scope=scope).get(REQUEST_ID_HEADER)
        request_id = resolve_request_id(supplied_id)
        # Request.state 底层读取 scope["state"]，异常处理器也能获得同一个 ID。
        scope.setdefault("state", {})["request_id"] = request_id
        log_tokens = bind_contextvars(request_id=request_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            # 绑定覆盖整个响应和 BackgroundTasks 执行周期，后台日志也会继承。
            await self.app(scope, receive, send_with_request_id)
        finally:
            reset_contextvars(**log_tokens)
