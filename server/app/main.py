from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.request_body_limit import RequestBodyLimitMiddleware
from app.core.request_id import RequestIdMiddleware
from app.core.security_headers import SecurityHeadersMiddleware
from app.lifespan import lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    """组装应用，并允许测试注入不含敏感信息的配置。"""
    resolved_settings = settings or get_settings()
    is_development = getattr(
        resolved_settings,
        "ENVIRONMENT",
        "local",
    ).lower() in {"local", "dev", "development"}
    application = FastAPI(
        title=resolved_settings.APP_NAME,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if is_development else None,
        openapi_url="/openapi.json" if is_development else None,
        redoc_url="/redoc" if is_development else None,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.CORS_ALLOWED_ORIGINS,
        # Access Token 使用 Bearer；Refresh Token 使用 HttpOnly Cookie。
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Accept",
            "Authorization",
            "Content-Type",
            "X-Request-ID",
        ],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    application.add_middleware(
        RequestBodyLimitMiddleware,
        default_limit=getattr(
            resolved_settings,
            "REQUEST_BODY_MAX_BYTES",
            10 * 1024 * 1024,
        ),
        limits={
            # multipart 会额外包含 boundary、文件名和表单元数据，为文件
            # 业务上限预留固定协议开销，文件本身仍由 Service 精确计数。
            "/document/upload": getattr(
                resolved_settings, "DOCUMENT_MAX_FILE_SIZE", 100 * 1024 * 1024
            )
            + 1024 * 1024,
            "/regulation/upload": getattr(
                resolved_settings, "REGULATION_MAX_FILE_SIZE", 100 * 1024 * 1024
            )
            + 1024 * 1024,
            "/audit/tasks": getattr(
                resolved_settings, "DOCUMENT_MAX_FILE_SIZE", 100 * 1024 * 1024
            )
            + 1024 * 1024,
            "/regulation/text": getattr(
                resolved_settings, "REGULATION_MAX_TEXT_LENGTH", 500_000
            )
            * 4
            + 64 * 1024,
            "/audit/tasks/markdown": getattr(
                resolved_settings, "AUDIT_MARKDOWN_MAX_BYTES", 5 * 1024 * 1024
            )
            + 64 * 1024,
        },
    )
    # 安全响应头包裹请求体限制层，确保提前返回的 413 也携带完整安全头。
    application.add_middleware(SecurityHeadersMiddleware)
    # 最后注册使其成为最外层业务中间件：包括认证失败、参数错误和后台任务
    # 在内的所有日志与响应都能获得同一个 request_id。
    application.add_middleware(RequestIdMiddleware)

    register_exception_handlers(application)
    application.include_router(api_router)
    return application


app = create_app()
