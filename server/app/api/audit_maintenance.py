from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.regulation_maintenance import verify_scheduler_token
from app.schemas.audit_maintenance import AuditTimeoutResult, AuditTimeoutStage
from app.schemas.response import Response
from app.services.audit_maintenance_service import (
    AuditMaintenanceService,
    get_audit_maintenance_service,
)

router = APIRouter(
    prefix="/internal/audit/tasks",
    tags=["internal-audit-maintenance"],
    dependencies=[Depends(verify_scheduler_token)],
)


@router.post("/timeout/{stage}", response_model=Response[AuditTimeoutResult])
async def mark_audit_tasks_timed_out(
    stage: AuditTimeoutStage,
    service: Annotated[
        AuditMaintenanceService, Depends(get_audit_maintenance_service)
    ],
) -> Response[AuditTimeoutResult]:
    return Response(data=await service.mark_timed_out_failed(stage=stage))
