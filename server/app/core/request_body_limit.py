from collections.abc import Mapping

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.error_codes import FILE_TOO_LARGE


class _RequestBodyTooLarge(BaseException):
    """绕过应用异常处理，由最外层请求体中间件生成 413。"""


class RequestBodyLimitMiddleware:
    """在框架解析 JSON 或 multipart 字段前限制所有请求的原始请求体。"""

    def __init__(
        self,
        app: ASGIApp,
        *,
        default_limit: int,
        limits: Mapping[str, int] | None = None,
    ) -> None:
        if default_limit <= 0:
            raise ValueError("default request body limit must be greater than zero")
        if any(limit <= 0 for limit in (limits or {}).values()):
            raise ValueError("request body limits must be greater than zero")
        self.app = app
        self.default_limit = default_limit
        self.limits = dict(limits or {})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.limits.get(scope["path"], self.default_limit)

        headers = dict(scope.get("headers", []))
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                if int(raw_length) > limit:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                await self._reject(scope, receive, send)
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        await JSONResponse(
            status_code=413,
            content={
                "code": FILE_TOO_LARGE,
                "message": "request body is too large",
                "data": None,
                "request_id": scope.get("state", {}).get("request_id"),
            },
        )(scope, receive, send)
