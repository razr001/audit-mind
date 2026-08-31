from contextvars import ContextVar

from fastapi import Depends, Request
from structlog.contextvars import bind_contextvars, reset_contextvars

from app.core.security import get_jwt_user
from app.schemas.auth import CurrentUser

_user_context: ContextVar[CurrentUser | None] = ContextVar(
    "current_user",
    default=None,
)


def get_request_user() -> CurrentUser:
    """读取当前异步请求绑定的用户；只能在认证依赖执行后调用。"""
    user = _user_context.get()
    if user is None:
        raise RuntimeError("Current user is not available")

    return user


async def bind_current_user(
    request: Request,
    user: CurrentUser = Depends(get_jwt_user),
):
    """在请求生命周期内绑定用户，并在请求结束时恢复上下文。"""
    # ContextVar 适配 asyncio 的任务上下文，比线程级 ThreadLocal 更适合
    # FastAPI；不同并发请求不会互相覆盖当前用户。
    context_token = _user_context.set(user)
    # yield 依赖会在异常处理器运行前清理 ContextVar，因此额外保存到
    # request.state，确保业务异常和未捕获异常日志仍能关联用户。
    request.state.user_id = str(user.user_id)
    # structlog 的所有后续日志会自动合并这两个字段。使用 token 恢复旧值，
    # 避免异步任务或测试嵌套绑定时误删外层日志上下文。
    log_tokens = bind_contextvars(
        user_id=str(user.user_id),
    )
    try:
        yield user
    finally:
        reset_contextvars(**log_tokens)
        # 必须 reset，防止连接或执行任务被复用时残留上一个请求的用户。
        _user_context.reset(context_token)
