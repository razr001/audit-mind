from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)

from app.api.dependencies import PaginationDep
from app.core.request_context import (
    bind_current_user,
    get_request_user,
)
from app.models.regulation import (
    KnowledgeCategory,
    KnowledgeVisibility,
    Regulation,
    RegulationSourceType,
)
from app.schemas.page_result import PageResult
from app.schemas.regulation import (
    RegulationDetailResponse,
    RegulationPublicResponse,
    RegulationSourceDownloadResponse,
    RegulationTextCreateRequest,
    RegulationUploadForm,
    RegulationUploadResponse,
)
from app.schemas.response import Response
from app.services.regulation_asset_service import (
    RegulationAssetService,
    get_regulation_asset_service,
)
from app.services.regulation_detail_service import (
    RegulationDetailService,
    get_regulation_detail_service,
)
from app.services.regulation_management_service import (
    RegulationManagementService,
    get_regulation_management_service,
)
from app.services.regulation_pipeline_dispatch_service import schedule_regulation_pipeline
from app.services.regulation_service import (
    RegulationService,
    get_regulation_service,
)
from app.services.regulation_text_service import (
    RegulationTextService,
    get_regulation_text_service,
)

router = APIRouter(
    prefix="/regulation",
    tags=["regulations"],
    dependencies=[Depends(bind_current_user)],
)


@router.get(
    "/get/download-url/{regulation_id}",
    response_model=Response[RegulationSourceDownloadResponse],
)
async def get_regulation_source_download_url(
    regulation_id: UUID,
    service: Annotated[
        RegulationAssetService,
        Depends(get_regulation_asset_service),
    ],
) -> Response[RegulationSourceDownloadResponse]:
    """获取可访问法规原文件的短期地址及解析页数。"""
    current_user = get_request_user()
    result = await service.create_source_download_url(
        regulation_id=regulation_id,
        user_id=current_user.user_id,
    )
    return Response[RegulationSourceDownloadResponse](data=result)


@router.get(
    "/get/{regulation_id}",
    response_model=Response[RegulationDetailResponse],
)
async def get_regulation(
    regulation_id: UUID,
    service: Annotated[
        RegulationDetailService,
        Depends(get_regulation_detail_service),
    ],
) -> Response[RegulationDetailResponse]:
    """获取共享知识或当前用户有权查看的私有知识。"""
    current_user = get_request_user()
    regulation, page_count = await service.get_accessible_detail(
        regulation_id=regulation_id,
        user_id=current_user.user_id,
    )
    detail = RegulationDetailResponse.model_validate(
        {**vars(regulation), "page_count": page_count},
    )
    detail.can_manage = regulation.uploaded_by == current_user.user_id
    return Response[RegulationDetailResponse](
        data=detail,
    )


@router.get(
    "/list",
    response_model=Response[PageResult[RegulationPublicResponse]],
)
async def get_regulation_list(
    pagination: PaginationDep,
    service: Annotated[
        RegulationService,
        Depends(get_regulation_service),
    ],
    category: Annotated[
        KnowledgeCategory | None,
        Query(description="Knowledge category filter"),
    ] = None,
    source_type: Annotated[
        RegulationSourceType | None,
        Query(alias="sourceType", description="Regulation source type filter"),
    ] = None,
) -> Response[PageResult[RegulationPublicResponse]]:
    """按访问范围、知识分类和来源类型分页查询知识源。"""
    current_user = get_request_user()
    items, total = await service.get_accessible_page(
        user_id=current_user.user_id,
        offset=pagination.offset,
        limit=pagination.limit,
        category=category,
        source_type=source_type,
    )
    return Response[PageResult[RegulationPublicResponse]](
        data=PageResult(
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
            items=[
                RegulationPublicResponse.model_validate(
                    {**vars(item), "can_manage": item.uploaded_by == current_user.user_id}
                )
                for item in items
            ],
        ),
    )


@router.delete(
    "/{regulation_id}",
    response_model=Response[UUID],
)
async def delete_regulation(
    regulation_id: UUID,
    request: Request,
    service: Annotated[
        RegulationManagementService,
        Depends(get_regulation_management_service),
    ],
) -> Response[UUID]:
    """物理删除当前用户上传的知识源及其存储、检索副本。"""
    current_user = get_request_user()
    deleted_id = await service.delete(
        regulation_id=regulation_id,
        user_id=current_user.user_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return Response[UUID](data=deleted_id)


def get_regulation_upload_form(
    title: Annotated[
        str,
        Form(
            min_length=1,
            max_length=255,
        ),
    ],
    source_type: Annotated[
        RegulationSourceType,
        Form(alias="sourceType"),
    ] = RegulationSourceType.REGULATION,
    visibility: Annotated[
        KnowledgeVisibility,
        Form(),
    ] = KnowledgeVisibility.SHARED,
    language: Annotated[
        str,
        Form(
            min_length=2,
            max_length=20,
        ),
    ] = "auto",
    document_number: Annotated[
        str | None,
        Form(
            alias="documentNumber",
            max_length=100,
        ),
    ] = None,
    authority: Annotated[
        str | None,
        Form(max_length=255),
    ] = None,
    jurisdiction: Annotated[
        str,
        Form(
            min_length=1,
            max_length=100,
        ),
    ] = "CN",
    effective_date: Annotated[
        date | None,
        Form(alias="effectiveDate"),
    ] = None,
    expiration_date: Annotated[
        date | None,
        Form(alias="expirationDate"),
    ] = None,
    version: Annotated[
        str | None,
        Form(max_length=50),
    ] = None,
    source_url: Annotated[
        str | None,
        Form(
            alias="sourceUrl",
            max_length=1000,
        ),
    ] = None,
) -> RegulationUploadForm:
    """把 multipart 表单字段组装为统一 Pydantic 对象。"""
    return RegulationUploadForm(
        title=title,
        source_type=source_type,
        visibility=visibility,
        language=language,
        document_number=document_number,
        authority=authority,
        jurisdiction=jurisdiction,
        effective_date=effective_date,
        expiration_date=expiration_date,
        version=version,
        source_url=source_url,
    )


@router.post(
    "/upload",
    response_model=Response[RegulationUploadResponse],
)
async def upload_regulation(
    request: Request,
    file: Annotated[
        UploadFile,
        File(description="Regulation document"),
    ],
    form: Annotated[
        RegulationUploadForm,
        Depends(get_regulation_upload_form),
    ],
    service: Annotated[
        RegulationService,
        Depends(get_regulation_service),
    ],
) -> Response[RegulationUploadResponse]:
    """上传法规、平台政策、合同或公司规则文件。"""
    current_user = get_request_user()

    request_id = request.state.request_id
    regulation = await service.upload(
        file=file,
        form=form,
        user_id=current_user.user_id,
        request_id=request_id,
    )
    await _schedule_created_regulation(
        regulation=regulation,
        user_id=current_user.user_id,
        request_id=request_id,
    )

    return Response[RegulationUploadResponse](
        data=RegulationUploadResponse.model_validate(regulation),
    )


@router.post(
    "/text",
    response_model=Response[RegulationUploadResponse],
)
async def create_regulation_text(
    body: RegulationTextCreateRequest,
    request: Request,
    service: Annotated[
        RegulationTextService,
        Depends(get_regulation_text_service),
    ],
) -> Response[RegulationUploadResponse]:
    """直接录入 Markdown/纯文本知识；原文落 MinIO 后进入统一规则流水线。"""
    current_user = get_request_user()
    request_id = request.state.request_id
    regulation = await service.create(
        request=body,
        user_id=current_user.user_id,
        request_id=request_id,
    )
    await _schedule_created_regulation(
        regulation=regulation,
        user_id=current_user.user_id,
        request_id=request_id,
    )
    return Response[RegulationUploadResponse](
        data=RegulationUploadResponse.model_validate(regulation),
    )


async def _schedule_created_regulation(
    *, regulation: Regulation, user_id: UUID, request_id: str
) -> None:
    """创建接口只有在后台队列可靠接管后才返回 202。"""

    try:
        await schedule_regulation_pipeline(
            regulation=regulation,
            user_id=user_id,
            request_id=request_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="regulation pipeline queue is unavailable",
        ) from exc
