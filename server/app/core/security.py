from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError as PyJWTInvalidTokenError

from app.core.config import get_settings
from app.schemas.auth import CurrentUser

bearer_scheme = HTTPBearer()


TokenType = Literal["access", "refresh"]


def decode_and_verify_jwt(token: str, *, expected_type: TokenType = "access") -> dict:
    """校验 JWT 的签名与标准声明，并返回可信的载荷。"""
    settings = get_settings()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    try:
        payload = jwt.decode(
            jwt=token,
            key=settings.JWT_SECRET_KEY.get_secret_value(),
            algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.JWT_ISSUER,
            audience=settings.JWT_AUDIENCE,
            options={
                # 强制这些声明存在，避免“签名正确但身份信息不完整”的
                # Token 进入后续业务代码。
                "require": [
                    "sub",
                    "exp",
                    "iat",
                    "iss",
                    "aud",
                    "token_type",
                    "jti",
                ]
            },
            leeway=10,
        )
    except PyJWTInvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid authentication token",
        )

    issued_at = payload.get("iat")
    expires_at = payload.get("exp")
    maximum_lifetime = (
        settings.JWT_EXPIRATION_DELTA * 60
        if expected_type == "access"
        else settings.JWT_REFRESH_EXPIRATION_DAYS * 24 * 60 * 60
    )
    if (
        isinstance(issued_at, bool)
        or not isinstance(issued_at, (int, float))
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, (int, float))
        or expires_at <= issued_at
        or expires_at - issued_at > maximum_lifetime + 1
        or payload.get("token_type") != expected_type
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid authentication token",
        )

    return payload


def create_token(
    user_id: UUID,
    username: str,
) -> str:
    """生成仅供本地开发使用的短期访问 Token。"""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_EXPIRATION_DELTA),
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "token_type": "access",
        "jti": uuid4().hex,
    }

    return jwt.encode(
        payload=payload,
        key=settings.JWT_SECRET_KEY.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )


def create_refresh_token(
    user_id: UUID,
    username: str,
    session_id: UUID,
    *,
    expires_at: datetime,
) -> str:
    """签发绑定数据库会话的 Refresh Token；jti 保证每次轮换值不同。"""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "sid": str(session_id),
        "jti": uuid4().hex,
        "token_type": "refresh",
        "iat": now,
        "exp": expires_at,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
    }
    return jwt.encode(
        payload=payload,
        key=settings.JWT_SECRET_KEY.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )


async def get_jwt_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> CurrentUser:
    """把 Bearer Token 转换为请求期间使用的当前用户对象。"""
    try:
        payload = decode_and_verify_jwt(credentials.credentials)

        return CurrentUser(
            user_id=UUID(payload["sub"]),
            username=payload.get("username"),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
