import math

from pydantic import computed_field

from app.schemas.base import ApiSchema


class PageResult[T](ApiSchema):
    """通用分页响应；items 的具体类型由调用接口决定。"""

    total: int
    items: list[T]
    page: int
    page_size: int

    @computed_field
    @property
    def total_pages(self) -> int:
        """根据总记录数和每页数量动态计算总页数。"""
        if self.page_size <= 0:
            return 0
        return math.ceil(self.total / self.page_size)
