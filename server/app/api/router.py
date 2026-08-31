from fastapi import APIRouter

from app.api.assistant import router as assistant_router
from app.api.audit_maintenance import router as audit_maintenance_router
from app.api.audit_workflow import router as audit_workflow_router
from app.api.auth import router as auth_router
from app.api.document import router as document_router
from app.api.health import router as health_router
from app.api.regulation import router as regulation_router
from app.api.regulation_maintenance import router as regulation_maintenance_router
from app.api.users import router as users_router

api_router = APIRouter()

# 所有业务 Router 在这里集中挂载，main.py 只需注册一个根 Router。
api_router.include_router(
    health_router,
)
api_router.include_router(document_router)
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(audit_workflow_router)
api_router.include_router(audit_maintenance_router)
api_router.include_router(assistant_router)
api_router.include_router(regulation_router)
api_router.include_router(regulation_maintenance_router)
