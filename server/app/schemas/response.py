from pydantic import BaseModel


class Response[T](BaseModel):
    """所有成功接口共用的外层响应结构。"""

    code: int = 0
    message: str = "success"
    data: T | None = None
