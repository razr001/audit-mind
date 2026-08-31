from app.ai.embedding import get_embedding_service
from app.ai.regulation.extractor import compliance_rule_extractor
from app.ai.visual_analyzer import get_regulation_visual_analyzer
from app.core.config import get_settings
from app.infrastructure.db.unit_of_work import UnitOfWork
from app.infrastructure.mineru_client import mineru_client
from app.infrastructure.regulation_rule_vector_store import regulation_rule_vector_store
from app.infrastructure.regulation_vector_store import regulation_vector_store
from app.repositories.regulation_chunk_repository import RegulationChunkRepository
from app.repositories.regulation_parse_block_repository import RegulationParseBlockRepository
from app.repositories.regulation_repository import RegulationRepository
from app.repositories.regulation_rule_repository import RegulationRuleRepository
from app.services.regulation_index_service import RegulationIndexService
from app.services.regulation_knowledge_service import RegulationKnowledgeService
from app.services.regulation_parse_service import RegulationParseService
from app.services.regulation_pipeline_service import RegulationPipelineService
from app.services.regulation_rule_index_service import RegulationRuleIndexService
from app.services.regulation_rule_orchestrator import RegulationRuleService
from app.services.regulation_storage_service import RegulationStorageService

settings = get_settings()


def build_regulation_pipeline_service(session) -> RegulationPipelineService:
    """为独立 Worker Session 组装法规流水线及其阶段服务。"""
    uow = UnitOfWork(session)
    regulation_repository = RegulationRepository(session)
    parse_block_repository = RegulationParseBlockRepository(session)
    chunk_repository = RegulationChunkRepository(session)
    rule_repository = RegulationRuleRepository(session)

    parse_service = RegulationParseService(
        uow=uow,
        repository=regulation_repository,
        parse_block_repository=parse_block_repository,
        storage=RegulationStorageService(),
        mineru=mineru_client,
        visual_analyzer=get_regulation_visual_analyzer(),
    )
    knowledge_service = RegulationKnowledgeService(
        uow=uow,
        regulation_repository=regulation_repository,
        parse_block_repository=parse_block_repository,
        chunk_repository=chunk_repository,
        rule_repository=rule_repository,
        vector_store=regulation_vector_store,
        rule_vector_store=regulation_rule_vector_store,
    )

    # 解析或分块尚未完成时不初始化 Embedding 客户端。
    def build_index_service() -> RegulationIndexService:
        return RegulationIndexService(
            uow=uow,
            regulation_repository=regulation_repository,
            chunk_repository=chunk_repository,
            embedding=get_embedding_service(),
            vector_store=regulation_vector_store,
        )

    rule_service = RegulationRuleService(
        uow=uow,
        regulation_repository=regulation_repository,
        chunk_repository=chunk_repository,
        parse_block_repository=parse_block_repository,
        rule_repository=rule_repository,
        extractor=compliance_rule_extractor,
        rule_index_service=RegulationRuleIndexService(
            embedding=get_embedding_service(),
            vector_store=regulation_rule_vector_store,
        ),
    )
    return RegulationPipelineService(
        uow=uow,
        repository=regulation_repository,
        parse_service=parse_service,
        knowledge_service=knowledge_service,
        index_service_provider=build_index_service,
        rule_service=rule_service,
        poll_interval_seconds=settings.REGULATION_PIPELINE_POLL_INTERVAL_SECONDS,
        wait_timeout_seconds=settings.REGULATION_PIPELINE_WAIT_TIMEOUT_SECONDS,
    )
