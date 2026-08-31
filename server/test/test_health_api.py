import asyncio
import gc
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import health as health_api
from app.infrastructure.minio_client import minio_client
from app.lifespan import lifespan
from app.main import create_app


def make_client() -> TestClient:
    settings = type(
        "HealthTestSettings",
        (),
        {"APP_NAME": "AuditMind Test", "CORS_ALLOWED_ORIGINS": []},
    )()
    return TestClient(create_app(settings=settings))


def test_health_returns_typed_dependency_snapshot() -> None:
    health_api.reset_health_cache()
    with (
        patch("app.api.health.ping_database", new=AsyncMock(return_value=True)),
        patch("app.api.health.redis_client.ping", new=AsyncMock(return_value=True)),
        patch("app.api.health.es_client.ping", new=AsyncMock(return_value=True)),
        patch("app.api.health.minio_client.ping", new=AsyncMock(return_value=True)),
    ):
        response = make_client().get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "UP",
        "dependencies": {
            "postgresql": True,
            "redis": True,
            "elasticsearch": True,
            "minio": True,
        },
    }


def test_health_contract_documents_success_and_degraded_responses() -> None:
    schema = make_client().get("/openapi.json").json()
    responses = schema["paths"]["/health"]["get"]["responses"]

    assert responses["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HealthResponse"
    }
    assert responses["503"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HealthResponse"
    }


def test_health_times_out_a_pending_dependency(
    monkeypatch,
) -> None:
    async def pending() -> bool:
        await asyncio.Event().wait()
        return True

    async def scenario():
        health_api.reset_health_cache()
        monkeypatch.setattr(health_api, "HEALTH_CHECK_TIMEOUT_SECONDS", 0.01)
        with (
            patch("app.api.health.ping_database", new=pending),
            patch("app.api.health.redis_client.ping", new=AsyncMock(return_value=True)),
            patch("app.api.health.es_client.ping", new=AsyncMock(return_value=True)),
            patch("app.api.health.minio_client.ping", new=AsyncMock(return_value=True)),
        ):
            response = await asyncio.wait_for(health_api.health(), timeout=0.2)
        assert response.status_code == 503

    asyncio.run(scenario())
    health_api.reset_health_cache()


def test_concurrent_health_requests_share_one_dependency_fanout(
    monkeypatch,
) -> None:
    async def scenario():
        health_api.reset_health_cache()
        monkeypatch.setattr(health_api, "HEALTH_CACHE_TTL_SECONDS", 0.0)
        gate = asyncio.Event()
        calls = 0

        async def probe() -> bool:
            nonlocal calls
            calls += 1
            await gate.wait()
            return True

        mocks = [AsyncMock(side_effect=probe) for _ in range(4)]
        with (
            patch("app.api.health.ping_database", new=mocks[0]),
            patch("app.api.health.redis_client.ping", new=mocks[1]),
            patch("app.api.health.es_client.ping", new=mocks[2]),
            patch("app.api.health.minio_client.ping", new=mocks[3]),
        ):
            requests = [asyncio.create_task(health_api.health()) for _ in range(3)]
            await asyncio.sleep(0)
            gate.set()
            responses = await asyncio.gather(*requests)

        assert calls == 4
        assert all(response.status_code == 200 for response in responses)

    asyncio.run(scenario())
    health_api.reset_health_cache()


def test_minio_ping_does_not_print_raw_exception(capsys) -> None:
    with patch.object(
        minio_client.health_client,
        "list_buckets",
        side_effect=RuntimeError("secret-endpoint\r\ninjected"),
    ):
        assert asyncio.run(minio_client.ping()) is False

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_minio_probe_is_not_reentered_after_outer_timeout() -> None:
    release = Event()
    calls = 0

    def blocking_probe():
        nonlocal calls
        calls += 1
        release.wait(timeout=1)
        return []

    async def scenario():
        with patch.object(
            minio_client.health_client,
            "list_buckets",
            side_effect=blocking_probe,
        ):
            for _ in range(2):
                try:
                    await asyncio.wait_for(minio_client.ping(), timeout=0.01)
                except TimeoutError:
                    pass
            assert calls == 1
            release.set()
            assert await asyncio.wait_for(minio_client.ping(), timeout=0.2)
            await asyncio.sleep(0)
            assert len(minio_client._ping_tasks) == 0

    try:
        asyncio.run(scenario())
    finally:
        release.set()


def test_overlapping_event_loops_do_not_share_asyncio_tasks() -> None:
    release = Event()
    lock = Lock()
    calls = 0

    async def probe() -> bool:
        nonlocal calls
        with lock:
            calls += 1
        await asyncio.to_thread(release.wait, 1)
        return True

    def request_health() -> int:
        return asyncio.run(health_api.health()).status_code

    health_api.reset_health_cache()
    with (
        patch("app.api.health.ping_database", new=probe),
        patch("app.api.health.redis_client.ping", new=probe),
        patch("app.api.health.es_client.ping", new=probe),
        patch("app.api.health.minio_client.ping", new=probe),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        futures = [executor.submit(request_health) for _ in range(2)]
        deadline = monotonic() + 0.5
        while calls < 8 and monotonic() < deadline:
            sleep(0.005)
        release.set()
        assert [future.result(timeout=1) for future in futures] == [200, 200]
    assert calls == 8
    health_api.reset_health_cache()


def test_lifespan_preserves_primary_and_cleanup_errors() -> None:
    async def scenario():
        with (
            patch("app.lifespan.get_settings", return_value=SimpleNamespace(MINIO_BUCKET="test")),
            patch("app.lifespan.ping_database", new=AsyncMock(return_value=True)),
            patch("app.lifespan.redis_client.ping", new=AsyncMock(return_value=True)),
            patch(
                "app.lifespan.redis_client.close",
                new=AsyncMock(side_effect=RuntimeError("redis close")),
            ),
            patch("app.lifespan.es_client.ping", new=AsyncMock(return_value=True)),
            patch(
                "app.lifespan.es_client.close", new=AsyncMock(side_effect=RuntimeError("es close"))
            ),
            patch("app.lifespan.minio_client.ping", new=AsyncMock(return_value=True)),
            patch("app.lifespan.minio_client.ensure_bucket", new=AsyncMock()),
            patch("app.lifespan.agent_checkpointer.initialize", new=AsyncMock()),
            patch("app.lifespan.agent_checkpointer.close", new=AsyncMock()),
            patch("app.lifespan.engine", new=SimpleNamespace(dispose=AsyncMock())),
        ):
            try:
                async with lifespan(FastAPI()):
                    raise ValueError("primary failure")
            except BaseExceptionGroup as group:
                assert [str(error) for error in group.exceptions] == [
                    "primary failure",
                    "redis close",
                    "es close",
                ]
            else:
                raise AssertionError("combined lifecycle failure was not raised")

    asyncio.run(scenario())


def test_lifespan_finishes_cleanup_when_cancelled_during_close() -> None:
    close_started = asyncio.Event()
    release_close = asyncio.Event()
    engine_dispose = AsyncMock()

    async def slow_close() -> None:
        close_started.set()
        await release_close.wait()

    async def scenario():
        with (
            patch("app.lifespan.get_settings", return_value=SimpleNamespace(MINIO_BUCKET="test")),
            patch("app.lifespan.ping_database", new=AsyncMock(return_value=True)),
            patch("app.lifespan.redis_client.ping", new=AsyncMock(return_value=True)),
            patch("app.lifespan.redis_client.close", new=slow_close),
            patch("app.lifespan.es_client.ping", new=AsyncMock(return_value=True)),
            patch("app.lifespan.es_client.close", new=AsyncMock()),
            patch("app.lifespan.minio_client.ping", new=AsyncMock(return_value=True)),
            patch("app.lifespan.minio_client.ensure_bucket", new=AsyncMock()),
            patch("app.lifespan.agent_checkpointer.initialize", new=AsyncMock()),
            patch("app.lifespan.agent_checkpointer.close", new=AsyncMock()),
            patch("app.lifespan.engine", new=SimpleNamespace(dispose=engine_dispose)),
        ):

            async def run_lifespan():
                async with lifespan(FastAPI()):
                    pass

            task = asyncio.create_task(run_lifespan())
            await close_started.wait()
            task.cancel()
            await asyncio.sleep(0)
            release_close.set()
            try:
                await task
            except asyncio.CancelledError:
                pass
        engine_dispose.assert_awaited_once()

    asyncio.run(scenario())


def test_cancelled_health_waiter_commits_cache_and_releases_loop() -> None:
    release = Event()

    async def probe() -> bool:
        await asyncio.to_thread(release.wait, 1)
        return True

    async def scenario():
        with (
            patch("app.api.health.ping_database", new=probe),
            patch("app.api.health.redis_client.ping", new=probe),
            patch("app.api.health.es_client.ping", new=probe),
            patch("app.api.health.minio_client.ping", new=probe),
        ):
            waiter = asyncio.create_task(health_api.health())
            await asyncio.sleep(0)
            waiter.cancel()
            try:
                await waiter
            except asyncio.CancelledError:
                pass
            release.set()
            await asyncio.sleep(0.05)
            state = health_api._current_loop_state()
            assert state.health_task is None
            assert state.cached_health is not None
            assert state.cached_health.status == "UP"

    health_api.reset_health_cache()
    asyncio.run(scenario())
    gc.collect()
    assert len(health_api._loop_states) == 0
