from fastapi import APIRouter

from app.api.regulation_pipeline import local_router as local_pipeline_router
from app.api.regulation_pipeline import router as pipeline_router
from app.api.regulation_pipeline import sync_regulation_parse
from app.api.regulation_processing import router as processing_router
from app.api.regulation_queries import regulation_answer_event_stream
from app.api.regulation_queries import router as query_router
from app.api.regulation_sources import get_regulation_source_download_url
from app.api.regulation_sources import router as source_router

router = APIRouter()
router.routes.extend(source_router.routes)
router.routes.extend(processing_router.routes)
router.routes.extend(pipeline_router.routes)
router.routes.extend(local_pipeline_router.routes)
router.routes.extend(query_router.routes)

__all__ = [
    "get_regulation_source_download_url",
    "regulation_answer_event_stream",
    "router",
    "sync_regulation_parse",
]
