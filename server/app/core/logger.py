import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import structlog


def _json_dumps(event_dict: object, **kwargs: Any) -> str:
    """序列化 JSON 日志时保留中文等非 ASCII 字符，方便直接检索和阅读。"""

    return json.dumps(event_dict, ensure_ascii=False, **kwargs)


def setup_logging(
    *,
    log_file_path: str = "",
    log_file_max_bytes: int = 20 * 1024 * 1024,
    log_file_backup_count: int = 5,
) -> None:
    """将 structlog JSON 同时写入 stdout 和可选轮转文件。"""

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file_path:
        path = Path(log_file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                filename=path,
                maxBytes=log_file_max_bytes,
                backupCount=log_file_backup_count,
                encoding="utf-8",
            )
        )

    # force=True 避免 Uvicorn 或测试框架提前配置 root logger 后，文件
    # Handler 被静默忽略。应用日志仍然只渲染一次 JSON。
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=handlers,
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            # 将 logger.exception() 传入的异常信息渲染成完整 traceback。
            structlog.processors.format_exc_info,
            # json.dumps 默认会把中文转成 \uXXXX。日志统一使用 UTF-8，
            # 因此直接保留原字符，便于在文件、Loki 和控制台中排查问题。
            structlog.processors.JSONRenderer(serializer=_json_dumps),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


# 业务模块统一导入该 logger，避免各处创建格式不一致的日志器。
logger = structlog.get_logger()
