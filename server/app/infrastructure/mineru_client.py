import asyncio
from collections.abc import AsyncIterable
from typing import Any, BinaryIO

import aiohttp

from app.core.config import get_settings
from app.infrastructure.http_client import AsyncHttpClient, outbound_http_client
from app.infrastructure.mineru_cloud_client import (
    MinerUCloudClient,
    MinerUTransientError,
    mineru_http_error_type,
)


class SizedAsyncIterablePayload(aiohttp.payload.AsyncIterablePayload):
    """为异步文件流补充确定长度，使 multipart 能生成正确请求体。"""

    def __init__(
        self,
        value: AsyncIterable[bytes],
        *,
        size: int,
        content_type: str,
    ) -> None:
        super().__init__(
            value,
            content_type=content_type,
        )
        self._size = size


settings = get_settings()


class MinerUClient:
    """封装 MinerU 的任务创建、状态查询和结果获取接口。"""

    def __init__(
        self,
        *,
        http_client: AsyncHttpClient = outbound_http_client,
    ) -> None:
        self.provider = settings.MINERU_PROVIDER
        self.base_url = settings.MINERU_BASE_URL.rstrip("/")
        self.http_client = http_client
        self.status_timeout = aiohttp.ClientTimeout(
            total=settings.MINERU_STATUS_TIMEOUT_SECONDS,
            connect=settings.MINERU_CONNECT_TIMEOUT_SECONDS,
        )
        self.stream_timeout = aiohttp.ClientTimeout(
            total=None,
            connect=settings.MINERU_CONNECT_TIMEOUT_SECONDS,
            sock_read=settings.MINERU_STREAM_IDLE_TIMEOUT_SECONDS,
        )
        self.cloud = MinerUCloudClient(
            http_client=http_client,
            status_timeout=self.status_timeout,
            stream_timeout=self.stream_timeout,
            download_zip=self._download_zip,
        )

    async def create_task(
        self,
        *,
        filename: str,
        content: AsyncIterable[bytes],
        content_type: str,
        content_length: int,
        backend: str,
        server_url: str | None,
        effort: str,
        parse_method: str,
        formula_enable: bool,
        table_enable: bool,
        image_analysis: bool,
        return_images: bool = False,
        response_format_zip: bool = False,
    ) -> str:
        """把 MinIO 文件流直接转发给 MinerU，并返回任务 ID。"""
        if self.provider == "cloud":
            return await self.cloud.create_task(
                filename=filename,
                content=content,
                content_length=content_length,
                parse_method=parse_method,
                formula_enable=formula_enable,
                table_enable=table_enable,
            )

        # 禁止 aiohttp 给字段名加引号，以兼容 MinerU multipart 解析器，
        # 同时保留中文文件名。
        form = aiohttp.FormData(quote_fields=False)
        # 流式转发时仍提供文件长度，避免 aiohttp 使用 MinerU 不兼容的
        # chunked multipart 编码。
        file_payload = SizedAsyncIterablePayload(
            content,
            size=content_length,
            content_type=content_type,
        )
        # filename 和 content_type 让该字段以真正的文件 part 发送，
        # 而不是普通表单字符串。
        form.add_field(
            "files",
            file_payload,
            filename=filename,
            content_type=content_type,
        )
        form.add_field("return_md", "true")
        form.add_field("return_content_list", "true")
        # 显式传递解析策略，避免 MinerU 服务端默认值升级后改变解析效果。
        form.add_field("backend", backend)
        # http-client 后端由轻量 API 容器转发到独立的 GPU 模型服务。
        if backend.endswith("-http-client") and server_url:
            form.add_field("server_url", server_url)
        form.add_field("effort", effort)
        form.add_field("parse_method", parse_method)
        form.add_field(
            "formula_enable",
            str(formula_enable).lower(),
        )
        form.add_field(
            "table_enable",
            str(table_enable).lower(),
        )
        form.add_field(
            "image_analysis",
            str(image_analysis).lower(),
        )
        form.add_field(
            "return_images",
            str(return_images).lower(),
        )
        form.add_field(
            "response_format_zip",
            str(response_format_zip).lower(),
        )

        session = await self.http_client.get_session()
        async with session.post(
            f"{self.base_url}/tasks",
            data=form,
            timeout=self.stream_timeout,
        ) as response:
            payload = await self._read_response(response)

        task_id = payload.get("task_id")

        if task_id is None and isinstance(payload.get("data"), dict):
            task_id = payload["data"].get("task_id")

        if not task_id:
            raise RuntimeError(f"MinerU response does not contain task_id: {payload}")

        return str(task_id)

    async def get_task(self, task_id: str) -> dict[str, Any]:
        """查询任务当前状态，不获取体积较大的完整解析结果。"""
        if self._is_cloud_task(task_id):
            return await self.cloud.get_task(task_id)

        session = await self.http_client.get_session()
        async with session.get(
            f"{self.base_url}/tasks/{task_id}",
            timeout=self.status_timeout,
        ) as response:
            return await self._read_response(response)

    async def get_task_result(self, task_id: str) -> dict[str, Any]:
        """任务完成后获取 Markdown、content_list 等完整结果。"""
        if self._is_cloud_task(task_id):
            return await self.cloud.get_task_result(task_id)

        session = await self.http_client.get_session()
        async with session.get(
            f"{self.base_url}/tasks/{task_id}/result",
            timeout=self.stream_timeout,
        ) as response:
            return await self._read_response(response)

    async def download_task_result_zip(
        self,
        *,
        task_id: str,
        destination: BinaryIO,
        max_bytes: int,
    ) -> int:
        """把 MinerU ZIP 结果流式写入文件，并限制压缩包下载大小。"""
        if self._is_cloud_task(task_id):
            return await self.cloud.download_task_result_zip(
                task_id=task_id,
                destination=destination,
                max_bytes=max_bytes,
            )

        return await self._download_zip(
            url=f"{self.base_url}/tasks/{task_id}/result",
            destination=destination,
            max_bytes=max_bytes,
        )

    async def _download_zip(
        self,
        *,
        url: str,
        destination: BinaryIO,
        max_bytes: int,
    ) -> int:
        session = await self.http_client.get_session()
        async with session.get(
            url,
            timeout=self.stream_timeout,
        ) as response:
            if response.status >= 400:
                payload = await response.text()
                error_type = mineru_http_error_type(response.status)
                raise error_type(
                    "MinerU result download failed: "
                    f"status={response.status}, body={payload[:500]}"
                )

            content_length = response.content_length
            if content_length is not None and content_length > max_bytes:
                raise RuntimeError("MinerU result archive is too large")

            total_size = 0
            async for chunk in response.content.iter_chunked(1024 * 1024):
                total_size += len(chunk)
                if total_size > max_bytes:
                    raise RuntimeError("MinerU result archive is too large")
                # 临时文件是同步文件对象，写入放在线程中避免阻塞事件循环。
                await asyncio.to_thread(destination.write, chunk)

        await asyncio.to_thread(destination.flush)
        await asyncio.to_thread(destination.seek, 0)
        return total_size

    @staticmethod
    def _is_cloud_task(task_id: str) -> bool:
        return task_id.startswith("cloud:")

    @staticmethod
    async def _read_response(
        response: aiohttp.ClientResponse,
    ) -> dict[str, Any]:
        """统一验证 MinerU HTTP 状态及 JSON 响应结构。"""
        try:
            payload = await response.json()
        except aiohttp.ContentTypeError:
            content = await response.text()
            raise RuntimeError(
                f"MinerU returned a non-JSON response: "
                f"status={response.status}, body={content[:500]}"
            )

        if response.status >= 400:
            if response.status in {408, 429} or response.status >= 500:
                raise MinerUTransientError(
                    f"MinerU request temporarily failed: status={response.status}"
                )
            raise RuntimeError(
                f"MinerU request failed: status={response.status}, body={payload}"
            )

        if not isinstance(payload, dict):
            raise RuntimeError(f"Invalid MinerU response: {payload}")

        return payload


mineru_client = MinerUClient()
