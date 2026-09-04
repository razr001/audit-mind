import asyncio

import pytest

from app.infrastructure.mineru_client import MinerUClient, MinerUTransientError


class FakeResponse:
    def __init__(self, payload: dict | None = None, *, status: int = 200) -> None:
        self.payload = payload or {"status": "processing"}
        self.status = status

    async def json(self):
        return self.payload


class FakeRequestContext:
    def __init__(self, response: FakeResponse | None = None) -> None:
        self.response = response or FakeResponse()

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeSession:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, object]] = []
        self.posted_data = None

    def get(self, url: str, *, timeout):
        self.requests.append(("GET", url, timeout))
        return FakeRequestContext()

    def post(self, url: str, *, data, timeout):
        self.requests.append(("POST", url, timeout))
        self.posted_data = data
        return FakeRequestContext(FakeResponse({"task_id": "task-upload"}))


class FakeHttpClient:
    def __init__(self) -> None:
        self.session = FakeSession()
        self.get_session_count = 0

    async def get_session(self):
        self.get_session_count += 1
        return self.session


def test_mineru_reuses_application_http_session_and_uses_status_timeout():
    """连续轮询必须复用连接池，并使用有界的短状态查询超时。"""
    http_client = FakeHttpClient()
    client = MinerUClient(http_client=http_client)

    async def run_test() -> None:
        await client.get_task("task-1")
        await client.get_task("task-2")

    asyncio.run(run_test())

    assert http_client.get_session_count == 2
    assert len(http_client.session.requests) == 2
    assert http_client.session.requests[0][2] is client.status_timeout
    assert http_client.session.requests[1][2] is client.status_timeout


def test_mineru_stream_timeout_does_not_limit_total_transfer_duration():
    """大文件上传和结果下载只限制连接及空闲读取，不限制传输总时长。"""
    client = MinerUClient(http_client=FakeHttpClient())

    assert client.stream_timeout.total is None
    assert client.stream_timeout.connect is not None
    assert client.stream_timeout.sock_read is not None


def test_mineru_upload_and_result_requests_use_stream_timeout():
    http_client = FakeHttpClient()
    client = MinerUClient(http_client=http_client)

    async def content():
        yield b"%PDF-test"

    async def run_test() -> None:
        task_id = await client.create_task(
            filename="test.pdf",
            content=content(),
            content_type="application/pdf",
            content_length=9,
            backend="pipeline",
            server_url=None,
            effort="medium",
            parse_method="auto",
            formula_enable=True,
            table_enable=True,
            image_analysis=False,
        )
        assert task_id == "task-upload"
        await client.get_task_result(task_id)

    asyncio.run(run_test())

    assert http_client.session.requests[0][0] == "POST"
    assert http_client.session.requests[0][2] is client.stream_timeout
    assert http_client.session.requests[1][0] == "GET"
    assert http_client.session.requests[1][2] is client.stream_timeout


def test_pipeline_upload_omits_irrelevant_server_url():
    http_client = FakeHttpClient()
    client = MinerUClient(http_client=http_client)

    async def content():
        yield b"%PDF-test"

    asyncio.run(
        client.create_task(
            filename="test.pdf",
            content=content(),
            content_type="application/pdf",
            content_length=9,
            backend="pipeline",
            server_url="http://host.docker.internal:30000",
            effort="medium",
            parse_method="auto",
            formula_enable=True,
            table_enable=True,
            image_analysis=False,
        )
    )

    field_names = {
        field[0]["name"] for field in http_client.session.posted_data._fields
    }
    assert "server_url" not in field_names


def test_mineru_5xx_response_is_classified_as_transient():
    async def run_test() -> None:
        with pytest.raises(MinerUTransientError):
            await MinerUClient._read_response(
                FakeResponse({"message": "bad gateway"}, status=502)
            )

    asyncio.run(run_test())
