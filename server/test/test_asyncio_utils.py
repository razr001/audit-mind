import asyncio

import anyio

from app.core.asyncio_utils import await_cancellation_safe


def test_cancellation_safe_cleanup_finishes_in_the_request_task() -> None:
    task_ids: list[int] = []
    cleanup_finished = False

    async def scenario() -> None:
        nonlocal cleanup_finished
        request_task = asyncio.current_task()
        assert request_task is not None
        task_ids.append(id(request_task))

        async def cleanup() -> None:
            nonlocal cleanup_finished
            cleanup_task = asyncio.current_task()
            assert cleanup_task is not None
            task_ids.append(id(cleanup_task))
            # AnyIO 的 level cancellation 会在普通 await 上重复投递取消；
            # shield 必须让这个检查点和后续 Session 写入能够完成。
            await anyio.sleep(0)
            cleanup_finished = True

        with anyio.CancelScope() as scope:
            scope.cancel()
            try:
                await anyio.sleep(0)
            except asyncio.CancelledError:
                await await_cancellation_safe(cleanup())
                raise

    anyio.run(scenario)

    assert cleanup_finished is True
    assert task_ids[0] == task_ids[1]
