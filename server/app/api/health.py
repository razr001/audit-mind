import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Any
from weakref import WeakKeyDictionary

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.infrastructure.db.health import ping_database
from app.infrastructure.es_client import es_client
from app.infrastructure.minio_client import minio_client
from app.infrastructure.redis_client import redis_client
from app.schemas.health import HealthDependencies, HealthResponse

router = APIRouter()
settings = get_settings()
HEALTH_CHECK_TIMEOUT_SECONDS = settings.HEALTH_CHECK_TIMEOUT_SECONDS
HEALTH_CACHE_TTL_SECONDS = settings.HEALTH_CACHE_TTL_SECONDS


@dataclass
class _LoopHealthState:
    cached_health: HealthResponse | None = None
    cache_expires_at: float = 0.0
    health_task: asyncio.Task[HealthResponse] | None = None


_loop_states: WeakKeyDictionary[
    asyncio.AbstractEventLoop,
    _LoopHealthState,
] = WeakKeyDictionary()
_loop_states_lock = Lock()


def _current_loop_state() -> _LoopHealthState:
    loop = asyncio.get_running_loop()
    with _loop_states_lock:
        state = _loop_states.get(loop)
        if state is None:
            state = _LoopHealthState()
            _loop_states[loop] = state
        return state


def _finish_health_task(
    state: _LoopHealthState,
    task: asyncio.Task[HealthResponse],
) -> None:
    if state.health_task is not task:
        return
    if not task.cancelled() and task.exception() is None:
        state.cached_health = task.result()
        state.cache_expires_at = monotonic() + HEALTH_CACHE_TTL_SECONDS
    state.health_task = None


async def _collect_health() -> HealthResponse:
    async def check(call: Callable[[], Awaitable[Any]]) -> bool:
        try:
            result = await asyncio.wait_for(
                call(),
                timeout=HEALTH_CHECK_TIMEOUT_SECONDS,
            )
            return result is not False
        except Exception:
            return False

    database_ok, redis_ok, es_ok, minio_ok = await asyncio.gather(
        check(ping_database),
        check(redis_client.ping),
        check(es_client.ping),
        check(minio_client.ping),
    )
    dependencies = HealthDependencies(
        postgresql=database_ok,
        redis=redis_ok,
        elasticsearch=es_ok,
        minio=minio_ok,
    )
    healthy = all(
        (
            dependencies.postgresql,
            dependencies.redis,
            dependencies.elasticsearch,
            dependencies.minio,
        )
    )
    return HealthResponse(
        status="UP" if healthy else "DOWN",
        dependencies=dependencies,
    )


async def _health_snapshot() -> HealthResponse:
    """Return a short-lived snapshot and single-flight concurrent probes."""
    state = _current_loop_state()
    if state.cached_health is not None and monotonic() < state.cache_expires_at:
        return state.cached_health
    if state.health_task is None or state.health_task.done():
        state.health_task = asyncio.create_task(_collect_health())
        state.health_task.add_done_callback(
            lambda completed: _finish_health_task(state, completed),
        )
    task = state.health_task
    return await asyncio.shield(task)


def reset_health_cache() -> None:
    """Reset process-local probe state for deterministic tests."""
    with _loop_states_lock:
        states = list(_loop_states.values())
        _loop_states.clear()
    for state in states:
        task = state.health_task
        if task is None or task.done():
            continue
        loop = task.get_loop()
        if loop.is_running():
            loop.call_soon_threadsafe(task.cancel)
        else:
            task.cancel()


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse}},
)
async def health() -> JSONResponse:
    payload = await _health_snapshot()
    return JSONResponse(
        status_code=200 if payload.status == "UP" else 503,
        content=payload.model_dump(mode="json", by_alias=True),
    )
