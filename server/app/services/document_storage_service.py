from collections.abc import AsyncIterator
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import get_settings
from app.infrastructure.minio_client import minio_client

settings = get_settings()


class DocumentStorageService:
    """负责文档对象命名及共享 MinIO Bucket 中的读写操作。"""

    def __init__(self):
        self.client = minio_client
        self.bucket_name = settings.MINIO_BUCKET

    async def upload(
        self,
        file: UploadFile,
        file_size: int,
        content_type: str,
    ) -> str:
        """使用不可预测的对象名保存文件，并返回持久化 storage_key。"""

        suffix = Path(file.filename or "").suffix.lower()

        object_name = f"documents/{uuid4()}{suffix}"
        await file.seek(0)

        await self.client.upload_object(
            bucket_name=self.bucket_name,
            object_name=object_name,
            data=file.file,
            length=file_size,
            content_type=content_type,
        )

        return object_name

    async def create_download_url(
        self,
        object_name: str,
        expires_in: int = 1800,
    ) -> str:
        return await self.client.presigned_get_object(
            bucket_name=self.bucket_name,
            object_name=object_name,
            expires=timedelta(seconds=expires_in),
        )

    async def upload_bytes(
        self,
        *,
        content: bytes,
        suffix: str,
        content_type: str,
    ) -> str:
        """保存后端生成或规范化后的原文，避免伪造 UploadFile。"""
        normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
        object_name = f"documents/{uuid4()}{normalized_suffix.lower()}"
        await self.client.upload_object(
            bucket_name=self.bucket_name,
            object_name=object_name,
            data=BytesIO(content),
            length=len(content),
            content_type=content_type,
        )
        return object_name

    async def remove(self, object_name: str) -> None:
        await self.client.remove_object(
            bucket_name=self.bucket_name,
            object_name=object_name,
        )

    # 这里不是 async def：调用本身不会访问网络，只返回稍后消费的异步迭代器。
    def stream(
        self,
        object_name: str,
    ) -> AsyncIterator[bytes]:

        return self.client.stream_object(
            bucket_name=self.bucket_name,
            object_name=object_name,
        )


def get_document_storage():
    return DocumentStorageService()
