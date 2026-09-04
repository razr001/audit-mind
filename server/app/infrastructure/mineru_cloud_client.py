import asyncio
import json
import tempfile
import zipfile
from collections.abc import AsyncIterable, Awaitable, Callable
from pathlib import PurePosixPath
from typing import Any, BinaryIO

import aiohttp

from app.core.config import get_settings
from app.infrastructure.http_client import AsyncHttpClient

settings = get_settings()


class MinerUTransientError(RuntimeError):
    """MinerU 暂时不可用，调用方应保留任务状态并稍后重试。"""


class CloudUploadPayload(aiohttp.payload.AsyncIterablePayload):
    def __init__(self, content: AsyncIterable[bytes], *, size: int) -> None:
        super().__init__(content)
        self.headers.pop(aiohttp.hdrs.CONTENT_TYPE, None)
        self._size = size


class MinerUCloudClient:
    """Adapt MinerU's signed-upload cloud API to the local task protocol."""

    def __init__(
        self,
        *,
        http_client: AsyncHttpClient,
        status_timeout: aiohttp.ClientTimeout,
        stream_timeout: aiohttp.ClientTimeout,
        download_zip: Callable[..., Awaitable[int]],
    ) -> None:
        self.http_client = http_client
        self.status_timeout = status_timeout
        self.stream_timeout = stream_timeout
        self.download_zip = download_zip
        self.api_base_url = settings.MINERU_CLOUD_API_BASE_URL
        self.api_token = settings.MINERU_CLOUD_API_TOKEN.get_secret_value()
        self.model_version = settings.MINERU_CLOUD_MODEL_VERSION
        self.language = settings.MINERU_CLOUD_LANGUAGE

    async def create_task(
        self,
        *,
        filename: str,
        content: AsyncIterable[bytes],
        content_length: int,
        parse_method: str,
        formula_enable: bool,
        table_enable: bool,
    ) -> str:
        session = await self.http_client.get_session()
        headers = {"Authorization": f"Bearer {self.api_token}"}
        request_body = {
            "files": [{"name": filename, "is_ocr": parse_method == "ocr"}],
            "model_version": self.model_version,
            "language": self.language,
            "enable_formula": formula_enable,
            "enable_table": table_enable,
        }
        async with session.post(
            f"{self.api_base_url}/api/v4/file-urls/batch",
            json=request_body,
            headers=headers,
            timeout=self.status_timeout,
        ) as response:
            payload = await self._read_response(response)

        data = payload.get("data")
        batch_id = data.get("batch_id") if isinstance(data, dict) else None
        file_urls = data.get("file_urls") if isinstance(data, dict) else None
        if (
            not isinstance(batch_id, str)
            or not batch_id
            or not isinstance(file_urls, list)
            or len(file_urls) != 1
            or not isinstance(file_urls[0], str)
            or not file_urls[0].startswith("https://")
        ):
            raise RuntimeError(f"Invalid MinerU cloud upload response: {payload}")

        upload_payload = CloudUploadPayload(
            content,
            size=content_length,
        )
        async with session.put(
            file_urls[0],
            data=upload_payload,
            headers={"Content-Length": str(content_length)},
            skip_auto_headers={"Content-Type"},
            timeout=self.stream_timeout,
        ) as response:
            if response.status >= 400:
                body = await response.text()
                error_type = mineru_http_error_type(response.status)
                raise error_type(
                    f"MinerU cloud upload failed: status={response.status}, body={body[:500]}"
                )
        return f"cloud:{batch_id}"

    async def get_task(self, task_id: str) -> dict[str, Any]:
        result = await self._get_extract_result(task_id)
        state = result.get("state")
        if not isinstance(state, str):
            raise RuntimeError(f"Invalid MinerU cloud task state: {state}")
        status = {
            "waiting-file": "pending",
            "pending": "pending",
            "running": "processing",
            "converting": "processing",
            "done": "completed",
            "failed": "failed",
        }.get(state, state)
        return {"status": status, "error": result.get("err_msg")}

    async def get_task_result(self, task_id: str) -> dict[str, Any]:
        with tempfile.TemporaryFile(mode="w+b") as archive:
            await self.download_task_result_zip(
                task_id=task_id,
                destination=archive,
                max_bytes=settings.MINERU_MAX_RESULT_ARCHIVE_SIZE,
            )
            filename, content_list = await asyncio.to_thread(
                self._read_content_list,
                archive,
            )
        return {"results": {filename: {"content_list": content_list}}}

    async def download_task_result_zip(
        self,
        *,
        task_id: str,
        destination: BinaryIO,
        max_bytes: int,
    ) -> int:
        result = await self._get_extract_result(task_id)
        if result.get("state") != "done":
            raise RuntimeError("MinerU cloud task is not completed")
        result_url = result.get("full_zip_url")
        if not isinstance(result_url, str) or not result_url.startswith("https://"):
            raise RuntimeError("MinerU cloud result does not contain a valid ZIP URL")

        return await self.download_zip(
            url=result_url,
            destination=destination,
            max_bytes=max_bytes,
        )

    async def _get_extract_result(self, task_id: str) -> dict[str, Any]:
        batch_id = task_id.removeprefix("cloud:")
        session = await self.http_client.get_session()
        async with session.get(
            f"{self.api_base_url}/api/v4/extract-results/batch/{batch_id}",
            headers={"Authorization": f"Bearer {self.api_token}"},
            timeout=self.status_timeout,
        ) as response:
            payload = await self._read_response(response)

        data = payload.get("data")
        results = data.get("extract_result") if isinstance(data, dict) else None
        if (
            not isinstance(results, list)
            or len(results) != 1
            or not isinstance(results[0], dict)
        ):
            raise RuntimeError(f"Invalid MinerU cloud task response: {payload}")
        return results[0]

    @staticmethod
    def _read_content_list(archive: BinaryIO) -> tuple[str, str]:
        with zipfile.ZipFile(archive) as result_zip:
            names = [
                name
                for name in result_zip.namelist()
                if PurePosixPath(name).name.endswith("_content_list.json")
            ]
            if len(names) != 1:
                raise RuntimeError("MinerU cloud ZIP must contain exactly one content_list")
            member = result_zip.getinfo(names[0])
            if member.file_size > settings.MINERU_MAX_RESULT_UNCOMPRESSED_SIZE:
                raise RuntimeError("MinerU cloud content_list is too large")
            content_list = result_zip.read(names[0]).decode("utf-8-sig")
            if not isinstance(json.loads(content_list), list):
                raise RuntimeError("MinerU cloud content_list must be a list")
            filename = PurePosixPath(names[0]).name.removesuffix("_content_list.json")
            return filename, content_list

    @staticmethod
    async def _read_response(response: aiohttp.ClientResponse) -> dict[str, Any]:
        try:
            payload = await response.json()
        except aiohttp.ContentTypeError:
            body = await response.text()
            raise RuntimeError(
                f"MinerU cloud returned non-JSON: status={response.status}, body={body[:500]}"
            )
        if response.status >= 400:
            error_type = mineru_http_error_type(response.status)
            raise error_type(f"MinerU cloud request failed: status={response.status}")
        if not isinstance(payload, dict):
            raise RuntimeError(f"Invalid MinerU cloud response: {payload}")
        if payload.get("code") != 0:
            code = payload.get("code")
            error_type = (
                MinerUTransientError
                if code in {-10001, -60001, -60007, -60008, -60009, -60010}
                else RuntimeError
            )
            raise error_type(
                f"MinerU cloud request failed: code={code}, msg={payload.get('msg')}"
            )
        return payload


def mineru_http_error_type(status: int) -> type[RuntimeError]:
    if status in {408, 429} or status >= 500:
        return MinerUTransientError
    return RuntimeError
