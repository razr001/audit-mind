from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Query,
)

from app.api.dependencies import PaginationDep
from app.core.request_context import (
    bind_current_user,
    get_request_user,
)
from app.models.regulation import (
    KnowledgeCategory,
)
from app.models.regulation_rule import RegulationRuleType
from app.schemas.page_result import PageResult
from app.schemas.regulation import (
    RegulationAssetDownloadResponse,
    RegulationParseBlockResponse,
    RegulationUploadListResponse,
)
from app.schemas.regulation_rule import RegulationRuleResponse
from app.schemas.response import Response
from app.services.regulation_asset_service import (
    RegulationAssetService,
    get_regulation_asset_service,
)
from app.services.regulation_rule_service import (
    RegulationRuleService,
    get_regulation_rule_service,
)
from app.services.regulation_service import (
    RegulationService,
    get_regulation_service,
)

router = APIRouter(
    prefix="/regulation",
    tags=["regulations"],
    dependencies=[Depends(bind_current_user)],
)


@router.get(
    "/my/list",
    response_model=Response[PageResult[RegulationUploadListResponse]],
)
async def get_my_regulation_list(
    pagination: PaginationDep,
    service: Annotated[
        RegulationService,
        Depends(get_regulation_service),
    ],
    category: Annotated[
        KnowledgeCategory | None,
        Query(description="Knowledge category filter"),
    ] = None,
) -> Response[PageResult[RegulationUploadListResponse]]:
    """分页返回当前用户上传的知识源，包含解析与知识化失败原因。"""
    current_user = get_request_user()
    items, total = await service.get_uploaded_page(
        user_id=current_user.user_id,
        offset=pagination.offset,
        limit=pagination.limit,
        category=category,
    )
    return Response[PageResult[RegulationUploadListResponse]](
        data=PageResult(
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
            items=[RegulationUploadListResponse.model_validate(item) for item in items],
        ),
    )


@router.get(
    "/asset/download-url/{block_id}",
    response_model=Response[RegulationAssetDownloadResponse],
)
async def get_regulation_asset_download_url(
    block_id: UUID,
    service: Annotated[
        RegulationAssetService,
        Depends(get_regulation_asset_service),
    ],
) -> Response[RegulationAssetDownloadResponse]:
    """为当前用户可访问的法规局部图片生成十分钟下载地址。"""
    current_user = get_request_user()
    result = await service.create_download_url(
        block_id=block_id,
        user_id=current_user.user_id,
    )
    return Response[RegulationAssetDownloadResponse](data=result)


@router.get(
    "/blocks/{regulation_id}",
    response_model=Response[list[RegulationParseBlockResponse]],
)
async def get_regulation_page_blocks(
    regulation_id: UUID,
    page_number: Annotated[
        int,
        Query(alias="pageNumber", ge=1),
    ],
    service: Annotated[
        RegulationAssetService,
        Depends(get_regulation_asset_service),
    ],
) -> Response[list[RegulationParseBlockResponse]]:
    """按 PDF 页返回文本、图片、bbox 和局部图片元数据。"""
    current_user = get_request_user()
    blocks = await service.get_page_blocks(
        regulation_id=regulation_id,
        page_number=page_number,
        user_id=current_user.user_id,
    )
    return Response[list[RegulationParseBlockResponse]](
        data=[RegulationParseBlockResponse.model_validate(block) for block in blocks],
    )


@router.get(
    "/rules/{regulation_id}",
    response_model=Response[PageResult[RegulationRuleResponse]],
)
async def get_regulation_rules(
    regulation_id: UUID,
    pagination: PaginationDep,
    service: Annotated[
        RegulationRuleService,
        Depends(get_regulation_rule_service),
    ],
    rule_type: Annotated[
        RegulationRuleType | None,
        Query(alias="ruleType"),
    ] = None,
) -> Response[PageResult[RegulationRuleResponse]]:
    """查询当前用户可访问法规的结构化规则及其原文来源。"""
    current_user = get_request_user()
    rules, total = await service.get_rules(
        regulation_id=regulation_id,
        user_id=current_user.user_id,
        offset=pagination.offset,
        limit=pagination.limit,
        rule_type=rule_type,
    )
    return Response[PageResult[RegulationRuleResponse]](
        data=PageResult(
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
            items=[RegulationRuleResponse.model_validate(rule) for rule in rules],
        ),
    )
