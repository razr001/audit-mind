from uuid import UUID

from fastapi import Depends
from structlog.contextvars import bound_contextvars

from app.ai.embedding import get_embedding_service
from app.ai.regulation.extractor import (
    compliance_rule_extractor,
)
from app.core.config import get_settings
from app.core.regulation_failure import log_regulation_failure
from app.infrastructure.db.session import async_session_factory
from app.infrastructure.db.unit_of_work import UnitOfWork, get_uow
from app.infrastructure.redis_lock import run_with_lease_guard
from app.infrastructure.regulation_pipeline_lock import (
    acquire_regulation_pipeline_lease,
)
from app.infrastructure.regulation_rule_vector_store import regulation_rule_vector_store
from app.repositories.regulation_chunk_repository import (
    RegulationChunkRepository,
)
from app.repositories.regulation_parse_block_repository import (
    RegulationParseBlockRepository,
)
from app.repositories.regulation_repository import RegulationRepository
from app.repositories.regulation_rule_repository import (
    RegulationRuleRepository,
)
from app.services.regulation_rule_index_service import RegulationRuleIndexService
from app.services.regulation_rule_orchestrator import RegulationRuleService

settings = get_settings()


def _build_rule_index_service() -> RegulationRuleIndexService:
    """仅在真正执行后台规则构建时初始化 Embedding 客户端。"""
    return RegulationRuleIndexService(
        embedding=get_embedding_service(),
        vector_store=regulation_rule_vector_store,
    )


def get_regulation_rule_service(
    uow: UnitOfWork = Depends(get_uow),
) -> RegulationRuleService:
    return RegulationRuleService(
        uow=uow,
        regulation_repository=RegulationRepository(uow.session),
        chunk_repository=RegulationChunkRepository(uow.session),
        parse_block_repository=RegulationParseBlockRepository(uow.session),
        rule_repository=RegulationRuleRepository(uow.session),
        extractor=compliance_rule_extractor,
    )


async def run_regulation_rule_build(
    *,
    regulation_id: UUID,
    user_id: UUID,
    rebuild: bool = False,
) -> None:
    """使用独立 Session 执行后台任务，避免复用已结束请求的连接。"""
    with bound_contextvars(user_id=str(user_id)):
        async with acquire_regulation_pipeline_lease(regulation_id) as acquired:
            if not acquired:
                return
            async with async_session_factory() as session:
                uow = UnitOfWork(session)
                service = RegulationRuleService(
                    uow=uow,
                    regulation_repository=RegulationRepository(session),
                    chunk_repository=RegulationChunkRepository(session),
                    parse_block_repository=RegulationParseBlockRepository(session),
                    rule_repository=RegulationRuleRepository(session),
                    extractor=compliance_rule_extractor,
                    rule_index_service=_build_rule_index_service(),
                )
                try:
                    await run_with_lease_guard(
                        acquired,
                        service.process_queued_build(
                            regulation_id=regulation_id,
                            user_id=user_id,
                            rebuild=rebuild,
                        ),
                    )
                except Exception as exc:
                    # Service 已负责写 FAILED；后台入口只吞掉异常，避免 ASGI
                    # 把已经返回 202 的请求记录成响应发送失败。
                    log_regulation_failure(
                        "regulation.rule.background_task_failed",
                        regulation_id=regulation_id,
                        error=exc,
                    )
