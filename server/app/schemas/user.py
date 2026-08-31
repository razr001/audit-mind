from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiSchema


class UserCreateRequest(ApiSchema):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class UserResponse(ApiSchema):
    id: UUID
    username: str
    created_at: datetime
    updated_at: datetime
