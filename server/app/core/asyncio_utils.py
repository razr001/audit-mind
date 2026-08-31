from collections.abc import Awaitable
from typing import TypeVar, cast

import anyio

T = TypeVar("T")
_UNSET = object()


async def await_cancellation_safe(awaitable: Awaitable[T]) -> T:
    """在当前任务内完成必要清理，阻止 AnyIO 重复取消产生孤儿任务。

    这里刻意不创建后台 Task。数据库清理继续使用当前请求的 Session，且调用方
    只有在清理结束后才重新抛出取消，让 FastAPI 随后安全关闭该 Session。
    """

    result: T | object = _UNSET
    with anyio.CancelScope(shield=True):
        result = await awaitable
    if result is _UNSET:
        raise RuntimeError("cancellation-safe operation did not complete")
    return cast(T, result)
