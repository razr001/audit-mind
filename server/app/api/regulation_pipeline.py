from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)

from app.core.config import Settings, get_settings
from app.core.error_codes import REGULATION_STATUS_INVALID
from app.core.exceptions import BusinessException
from app.core.logger import logger
from app.core.request_context import bind_current_user, get_request_user
from app.infrastructure.regulation_pipeline_lock import (
    acquire_regulation_pipeline_lease,
)
from app.schemas.regulation import RegulationUploadListResponse
from app.schemas.response import Response
from app.services.regulation_index_service import (
    RegulationIndexService,
    get_regulation_index_service,
)
from app.services.regulation_knowledge_service import (
    RegulationKnowledgeService,
    get_regulation_knowledge_service,
)
from app.services.regulation_parse_service import (
    RegulationParseService,
    get_regulation_parse_service,
    run_regulation_parse_sync,
)
from app.services.regulation_pipeline_dispatch_service import schedule_regulation_pipeline
from app.services.regulation_pipeline_service import (
    get_regulation_pipeline_state,
)
from app.services.regulation_rule_service import (
    RegulationRuleService,
    get_regulation_rule_service,
    run_regulation_rule_build,
)


def require_local_environment(
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """禁止生产环境直接调用会绕过统一编排的单步调试接口。"""
    if settings.ENVIRONMENT.strip().lower() != "local":
        # 返回 404，避免在正式环境中暴露内部调试入口的存在。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

router = APIRouter(
    prefix="/regulation",
    tags=["regulations"],
    dependencies=[Depends(bind_current_user)],
)

# 单步接口只用于本地定位某一处理阶段的问题。生产业务必须使用
# /regulation/process/{regulation_id}，避免客户端自行拼接流水线。
local_router = APIRouter(
    prefix="/regulation",
    tags=["regulations-local"],
    dependencies=[
        Depends(require_local_environment),
        Depends(bind_current_user),
    ],
)


@asynccontextmanager
async def _local_pipeline_lease(regulation_id: UUID):
    """本地单步调试也复用法规总锁，不能绕过删除和完整流水线。"""
    async with acquire_regulation_pipeline_lease(regulation_id) as acquired:
        if not acquired:
            raise BusinessException(
                REGULATION_STATUS_INVALID,
                "regulation is being processed or deleted",
            )
        yield


@router.post(
    "/process/{regulation_id}",
    response_model=Response[RegulationUploadListResponse],
    status_code=202,
)
async def process_regulation(
    request: Request,
    regulation_id: UUID,
) -> Response[RegulationUploadListResponse]:
    """后台串联完整法规流水线；重复调用会从未完成或失败步骤继续。"""
    current_user = get_request_user()
    regulation = await get_regulation_pipeline_state(
        regulation_id=regulation_id,
        user_id=current_user.user_id,
    )

    # 全部完成时保持幂等，不再创建后台任务。其他状态统一交给后台编排器；
    # 同一法规的并发请求会由 Redis 总锁去重。
    try:
        scheduled = await schedule_regulation_pipeline(
            regulation=regulation,
            user_id=current_user.user_id,
            request_id=request.state.request_id,
        )
    except Exception as exc:
        # 入队失败表示后台尚未可靠接管，不能返回误导性的 202，也不能
        # 修改法规阶段状态；用户稍后重试会从原状态继续。
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="regulation pipeline queue is unavailable",
        ) from exc
    logger.info(
        "regulation.pipeline.requested",
        regulation_id=str(regulation.id),
        scheduled=scheduled,
        parse_status=regulation.status.value,
        chunk_status=regulation.chunk_status.value,
        index_status=regulation.index_status.value,
        rule_status=regulation.rule_status.value,
    )

    return Response[RegulationUploadListResponse](
        data=RegulationUploadListResponse.model_validate(regulation),
    )


@local_router.post(
    "/parse/{regulation_id}",
    response_model=Response[RegulationUploadListResponse],
    status_code=202,
)
async def start_regulation_parse(
    regulation_id: UUID,
    service: Annotated[
        RegulationParseService,
        Depends(get_regulation_parse_service),
    ],
) -> Response[RegulationUploadListResponse]:
    """本地调试：为上传者的知识源创建 MinerU 解析任务。"""
    current_user = get_request_user()
    async with _local_pipeline_lease(regulation_id):
        regulation = await service.start_parse(
            regulation_id=regulation_id,
            user_id=current_user.user_id,
        )
    return Response[RegulationUploadListResponse](
        data=RegulationUploadListResponse.model_validate(regulation),
    )


@local_router.post(
    "/parse/sync/{regulation_id}",
    response_model=Response[RegulationUploadListResponse],
    status_code=202,
)
async def sync_regulation_parse(
    regulation_id: UUID,
    background_tasks: BackgroundTasks,
    service: Annotated[
        RegulationParseService,
        Depends(get_regulation_parse_service),
    ],
) -> Response[RegulationUploadListResponse]:
    """本地调试：安排后台同步 MinerU 结果并立即返回当前状态。"""
    current_user = get_request_user()
    regulation, should_sync = await service.queue_sync_parse(
        regulation_id=regulation_id,
        user_id=current_user.user_id,
    )
    if should_sync:
        background_tasks.add_task(
            run_regulation_parse_sync,
            regulation_id=regulation_id,
            user_id=current_user.user_id,
        )
    return Response[RegulationUploadListResponse](
        data=RegulationUploadListResponse.model_validate(regulation),
    )


@local_router.post(
    "/chunks/build/{regulation_id}",
    response_model=Response[RegulationUploadListResponse],
)
async def build_regulation_chunks(
    regulation_id: UUID,
    service: Annotated[
        RegulationKnowledgeService,
        Depends(get_regulation_knowledge_service),
    ],
    rebuild: Annotated[
        bool,
        Query(description="Rebuild READY chunks and reset downstream states"),
    ] = False,
) -> Response[RegulationUploadListResponse]:
    """本地调试：从 ParseBlock 构建排除页面噪声的语义 Chunk。"""
    current_user = get_request_user()
    async with _local_pipeline_lease(regulation_id):
        regulation = await service.build(
            regulation_id=regulation_id,
            user_id=current_user.user_id,
            rebuild=rebuild,
        )
    return Response[RegulationUploadListResponse](
        data=RegulationUploadListResponse.model_validate(regulation),
    )


@local_router.post(
    "/index/{regulation_id}",
    response_model=Response[RegulationUploadListResponse],
)
async def index_regulation(
    regulation_id: UUID,
    service: Annotated[
        RegulationIndexService,
        Depends(get_regulation_index_service),
    ],
) -> Response[RegulationUploadListResponse]:
    """本地调试：为已经完成 Chunk 构建的法规生成向量索引。"""
    current_user = get_request_user()
    async with _local_pipeline_lease(regulation_id):
        regulation = await service.index(
            regulation_id=regulation_id,
            user_id=current_user.user_id,
        )
    return Response[RegulationUploadListResponse](
        data=RegulationUploadListResponse.model_validate(regulation),
    )


@local_router.post(
    "/rules/build/{regulation_id}",
    response_model=Response[RegulationUploadListResponse],
    status_code=202,
)
async def build_regulation_rules(
    regulation_id: UUID,
    background_tasks: BackgroundTasks,
    service: Annotated[
        RegulationRuleService,
        Depends(get_regulation_rule_service),
    ],
    rebuild: Annotated[
        bool,
        Query(
            description=(
                "Rebuild rules even when the current rule status is READY. "
                "Available only in the local environment."
            ),
        ),
    ] = False,
) -> Response[RegulationUploadListResponse]:
    """本地调试：使用 LangExtract 从法规 Chunk 构建结构化规则。"""
    current_user = get_request_user()
    regulation, should_build = await service.queue_build(
        regulation_id=regulation_id,
        user_id=current_user.user_id,
        rebuild=rebuild,
    )
    if should_build:
        background_tasks.add_task(
            run_regulation_rule_build,
            regulation_id=regulation_id,
            user_id=current_user.user_id,
            rebuild=rebuild,
        )
    return Response[RegulationUploadListResponse](
        data=RegulationUploadListResponse.model_validate(regulation),
    )
