from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, UploadFile

from app.api.dependencies import PaginationDep
from app.core.request_context import bind_current_user, get_request_user
from app.schemas.document import DocumentDownloadResponse, DocumentResponse
from app.schemas.page_result import PageResult
from app.schemas.response import Response
from app.services.document_parse_service import (
    DocumentParseService,
    get_document_parse_service,
)
from app.services.document_service import DocumentService, get_document_service

router = APIRouter(
    prefix="/document",
    tags=["documents"],
    dependencies=[Depends(bind_current_user)],
)


@router.post("/upload", response_model=Response[DocumentResponse])
async def upload_document(
    file: UploadFile, service: DocumentService = Depends(get_document_service)
):
    """上传并校验用户文档；文件解析由后续接口单独启动。"""
    current_user = get_request_user()
    document = await service.upload_document(file, current_user.user_id)
    return Response(data=document)


@router.get("/get/{document_id}", response_model=Response[DocumentResponse])
async def get_document(document_id: UUID, service: DocumentService = Depends(get_document_service)):
    """获取当前用户自己的单个文档。"""
    current_user = get_request_user()
    document = await service.get_document(document_id, current_user.user_id)
    return Response(data=document)


@router.get("/list", response_model=Response[PageResult[DocumentResponse]])
async def get_document_list(
    pagination: PaginationDep,
    service: DocumentService = Depends(get_document_service),
    sort_by: Annotated[
        Literal["createdAt", "originalFilename", "fileSize", "status"],
        Query(alias="sortBy", description="Allow-listed document sort field"),
    ] = "createdAt",
    sort_order: Annotated[
        Literal["asc", "desc"],
        Query(alias="sortOrder", description="Document sort direction"),
    ] = "desc",
):
    """分页获取当前用户上传的文档及失败状态。"""
    current_user = get_request_user()
    documents, total = await service.get_document_list(
        current_user.user_id,
        pagination.offset,
        pagination.limit,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    page_result = PageResult(
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        items=[DocumentResponse.model_validate(document) for document in documents],
    )
    return Response(data=page_result)


@router.get(
    "/get/download-url/{document_id}",
    response_model=Response[DocumentDownloadResponse],
)
async def get_document_download_url(
    document_id: UUID,
    service: DocumentService = Depends(get_document_service),
):
    """生成当前用户文档的短期下载链接。"""
    current_user = get_request_user()

    url, expires_in = await service.create_download_url(
        document_id=document_id,
        user_id=current_user.user_id,
    )

    result = DocumentDownloadResponse(
        url=url,
        expires_in=expires_in,
    )

    return Response[DocumentDownloadResponse](data=result)


@router.post(
    "/parse/{document_id}",
    response_model=Response[DocumentResponse],
    status_code=202,
)
async def start_document_parse(
    document_id: UUID,
    service: DocumentParseService = Depends(
        get_document_parse_service,
    ),
):
    """创建 MinerU 解析任务并立即返回 PARSING 状态。"""
    current_user = get_request_user()

    document = await service.start_parse(
        document_id=document_id,
        user_id=current_user.user_id,
    )

    return Response[DocumentResponse](
        data=DocumentResponse.model_validate(document),
    )


@router.post(
    "/parse/sync/{document_id}",
    response_model=Response[DocumentResponse],
)
async def sync_document_parse(
    document_id: UUID,
    service: DocumentParseService = Depends(
        get_document_parse_service,
    ),
):
    """由客户端轮询 MinerU 结果，并在完成后保存页面和 Chunk。"""
    current_user = get_request_user()

    document = await service.sync_parse_result(
        document_id=document_id,
        user_id=current_user.user_id,
    )

    return Response[DocumentResponse](
        data=DocumentResponse.model_validate(document),
    )
