import re
import traceback
from pathlib import Path
from typing import Literal
from uuid import UUID

from app.core.logger import logger

RegulationFailureStage = Literal["parse", "chunk", "index", "rule"]
REGULATION_FAILURE_CODES: dict[RegulationFailureStage, str] = {
    "parse": "REGULATION_PARSE_FAILED",
    "chunk": "REGULATION_CHUNK_FAILED",
    "index": "REGULATION_INDEX_FAILED",
    "rule": "REGULATION_RULE_FAILED",
}


def public_regulation_failure(
    stage: RegulationFailureStage,
    value: str | None,
) -> str | None:
    """Map both new and legacy internal failures to a stable public code."""
    return None if value is None else REGULATION_FAILURE_CODES[stage]


def log_regulation_failure(
    event: str,
    *,
    regulation_id: UUID,
    error: BaseException | type[BaseException] | str,
) -> None:
    """记录安全的异常类型与调用栈，不写异常消息、原文或密钥。"""
    if isinstance(error, BaseException):
        error_type = type(error).__name__
    elif isinstance(error, type) and issubclass(error, BaseException):
        error_type = error.__name__
    else:
        error_type = (
            error if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", error) else "ExternalFailure"
        )
    fields: dict[str, object] = {
        "regulation_id": str(regulation_id),
        "error_type": error_type,
    }
    if isinstance(error, BaseException) and error.__traceback__ is not None:
        # traceback.extract_tb 不读取局部变量，也不包含可能携带文档内容或
        # 第三方响应正文的异常消息。只保留最后 20 个调用位置，足够在
        # Grafana/Loki 中定位失败代码，同时避免日志泄露敏感数据。
        fields["error_stack"] = [
            {
                "file": _safe_frame_path(frame.filename),
                "line": frame.lineno,
                "function": frame.name,
            }
            for frame in traceback.extract_tb(error.__traceback__)[-20:]
        ]

    logger.error(event, **fields)


def _safe_frame_path(filename: str) -> str:
    """去除主机绝对目录，只保留足够定位代码的末尾路径。"""
    path = Path(filename)
    return "/".join(path.parts[-4:])
