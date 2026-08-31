from collections.abc import Awaitable, Callable
from typing import Any

from starlette.datastructures import MutableHeaders

SECURITY_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
    "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


class SecurityHeadersMiddleware:
    """Apply fail-closed headers to every API response, including streams."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_RESPONSE_HEADERS.items():
                    if name == "Cache-Control" and "no-transform" in headers.get(name, "").lower():
                        # Streaming endpoints must retain no-transform so a proxy
                        # cannot buffer or rewrite event boundaries.
                        headers[name] = "no-store, no-transform"
                    else:
                        headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_security_headers)
