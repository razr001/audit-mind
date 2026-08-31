import re
from typing import Literal
from uuid import UUID

from app.core.logger import logger

DocumentFailureStage = Literal["parse"]
DOCUMENT_FAILURE_CODES: dict[DocumentFailureStage, str] = {
    "parse": "DOCUMENT_PARSE_FAILED",
}


def public_document_failure(
    stage: DocumentFailureStage,
    value: str | None,
) -> str | None:
    """Map internal and legacy document failures to a stable public code."""
    return None if value is None else DOCUMENT_FAILURE_CODES[stage]


def log_document_failure(
    event: str,
    *,
    document_id: UUID | None,
    error: BaseException | type[BaseException] | str,
) -> None:
    """Log an allow-listed failure category without messages or tracebacks."""
    if isinstance(error, BaseException):
        error_type = type(error).__name__
    elif isinstance(error, type) and issubclass(error, BaseException):
        error_type = error.__name__
    else:
        error_type = (
            error if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", error) else "ExternalFailure"
        )
    fields = {"error_type": error_type}
    if document_id is not None:
        fields["document_id"] = str(document_id)
    logger.error(event, **fields)
