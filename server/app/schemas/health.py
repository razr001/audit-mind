from typing import Literal

from app.schemas.base import ApiSchema


class HealthDependencies(ApiSchema):
    postgresql: bool
    redis: bool
    elasticsearch: bool
    minio: bool


class HealthResponse(ApiSchema):
    status: Literal["UP", "DOWN"]
    dependencies: HealthDependencies
