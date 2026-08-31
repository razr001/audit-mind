from datetime import timedelta
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import UploadFile

from app.core.config import get_settings
from app.infrastructure.minio_client import minio_client

settings = get_settings()


class RegulationStorageService:
    """负责法规文件在共享 MinIO Bucket 中的对象命名和读写。"""

    def __init__(self) -> None:
        self.client = minio_client
        self.bucket_name = settings.MINIO_BUCKET

    async def upload(
        self,
        file: UploadFile,
        file_size: int,
        content_type: str,
    ) -> str:
        """按 regulations/ 前缀保存文件并返回对象键。"""
        suffix = Path(file.filename or "").suffix.lower()

        object_name = f"regulations/{uuid4()}{suffix}"

        await file.seek(0)

        await self.client.upload_object(
            bucket_name=self.bucket_name,
            object_name=object_name,
            data=file.file,
            length=file_size,
            content_type=content_type,
        )

        return object_name

    async def upload_text(
        self,
        *,
        data: bytes,
    ) -> str:
        """保存直接录入的 Markdown 原文，供下载、核验和后续追溯。"""
        object_name = f"regulations/{uuid4()}.md"
        await self.client.upload_object(
            bucket_name=self.bucket_name,
            object_name=object_name,
            data=BytesIO(data),
            length=len(data),
            content_type="text/markdown; charset=utf-8",
        )
        return object_name

    async def remove(
        self,
        object_name: str,
    ) -> None:
        await self.client.remove_object(
            bucket_name=self.bucket_name,
            object_name=object_name,
        )

    async def remove_parse_assets(self, regulation_id: UUID) -> None:
        """删除该法规所有解析任务产生的图片，包括失败重试的遗留对象。"""
        await self.client.remove_objects_by_prefix(
            bucket_name=self.bucket_name,
            prefix=f"regulation-assets/{regulation_id}/",
        )

    async def upload_parse_asset(
        self,
        *,
        regulation_id: UUID,
        parse_task_id: str,
        content_hash: str,
        suffix: str,
        content_type: str,
        data: bytes,
    ) -> str:
        """把 MinerU 截取图片保存为法规解析产物，并返回永久对象键。"""
        # task_id 隔离每次解析产生的对象；同一任务的并发同步会写入
        # 相同对象键，因此失败请求不会在业务流程中立即删除这些对象。
        object_name = f"regulation-assets/{regulation_id}/{parse_task_id}/{content_hash}{suffix}"
        await self.client.upload_object(
            bucket_name=self.bucket_name,
            object_name=object_name,
            data=BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
        return object_name

    async def create_asset_download_url(
        self,
        *,
        object_name: str,
        expires_in: int = 600,
    ) -> str:
        """为通过业务权限校验的局部图片生成短期访问地址。"""
        return await self.client.presigned_get_object(
            bucket_name=self.bucket_name,
            object_name=object_name,
            expires=timedelta(seconds=expires_in),
        )

    async def create_source_download_url(
        self,
        *,
        object_name: str,
        expires_in: int = 600,
    ) -> str:
        """为已通过法规访问校验的原文件生成短期访问地址。"""
        return await self.client.presigned_get_object(
            bucket_name=self.bucket_name,
            object_name=object_name,
            expires=timedelta(seconds=expires_in),
        )

    def stream(self, object_name: str):
        """返回惰性异步文件流，供 MinerU 转发时按需读取。"""
        return self.client.stream_object(
            bucket_name=self.bucket_name,
            object_name=object_name,
        )
