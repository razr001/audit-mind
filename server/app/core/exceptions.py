# app/core/exceptions.py

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from app.core.logger import logger
from app.core.request_id import REQUEST_ID_HEADER


def _request_log_fields(request: Request) -> dict[str, str | None]:
    """异常处理时从 request.state 恢复已清理的请求与用户上下文。"""
    fields: dict[str, str | None] = {
        "request_id": getattr(request.state, "request_id", None),
    }
    user_id = getattr(request.state, "user_id", None)
    if user_id is not None:
        fields["user_id"] = user_id
    return fields


def _response_headers(
    request: Request,
    headers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """确保异常响应也返回 request ID，同时保留 WWW-Authenticate 等头。"""
    result = dict(headers or {})
    request_id = getattr(request.state, "request_id", None)
    if request_id is not None:
        result[REQUEST_ID_HEADER] = request_id
    return result


class BusinessException(Exception):
    """
    业务异常

    用于可预期业务错误，例如：
    - 文档不存在
    - 状态不允许操作
    - 权限不足
    """

    def __init__(
        self,
        code: int,
        message: str,
        data: Any | None = None,
    ):

        self.code = code
        self.message = message
        self.data = data

        super().__init__(message)


async def business_exception_handler(
    request: Request,
    exc: BusinessException,
) -> Response:
    """把可预期的业务异常转换为统一响应结构。"""

    log_fields = _request_log_fields(request)
    request_id = log_fields["request_id"]

    logger.warning(
        "business.exception",
        code=exc.code,
        message=exc.message,
        **log_fields,
    )

    status_code = exc.code // 100
    if status_code < 400 or status_code > 599:
        status_code = 400
    return JSONResponse(
        status_code=status_code,
        headers=_response_headers(request),
        content={
            "code": exc.code,
            "message": exc.message,
            "data": exc.data,
            "request_id": request_id,
        },
    )


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> Response:
    """把 FastAPI/Pydantic 的参数错误整理为前端易处理的字段列表。"""

    errors = []

    for error in exc.errors():
        errors.append(
            {
                "field": ".".join(str(item) for item in error["loc"]),
                "message": error["msg"],
                "type": error["type"],
            }
        )

    log_fields = _request_log_fields(request)
    request_id = log_fields["request_id"]

    logger.warning(
        "request.validation.failed",
        errors=errors,
        **log_fields,
    )

    return JSONResponse(
        status_code=422,
        headers=_response_headers(request),
        content={
            "code": 42201,
            "message": "request validation failed",
            "data": {"errors": errors},
            "request_id": request_id,
        },
    )


async def http_exception_handler(
    request: Request,
    exc: HTTPException,
):
    """保留 HTTP 状态码，同时统一异常响应字段。"""
    log_fields = _request_log_fields(request)
    logger.warning(
        "HTTPException",
        code=exc.status_code,
        message=exc.detail,
        **log_fields,
    )
    request_id = log_fields["request_id"]
    return JSONResponse(
        status_code=exc.status_code,
        headers=_response_headers(request, exc.headers),
        content={
            "code": exc.status_code,
            "message": exc.detail,
            "data": None,
            "request_id": request_id,
        },
    )


async def global_exception_handler(
    request: Request,
    exc: Exception,
) -> Response:
    """记录未预期异常的完整调用栈，但只向客户端返回统一错误。"""

    log_fields = _request_log_fields(request)
    request_id = log_fields["request_id"]

    logger.error(
        "system.exception",
        error_type=type(exc).__name__,
        # 显式传入异常对象，确保即使处理器不在原始 except 作用域中，
        # structlog 仍能输出异常消息、源码文件、行号和完整调用链。
        exc_info=exc,
        **log_fields,
    )

    return JSONResponse(
        status_code=500,
        headers=_response_headers(request),
        content={
            "code": 50000,
            "message": "internal server error",
            "data": None,
            "request_id": request_id,
        },
    )


def register_exception_handlers(app):
    """集中注册异常处理器；具体处理器的注册顺序不代表匹配优先级。"""
    app.add_exception_handler(
        BusinessException,
        business_exception_handler,
    )

    app.add_exception_handler(
        RequestValidationError,
        validation_exception_handler,
    )

    app.add_exception_handler(
        Exception,
        global_exception_handler,
    )

    app.add_exception_handler(HTTPException, http_exception_handler)
