import asyncio
from collections.abc import Awaitable
from contextlib import asynccontextmanager, suppress
from time import monotonic
from typing import TypeVar
from uuid import uuid4

from app.core.logger import logger
from app.infrastructure.redis_client import RedisClient, redis_client

_RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""

_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

T = TypeVar("T")


class RedisLeaseLostError(RuntimeError):
    """当前执行者已失去分布式租约，必须停止产生业务副作用。"""


class RedisLease:
    """带唯一所有权 token 和自动续租的 Redis 分布式锁。"""

    def __init__(
        self,
        *,
        client: RedisClient,
        key: str,
        ttl_seconds: int,
        max_hold_seconds: int | None = None,
    ) -> None:
        if ttl_seconds < 3:
            raise ValueError("Redis lease TTL must be at least 3 seconds")
        self.client = client
        self.key = key
        self.ttl_seconds = ttl_seconds
        self.max_hold_seconds = max_hold_seconds
        self.token = uuid4().hex
        self._renew_task: asyncio.Task[None] | None = None
        self._lost_event = asyncio.Event()
        self._lost_reason: str | None = None
        self._acquired_at: float | None = None
        self._last_successful_renewal: float | None = None

    async def acquire(self) -> bool:
        """非阻塞抢锁；成功后启动续租任务。"""
        acquired = await self.client.client.set(
            self.key,
            self.token,
            nx=True,
            ex=self.ttl_seconds,
        )
        if not acquired:
            return False

        now = monotonic()
        self._acquired_at = now
        self._last_successful_renewal = now
        self._renew_task = asyncio.create_task(self._renew_loop())
        return True

    async def release(self) -> None:
        """停止续租，并且只删除仍属于当前请求的锁。"""
        if self._renew_task is not None:
            self._renew_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._renew_task
            self._renew_task = None

        await self.client.client.eval(
            _RELEASE_SCRIPT,
            1,
            self.key,
            self.token,
        )

    async def is_owned(self) -> bool:
        """确认租约仍属于当前任务，并容忍安全窗口内的 Redis 短暂抖动。"""
        if self._lost_event.is_set():
            return False
        now = monotonic()
        if (
            self.max_hold_seconds is not None
            and self._acquired_at is not None
            and now - self._acquired_at >= self.max_hold_seconds
        ):
            self._mark_lost("max_hold_exceeded")
            return False
        try:
            owned = await self.client.client.get(self.key) == self.token
        except Exception as exc:
            # 最近一次续租仍在完整 TTL 内时，Redis 短暂不可达不代表租约已经
            # 丢失。这个容错只保护已完成的昂贵计算，不会越过 TTL 或最长持有期。
            last_renewed = self._last_successful_renewal
            if last_renewed is not None and now - last_renewed < self.ttl_seconds:
                logger.warning(
                    "redis_lease_ownership_check_failed",
                    lock_key=self.key,
                    error_type=type(exc).__name__,
                )
                return True
            self._mark_lost("ownership_check_deadline_exceeded")
            return False
        if not owned:
            self._mark_lost("ownership_changed_or_expired")
        return owned

    async def run_guarded(self, awaitable: Awaitable[T]) -> T:
        """运行长任务；租约失效时取消旧执行者，避免它继续写外部系统。"""
        work_task = asyncio.ensure_future(awaitable)
        lost_task = asyncio.create_task(self._lost_event.wait())
        try:
            done, _ = await asyncio.wait(
                {work_task, lost_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            # 两个任务可能在同一轮事件循环内同时完成。只要确认失锁，
            # 就不能再把业务结果当作当前执行者的有效结果。
            if lost_task in done and self._lost_event.is_set():
                work_task.cancel()
                with suppress(asyncio.CancelledError):
                    await work_task
                raise RedisLeaseLostError(
                    f"Redis lease lost for {self.key}: {self._lost_reason or 'unknown'}"
                )
            return await work_task
        finally:
            lost_task.cancel()
            with suppress(asyncio.CancelledError):
                await lost_task
            # run_guarded 本身被 worker 超时、服务关闭等外层取消时，不能留下
            # 已脱离租约上下文的业务任务继续写数据库或外部系统。
            if not work_task.done():
                work_task.cancel()
                with suppress(asyncio.CancelledError):
                    await work_task

    def _mark_lost(self, reason: str) -> None:
        if self._lost_event.is_set():
            return
        self._lost_reason = reason
        self._lost_event.set()
        logger.error(
            "redis_lease_lost",
            lock_key=self.key,
            reason=reason,
        )

    async def _renew_loop(self) -> None:
        """在 TTL 的三分之一处续租，覆盖大型文件和慢视觉模型。"""
        interval = max(1, self.ttl_seconds // 3)
        while True:
            await asyncio.sleep(interval)
            now = monotonic()
            if (
                self.max_hold_seconds is not None
                and self._acquired_at is not None
                and now - self._acquired_at >= self.max_hold_seconds
            ):
                self._mark_lost("max_hold_exceeded")
                return
            try:
                renewed = await self.client.client.eval(
                    _RENEW_SCRIPT,
                    1,
                    self.key,
                    self.token,
                    self.ttl_seconds,
                )
                if not renewed:
                    self._mark_lost("ownership_changed_or_expired")
                    return
                self._last_successful_renewal = monotonic()
            except Exception as exc:
                # 一次网络抖动不会立即中止任务；但从最后一次成功续租算起
                # 超过完整 TTL 后，Redis 已无法保证独占性，旧执行者必须退出。
                logger.warning(
                    "redis_lease_renew_failed",
                    lock_key=self.key,
                    error_type=type(exc).__name__,
                )
                last_renewed = self._last_successful_renewal
                if last_renewed is None or monotonic() - last_renewed >= self.ttl_seconds:
                    self._mark_lost("renewal_deadline_exceeded")
                    return


async def run_with_lease_guard(lease: RedisLease | object, awaitable: Awaitable[T]) -> T:
    """兼容测试替身，并让生产租约丢失能够中止对应的长任务。"""
    if isinstance(lease, RedisLease):
        return await lease.run_guarded(awaitable)
    return await awaitable


@asynccontextmanager
async def acquire_redis_lease(
    *,
    key: str,
    ttl_seconds: int,
    max_hold_seconds: int | None = None,
    client: RedisClient = redis_client,
):
    """兼容普通调用者，返回是否抢锁成功，并确保退出时安全释放。"""
    lease = RedisLease(
        client=client,
        key=key,
        ttl_seconds=ttl_seconds,
        max_hold_seconds=max_hold_seconds,
    )
    acquired = await lease.acquire()
    try:
        yield acquired
    finally:
        if acquired:
            try:
                await lease.release()
            except Exception as exc:
                # 锁本身带 TTL；释放失败时记录日志，过期后会自动消失。
                logger.warning(
                    "redis_lease_release_failed",
                    error_type=type(exc).__name__,
                )


@asynccontextmanager
async def acquire_redis_lease_handle(
    *,
    key: str,
    ttl_seconds: int,
    max_hold_seconds: int,
    client: RedisClient = redis_client,
):
    """为长流水线返回可监听丢锁事件的租约对象。"""
    lease = RedisLease(
        client=client,
        key=key,
        ttl_seconds=ttl_seconds,
        max_hold_seconds=max_hold_seconds,
    )
    acquired = await lease.acquire()
    try:
        yield lease if acquired else None
    finally:
        if acquired:
            try:
                await lease.release()
            except Exception as exc:
                logger.warning(
                    "redis_lease_release_failed",
                    lock_key=key,
                    error_type=type(exc).__name__,
                )
