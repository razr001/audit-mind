from uuid import UUID

from app.models.operation_log import OperationLog
from app.models.regulation import Regulation
from app.repositories.operation_log_repository import OperationLogRepository


class OperationAuditService:
    """构造安全的操作快照，避免业务 Service 重复或误记敏感正文。"""

    def __init__(self, *, repository: OperationLogRepository) -> None:
        self.repository = repository

    async def record_regulation_created(
        self,
        *,
        regulation: Regulation,
        user_id: UUID,
        request_id: str | None,
        operation_type: str,
    ) -> None:
        await self.repository.save(
            OperationLog(
                user_id=user_id,
                operation_type=operation_type,
                target_type="REGULATION",
                target_id=regulation.id,
                request_id=request_id,
                # 只记录用于审计的业务元数据，不保存正文、哈希和存储键。
                after_data={
                    "title": regulation.title,
                    "sourceType": regulation.source_type.value,
                    "visibility": regulation.visibility.value,
                    "originalFilename": regulation.original_filename,
                    "contentType": regulation.content_type,
                },
            )
        )

    async def record_regulation_deleted(
        self,
        *,
        regulation: Regulation,
        user_id: UUID,
        request_id: str | None,
    ) -> None:
        """记录知识源删除前的业务元数据，不复制完整原文。"""
        await self.repository.save(
            OperationLog(
                user_id=user_id,
                operation_type="REGULATION_DELETED",
                target_type="REGULATION",
                target_id=regulation.id,
                request_id=request_id,
                before_data={
                    "title": regulation.title,
                    "sourceType": regulation.source_type.value,
                    "visibility": regulation.visibility.value,
                    "originalFilename": regulation.original_filename,
                    "contentType": regulation.content_type,
                },
            )
        )
