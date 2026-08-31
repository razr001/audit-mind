import enum
from datetime import datetime

from app.schemas.base import ApiSchema


class AuditTimeoutStage(str, enum.Enum):
    PIPELINE = "pipeline"
    PAGE = "page"


class AuditTimeoutResult(ApiSchema):
    stage: AuditTimeoutStage
    stale_before: datetime
    updated_count: int
