import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.core.config import Settings, get_settings
from app.schemas.regulation_maintenance import (
    RegulationTimeoutResult,
    RegulationTimeoutStage,
)
from app.schemas.response import Response
from app.services.regulation_maintenance_service import (
    RegulationMaintenanceService,
    get_regulation_maintenance_service,
)


def verify_scheduler_token(
    settings: Annotated[Settings, Depends(get_settings)],
    provided_token: Annotated[str, Header(alias="X-Internal-Token")] = "",
) -> None:
    """使用独立 Token 认证调度器；未配置时默认关闭内部维护接口。"""
    expected_token = settings.SCHEDULER_ACCESS_TOKEN.get_secret_value()
    if not expected_token or not secrets.compare_digest(provided_token, expected_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


router = APIRouter(
    prefix="/internal/regulation/tasks",
    tags=["internal-regulation-maintenance"],
    dependencies=[Depends(verify_scheduler_token)],
)


@router.post(
    "/timeout/{stage}",
    response_model=Response[RegulationTimeoutResult],
)
async def mark_regulation_tasks_timed_out(
    stage: RegulationTimeoutStage,
    service: Annotated[
        RegulationMaintenanceService,
        Depends(get_regulation_maintenance_service),
    ],
) -> Response[RegulationTimeoutResult]:
    """供 XXL-Job 定时把指定阶段的超时任务标记为 FAILED。"""
    result = await service.mark_timed_out_failed(stage=stage)
    return Response[RegulationTimeoutResult](data=result)
