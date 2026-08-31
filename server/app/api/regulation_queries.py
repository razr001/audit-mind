import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Query,
    Request,
)
from fastapi.responses import StreamingResponse

from app.ai.regulation_qa.errors import (
    REGULATION_QA_VERIFICATION_ERROR_CODE,
    REGULATION_QA_VERIFICATION_ERROR_MESSAGE,
    RegulationCitationVerificationError,
)
from app.core.logger import logger
from app.core.request_context import (
    bind_current_user,
    get_request_user,
)
from app.models.regulation import (
    KnowledgeCategory,
    RegulationSourceType,
)
from app.schemas.regulation_qa import (
    RegulationAnswerResponse,
    RegulationQuestionRequest,
)
from app.schemas.regulation_search import RegulationSearchItem
from app.schemas.response import Response
from app.services.regulation_qa_service import (
    RegulationQaService,
    get_regulation_qa_service,
)
from app.services.regulation_search_service import (
    RegulationSearchService,
    get_regulation_search_service,
)

router = APIRouter(
    prefix="/regulation",
    tags=["regulations"],
    dependencies=[Depends(bind_current_user)],
)


@router.get(
    "/search",
    response_model=Response[list[RegulationSearchItem]],
)
async def search_regulations(
    query: Annotated[
        str,
        Query(
            min_length=1,
            max_length=1000,
            description="Semantic regulation search query",
        ),
    ],
    top_k: Annotated[
        int,
        Query(alias="topK", ge=1, le=50),
    ] = 10,
    category: Annotated[
        KnowledgeCategory | None,
        Query(),
    ] = None,
    source_type: Annotated[
        RegulationSourceType | None,
        Query(alias="sourceType"),
    ] = None,
    jurisdiction: Annotated[
        str | None,
        Query(min_length=1, max_length=100),
    ] = None,
    service: RegulationSearchService = Depends(get_regulation_search_service),
) -> Response[list[RegulationSearchItem]]:
    """混合检索用户可访问的法规、平台政策和公司规则。"""
    current_user = get_request_user()

    items = await service.search(
        user_id=current_user.user_id,
        query=query,
        top_k=top_k,
        category=category,
        source_type=source_type,
        jurisdiction=jurisdiction,
    )

    return Response[list[RegulationSearchItem]](data=items)


@router.post(
    "/ask",
    response_model=Response[RegulationAnswerResponse],
)
async def ask_regulations(
    request: RegulationQuestionRequest,
    service: Annotated[
        RegulationQaService,
        Depends(get_regulation_qa_service),
    ],
) -> Response[RegulationAnswerResponse]:
    """根据可访问的法规 Chunk 回答问题，并返回经过校验的原文依据。"""
    current_user = get_request_user()
    result = await service.ask(
        user_id=current_user.user_id,
        question=request.question,
        top_k=request.top_k,
        category=request.category,
        source_type=request.source_type,
        jurisdiction=request.jurisdiction,
    )
    return Response[RegulationAnswerResponse](data=result)


def encode_sse_event(event_type: str, data: dict) -> str:
    """把受控事件名和 JSON 数据编码为单个 SSE 帧。"""
    payload = json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"event: {event_type}\ndata: {payload}\n\n"


async def regulation_answer_event_stream(
    *,
    request_body: RegulationQuestionRequest,
    request: Request,
    service: RegulationQaService,
    user_id: UUID,
) -> AsyncIterator[str]:
    """驱动问答流，并在断连时确定性关闭下游异步生成器。"""
    try:
        async with aclosing(
            service.stream(
                user_id=user_id,
                question=request_body.question,
                top_k=request_body.top_k,
                category=request_body.category,
                source_type=request_body.source_type,
                jurisdiction=request_body.jurisdiction,
            )
        ) as events:
            async for event in events:
                if await request.is_disconnected():
                    return
                if event["type"] == "heartbeat":
                    yield ": ping\n\n"
                else:
                    yield encode_sse_event(event["type"], event["data"])
    except asyncio.CancelledError:
        raise
    except RegulationCitationVerificationError:
        # Stable public classification lets the client explain that untrusted
        # output was blocked without exposing the model's fabricated content.
        logger.warning("regulation.qa.verification_blocked")
        if not await request.is_disconnected():
            yield encode_sse_event(
                "error",
                {
                    "code": REGULATION_QA_VERIFICATION_ERROR_CODE,
                    "message": REGULATION_QA_VERIFICATION_ERROR_MESSAGE,
                },
            )
            yield encode_sse_event("done", {})
    except Exception as exc:
        # 不向客户端或结构化日志暴露模型供应商的原始错误内容。
        logger.error(
            "regulation.qa.stream_failed",
            error_type=type(exc).__name__,
        )
        if not await request.is_disconnected():
            yield encode_sse_event(
                "error",
                {
                    "code": 50000,
                    "message": "regulation answer generation failed",
                },
            )
            yield encode_sse_event("done", {})


@router.post(
    "/ask/stream",
    response_class=StreamingResponse,
)
async def stream_regulation_answer(
    request_body: RegulationQuestionRequest,
    request: Request,
    service: Annotated[
        RegulationQaService,
        Depends(get_regulation_qa_service),
    ],
) -> StreamingResponse:
    """以 SSE 返回阶段和经过服务端引用校验的法规答案。"""
    current_user = get_request_user()

    return StreamingResponse(
        regulation_answer_event_stream(
            request_body=request_body,
            request=request,
            service=service,
            user_id=current_user.user_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
