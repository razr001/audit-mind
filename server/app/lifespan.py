import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.ai.agent.checkpointer import agent_checkpointer
from app.core.config import get_settings
from app.core.logger import logger, setup_logging
from app.infrastructure.db.engine import engine
from app.infrastructure.db.health import ping_database
from app.infrastructure.es_client import es_client
from app.infrastructure.http_client import outbound_http_client
from app.infrastructure.minio_client import minio_client
from app.infrastructure.redis_client import redis_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    """在接收请求前验证依赖并在进程退出时统一释放客户端。"""
    settings = get_settings()
    setup_logging(
        # getattr 的默认值只服务于使用最小 Settings stub 的单元测试；
        # 正常运行时 Pydantic Settings 始终提供这三个字段。
        log_file_path=getattr(settings, "LOG_FILE_PATH", ""),
        log_file_max_bytes=getattr(
            settings,
            "LOG_FILE_MAX_BYTES",
            20 * 1024 * 1024,
        ),
        log_file_backup_count=getattr(settings, "LOG_FILE_BACKUP_COUNT", 5),
    )
    logger.info("auditmind.start")
    primary_error: BaseException | None = None
    try:
        # 启动阶段采用 fail-fast：核心依赖不可用时不启动一个表面健康、
        # 实际无法处理业务的 API 进程。
        if not await ping_database():
            raise RuntimeError("PostgreSQL health check failed")
        if not await redis_client.ping():
            raise RuntimeError("Redis health check failed")
        if not await es_client.ping():
            raise RuntimeError("Elasticsearch health check failed")
        # 共享 Bucket 只在应用启动阶段创建；业务上传流程不负责建桶。
        if not await minio_client.ping():
            raise RuntimeError("MinIO health check failed")
        await minio_client.ensure_bucket(settings.MINIO_BUCKET)
        await agent_checkpointer.initialize()

        yield
    except BaseException as exc:
        primary_error = exc

    async def close_resources() -> list[BaseException]:
        results = await asyncio.gather(
            redis_client.close(),
            es_client.close(),
            outbound_http_client.close(),
            agent_checkpointer.close(),
            engine.dispose(),
            return_exceptions=True,
        )
        return [result for result in results if isinstance(result, BaseException)]

    cleanup_task = asyncio.create_task(close_resources())
    cleanup_cancellation: asyncio.CancelledError | None = None
    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError as exc:
            cleanup_cancellation = cleanup_cancellation or exc
    cleanup_errors = cleanup_task.result()

    logger.info("auditmind.stop")

    if cleanup_cancellation is not None and primary_error is None:
        primary_error = cleanup_cancellation
    elif cleanup_cancellation is not None:
        cleanup_errors.insert(0, cleanup_cancellation)

    if primary_error is not None:
        if cleanup_errors:
            raise BaseExceptionGroup(
                "Application failed and resources failed to close",
                [primary_error, *cleanup_errors],
            )
        raise primary_error.with_traceback(primary_error.__traceback__)
    if cleanup_errors:
        raise BaseExceptionGroup("Application resources failed to close", cleanup_errors)
