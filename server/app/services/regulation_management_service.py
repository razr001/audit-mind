from uuid import UUID

from fastapi import Depends

from app.core.error_codes import REGULATION_NOT_FOUND, REGULATION_STATUS_INVALID
from app.core.exceptions import BusinessException
from app.core.logger import logger
from app.infrastructure.db.unit_of_work import UnitOfWork, get_uow
from app.infrastructure.regulation_deletion_coordinator import (
    RegulationDeletionCoordinator,
    regulation_deletion_coordinator,
)
from app.infrastructure.regulation_rule_vector_store import (
    RegulationRuleVectorStore,
    regulation_rule_vector_store,
)
from app.infrastructure.regulation_vector_store import (
    RegulationVectorStore,
    regulation_vector_store,
)
from app.models.regulation import Regulation
from app.repositories.operation_log_repository import OperationLogRepository
from app.repositories.regulation_management_repository import (
    RegulationManagementRepository,
)
from app.repositories.regulation_rule_repository import RegulationRuleRepository
from app.services.operation_audit_service import OperationAuditService
from app.services.regulation_storage_service import RegulationStorageService


class RegulationManagementService:
    """管理知识源的用户操作，不与解析和规则生成流程耦合。"""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        repository: RegulationManagementRepository,
        rule_repository: RegulationRuleRepository,
        operation_audit: OperationAuditService,
        storage: RegulationStorageService,
        chunk_vector_store: RegulationVectorStore,
        rule_vector_store: RegulationRuleVectorStore,
        deletion_coordinator: RegulationDeletionCoordinator,
    ) -> None:
        self.uow = uow
        self.repository = repository
        self.rule_repository = rule_repository
        self.operation_audit = operation_audit
        self.storage = storage
        self.chunk_vector_store = chunk_vector_store
        self.rule_vector_store = rule_vector_store
        self.deletion_coordinator = deletion_coordinator

    async def delete(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
        request_id: str | None,
    ) -> UUID:
        """只允许上传者物理删除，历史审计使用独立规则快照追溯。"""
        logger.info(
            "regulation.delete_started",
            regulation_id=str(regulation_id),
            request_id=request_id,
            user_id=str(user_id),
        )
        async with self.deletion_coordinator.acquire(regulation_id) as guard:
            if not guard.acquired:
                logger.info(
                    "regulation.delete_lock_conflict",
                    regulation_id=str(regulation_id),
                    request_id=request_id,
                    user_id=str(user_id),
                    reason=guard.reason,
                )
                raise BusinessException(
                    REGULATION_STATUS_INVALID,
                    "regulation is being processed or deleted",
                )
            source_storage_key = await self._delete_acquired(
                regulation_id=regulation_id,
                user_id=user_id,
                request_id=request_id,
            )
            # 一个删除流程只使用这一把总锁，但包括 ES、PostgreSQL、MinIO
            # 的每个步骤都必须发生在成功持锁之后，锁冲突时保证零副作用。
            failed_resources = await self._cleanup_storage(
                regulation_id=regulation_id,
                source_storage_key=source_storage_key,
                user_id=user_id,
                request_id=request_id,
            )
        logger.info(
            (
                "regulation.delete_completed_with_errors"
                if failed_resources
                else "regulation.delete_completed"
            ),
            regulation_id=str(regulation_id),
            request_id=request_id,
            user_id=str(user_id),
            failed_resources=failed_resources,
        )
        return regulation_id

    async def _delete_acquired(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
        request_id: str | None,
    ) -> str:
        """先提交删除意图，再删除 ES 和 PostgreSQL，并返回待清理文件键。"""
        async with self.uow:
            regulation = await self.repository.claim_for_deletion(
                regulation_id=regulation_id,
                user_id=user_id,
            )
            if regulation is None:
                existing = await self.repository.find_by_id_and_user(
                    regulation_id=regulation_id,
                    user_id=user_id,
                )
                if existing is not None:
                    raise BusinessException(
                        REGULATION_STATUS_INVALID,
                        "regulation cannot be deleted while processing",
                    )
            regulation = self._require_owned(regulation)
            source_storage_key = regulation.storage_key
            deletion_lock_version = regulation.lock_version

        # 上面的短事务已经持久化 DELETING 和 enabled=False。任何一个 ES
        # 删除失败都会保留删除意图；用户重试时重新领取版本并从这里继续。
        # 规则召回的数据库权限复核也不会再接受该法规；Chunk 副本由本流程
        # 紧接着删除，任一删除异常都会终止流程并保留该删除意图。
        index_cleanup_operations = (
            ("chunks", self.chunk_vector_store.delete_regulation_chunks),
            ("rules", self.rule_vector_store.delete_regulation_rules),
        )
        for resource_kind, cleanup in index_cleanup_operations:
            try:
                await cleanup(regulation_id=str(regulation_id))
            except Exception as exc:
                logger.exception(
                    "regulation.resource_delete_failed",
                    regulation_id=str(regulation_id),
                    request_id=request_id,
                    user_id=str(user_id),
                    resource_kind=resource_kind,
                    error_type=type(exc).__name__,
                )
                raise

        # ES 删除完成后重新锁行并复核版本和 DELETING 状态。Redis 租约如果
        # 中途失效，新的删除者会递增 lock_version，使旧删除者无法提交删除。
        async with self.uow:
            regulation = await self.repository.find_by_id_and_user_for_update(
                regulation_id=regulation_id,
                user_id=user_id,
            )
            regulation = self._require_owned(regulation)
            await self.operation_audit.record_regulation_deleted(
                regulation=regulation,
                user_id=user_id,
                request_id=request_id,
            )
            # RegulationRule 没有数据库外键，必须在删除主表前显式删除。
            await self.rule_repository.delete_by_regulation(regulation_id)
            deleted = await self.repository.delete_if_lock_version(
                regulation_id=regulation_id,
                user_id=user_id,
                expected_lock_version=deletion_lock_version,
            )
            if not deleted:
                # 抛出异常会让同一事务中的操作日志和规则删除一起回滚。
                raise BusinessException(
                    REGULATION_STATUS_INVALID,
                    "regulation execution lease was superseded",
                )

        return source_storage_key

    async def _cleanup_storage(
        self,
        *,
        regulation_id: UUID,
        source_storage_key: str,
        user_id: UUID,
        request_id: str | None,
    ) -> list[str]:
        """持有法规总锁清理文件；失败只记录并交给运维处理。"""
        failed_resources: list[str] = []
        storage_cleanup_operations = (
            ("source", lambda: self.storage.remove(source_storage_key)),
            ("assets", lambda: self.storage.remove_parse_assets(regulation_id)),
        )
        for resource_kind, cleanup in storage_cleanup_operations:
            try:
                await cleanup()
            except Exception as exc:
                failed_resources.append(f"minio_{resource_kind}")
                logger.exception(
                    "regulation.resource_delete_failed",
                    regulation_id=str(regulation_id),
                    request_id=request_id,
                    user_id=str(user_id),
                    resource_kind=f"minio_{resource_kind}",
                    error_type=type(exc).__name__,
                )
        return failed_resources

    @staticmethod
    def _require_owned(regulation: Regulation | None) -> Regulation:
        if regulation is None:
            # 不区分无权和不存在，避免泄露其他用户的私有知识。
            raise BusinessException(REGULATION_NOT_FOUND, "regulation not found")
        return regulation


def get_regulation_management_service(
    uow: UnitOfWork = Depends(get_uow),
) -> RegulationManagementService:
    return RegulationManagementService(
        uow=uow,
        repository=RegulationManagementRepository(uow.session),
        rule_repository=RegulationRuleRepository(uow.session),
        operation_audit=OperationAuditService(
            repository=OperationLogRepository(uow.session),
        ),
        storage=RegulationStorageService(),
        chunk_vector_store=regulation_vector_store,
        rule_vector_store=regulation_rule_vector_store,
        deletion_coordinator=regulation_deletion_coordinator,
    )
