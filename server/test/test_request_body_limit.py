import asyncio

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.error_codes import FILE_TOO_LARGE
from app.core.request_body_limit import RequestBodyLimitMiddleware
from app.core.security_headers import SecurityHeadersMiddleware


def make_app(limit: int) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        RequestBodyLimitMiddleware,
        default_limit=limit * 2,
        limits={"/text": limit},
    )
    app.add_middleware(SecurityHeadersMiddleware)

    @app.post("/text")
    async def receive_text(request: Request):
        return {"size": len(await request.body())}

    return app


def test_request_body_limit_rejects_declared_oversized_body() -> None:
    response = TestClient(make_app(10)).post("/text", content=b"01234567890")

    assert response.status_code == 413
    assert response.json()["code"] == FILE_TOO_LARGE
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_request_body_limit_allows_body_within_limit() -> None:
    response = TestClient(make_app(10)).post("/text", content=b"0123456789")

    assert response.status_code == 200
    assert response.json() == {"size": 10}


def test_default_request_body_limit_covers_unlisted_routes() -> None:
    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware, default_limit=10)

    @app.post("/other")
    async def receive_other(request: Request):
        return {"size": len(await request.body())}

    response = TestClient(app).post("/other", content=b"01234567890")

    assert response.status_code == 413
    assert response.json()["code"] == FILE_TOO_LARGE


def test_default_limit_counts_streamed_body_without_content_length() -> None:
    """分块传输不能通过省略 Content-Length 绕过全局限制。"""

    async def run_test() -> None:
        async def consume_body(scope, receive, send) -> None:
            while True:
                message = await receive()
                if not message.get("more_body", False):
                    break

        middleware = RequestBodyLimitMiddleware(
            consume_body,
            default_limit=5,
        )
        incoming = [
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False},
        ]
        outgoing = []

        async def receive():
            return incoming.pop(0)

        async def send(message):
            outgoing.append(message)

        await middleware(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/chunked",
                "raw_path": b"/chunked",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 1234),
                "server": ("testserver", 80),
                "state": {},
            },
            receive,
            send,
        )

        assert outgoing[0]["type"] == "http.response.start"
        assert outgoing[0]["status"] == 413

    asyncio.run(run_test())
