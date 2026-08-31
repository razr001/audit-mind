import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from threading import Lock
from typing import BinaryIO
from weakref import WeakKeyDictionary

import urllib3
from minio import Minio
from minio.error import S3Error
from minio.helpers import ObjectWriteResult

from app.core.config import get_settings

settings = get_settings()


class MinioClient:
    """把同步 MinIO SDK 包装成不会阻塞 FastAPI 事件循环的异步接口。"""

    def __init__(self):
        http_client = urllib3.PoolManager(
            timeout=urllib3.Timeout(
                connect=settings.MINIO_CONNECT_TIMEOUT_SECONDS,
                read=settings.MINIO_READ_TIMEOUT_SECONDS,
            ),
        )
        self.client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY.get_secret_value(),
            secure=settings.MINIO_SECURE,
            http_client=http_client,
        )
        health_http_client = urllib3.PoolManager(
            timeout=urllib3.Timeout(
                connect=settings.MINIO_HEALTH_TIMEOUT_SECONDS,
                read=settings.MINIO_HEALTH_TIMEOUT_SECONDS,
            ),
            retries=urllib3.Retry(total=0, redirect=0),
        )
        self.health_client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY.get_secret_value(),
            secure=settings.MINIO_SECURE,
            http_client=health_http_client,
        )
        self._ping_tasks: WeakKeyDictionary[
            asyncio.AbstractEventLoop,
            asyncio.Task[bool],
        ] = WeakKeyDictionary()
        self._ping_tasks_lock = Lock()

    async def _ping_once(self) -> bool:
        try:
            await asyncio.to_thread(self.health_client.list_buckets)
            return True
        except Exception:
            # Health responses and logs intentionally omit raw SDK errors because
            # they may contain endpoints, request metadata, or untrusted text.
            return False

    def _finish_ping_task(
        self,
        loop: asyncio.AbstractEventLoop,
        task: asyncio.Task[bool],
    ) -> None:
        with self._ping_tasks_lock:
            if self._ping_tasks.get(loop) is task:
                del self._ping_tasks[loop]

    async def ping(self) -> bool:
        """Probe once per event loop until the underlying sync call finishes."""
        loop = asyncio.get_running_loop()
        with self._ping_tasks_lock:
            task = self._ping_tasks.get(loop)
            if task is None or task.done():
                task = asyncio.create_task(self._ping_once())
                self._ping_tasks[loop] = task
                task.add_done_callback(
                    lambda completed: self._finish_ping_task(loop, completed),
                )
        return await asyncio.shield(task)

    async def ensure_bucket(
        self,
        bucket_name: str,
    ) -> None:
        """启动时确保共享 Bucket 存在，并兼容并发创建。"""
        exists = await asyncio.to_thread(
            self.client.bucket_exists,
            bucket_name,
        )

        if not exists:
            try:
                await asyncio.to_thread(
                    self.client.make_bucket,
                    bucket_name,
                )
            except S3Error as exc:
                # bucket_exists 与 make_bucket 之间可能被另一实例抢先创建。
                if exc.code != "BucketAlreadyOwnedByYou":
                    raise

    async def upload_object(
        self,
        *,
        bucket_name: str,
        object_name: str,
        data: BinaryIO,
        length: int = -1,
        content_type: str = "application/octet-stream",
    ) -> ObjectWriteResult:
        """流式上传已知长度的对象；part_size 控制分片上传粒度。"""

        return await asyncio.to_thread(
            self.client.put_object,
            bucket_name=bucket_name,
            object_name=object_name,
            data=data,
            length=length,
            part_size=10 * 1024 * 1024,
            content_type=content_type,
        )

    async def remove_object(
        self,
        *,
        bucket_name: str,
        object_name: str,
    ) -> None:
        await asyncio.to_thread(
            self.client.remove_object,
            bucket_name,
            object_name,
        )

    async def remove_objects_by_prefix(
        self,
        *,
        bucket_name: str,
        prefix: str,
    ) -> None:
        """删除前缀下的全部对象，用于清理某个业务对象的所有派生资源。"""

        def remove_all() -> None:
            objects = self.client.list_objects(
                bucket_name,
                prefix=prefix,
                recursive=True,
            )
            for item in objects:
                if item.object_name is not None:
                    self.client.remove_object(bucket_name, item.object_name)

        await asyncio.to_thread(remove_all)

    async def presigned_get_object(
        self,
        *,
        bucket_name: str,
        object_name: str,
        expires: timedelta,
    ) -> str:
        return await asyncio.to_thread(
            self.client.presigned_get_object,
            bucket_name=bucket_name,
            object_name=object_name,
            expires=expires,
        )

    async def stream_object(
        self,
        *,
        bucket_name: str,
        object_name: str,
        chunk_size: int = 1024 * 1024,
    ) -> AsyncIterator[bytes]:
        """按块读取对象，并保证响应和底层 HTTP 连接最终被释放。"""
        response = await asyncio.to_thread(
            self.client.get_object,
            bucket_name,
            object_name,
        )

        try:
            while True:
                chunk = await asyncio.to_thread(
                    response.read,
                    chunk_size,
                )

                if not chunk:
                    break

                yield chunk
        finally:
            # MinIO 返回的 response 必须同时 close 和 release_conn，
            # 否则连接池会逐渐耗尽。
            await asyncio.to_thread(response.close)
            await asyncio.to_thread(response.release_conn)


minio_client: MinioClient = MinioClient()
