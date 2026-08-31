from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiSchema


class CurrentUser(ApiSchema):
    """JWT 载荷验证后在请求上下文中保存的可信用户信息。"""

    user_id: UUID
    username: str | None = None


class LoginRequest(ApiSchema):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class TokenResponse(ApiSchema):
    """Refresh token 由 HttpOnly Cookie 承载，不能出现在响应 JSON 中。"""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
