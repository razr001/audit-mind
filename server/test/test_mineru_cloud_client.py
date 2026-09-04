import asyncio
import io
import zipfile
from collections.abc import AsyncIterator
from typing import Any, cast

from app.infrastructure.mineru_client import MinerUClient


class FakeContent:
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def iter_chunked(self, _size: int) -> AsyncIterator[bytes]:
        yield self.body


class FakeResponse:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        status: int = 200,
        body: bytes = b"",
        content_type: str = "application/json",
    ) -> None:
        self.payload = payload or {}
        self.status = status
        self.body = body
        self.headers = {"Content-Type": content_type}
        self.content_length = len(body) if body else None
        self.content = FakeContent(body)

    async def json(self) -> dict[str, Any]:
        return self.payload

    async def text(self) -> str:
        return self.body.decode(errors="replace")


class FakeRequestContext:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    async def __aenter__(self) -> FakeResponse:
        return self.response

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        return False


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, dict]] = []

    def _request(self, method: str, url: str, **kwargs: Any) -> FakeRequestContext:
        self.requests.append((method, url, kwargs))
        return FakeRequestContext(self.responses.pop(0))

    def post(self, url: str, **kwargs: Any) -> FakeRequestContext:
        return self._request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> FakeRequestContext:
        return self._request("PUT", url, **kwargs)

    def get(self, url: str, **kwargs: Any) -> FakeRequestContext:
        return self._request("GET", url, **kwargs)


class FakeHttpClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.session = FakeSession(responses)

    async def get_session(self) -> FakeSession:
        return self.session


def cloud_client(responses: list[FakeResponse]) -> tuple[MinerUClient, FakeSession]:
    http_client = FakeHttpClient(responses)
    client = MinerUClient(http_client=cast(Any, http_client))
    client.provider = "cloud"
    client.cloud.api_base_url = "https://mineru.net"
    client.cloud.api_token = "secret-token"
    client.cloud.model_version = "vlm"
    client.cloud.language = "ch"
    return client, http_client.session


def test_cloud_mineru_creates_signed_upload_and_normalizes_status() -> None:
    client, session = cloud_client(
        [
            FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "batch_id": "batch-1",
                        "file_urls": ["https://upload.example.com/signed"],
                    },
                }
            ),
            FakeResponse(),
            FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {"file_name": "报告.pdf", "state": "running"}
                        ]
                    },
                }
            ),
        ]
    )

    async def content() -> AsyncIterator[bytes]:
        yield b"%PDF-test"

    async def run_test() -> None:
        task_id = await client.create_task(
            filename="报告.pdf",
            content=content(),
            content_type="application/pdf",
            content_length=9,
            backend="pipeline",
            server_url=None,
            effort="high",
            parse_method="ocr",
            formula_enable=True,
            table_enable=False,
            image_analysis=True,
        )
        assert task_id == "cloud:batch-1"
        assert await client.get_task(task_id) == {"status": "processing", "error": None}

    asyncio.run(run_test())

    request_body = session.requests[0][2]["json"]
    assert request_body == {
        "files": [{"name": "报告.pdf", "is_ocr": True}],
        "model_version": "vlm",
        "language": "ch",
        "enable_formula": True,
        "enable_table": False,
    }
    assert session.requests[0][2]["headers"] == {
        "Authorization": "Bearer secret-token"
    }
    assert session.requests[1][0:2] == (
        "PUT",
        "https://upload.example.com/signed",
    )
    assert session.requests[1][2]["headers"] == {"Content-Length": "9"}
    assert "Content-Type" not in session.requests[1][2]["data"].headers


def test_cloud_mineru_converts_result_zip_to_local_result_shape() -> None:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as result_zip:
        result_zip.writestr("报告/报告_content_list.json", '[{"type":"text"}]')
    archive_bytes = archive.getvalue()

    client, _ = cloud_client(
        [
            FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "file_name": "报告.pdf",
                                "state": "done",
                                "full_zip_url": "https://cdn.example.com/result.zip",
                            }
                        ]
                    },
                }
            ),
            FakeResponse(
                body=archive_bytes,
                content_type="application/octet-stream",
            ),
        ]
    )

    result = asyncio.run(client.get_task_result("cloud:batch-1"))

    assert result == {
        "results": {"报告": {"content_list": '[{"type":"text"}]'}}
    }
