import enum
from datetime import datetime

from app.schemas.base import ApiSchema


class RegulationTimeoutStage(str, enum.Enum):
    """允许 XXL-Job 独立调度的法规后台处理阶段。"""

    PARSE = "parse"
    CHUNK = "chunk"
    INDEX = "index"
    RULE = "rule"


class RegulationTimeoutResult(ApiSchema):
    """一次超时清理任务的可观测结果。"""

    stage: RegulationTimeoutStage
    stale_before: datetime
    updated_count: int
