from typing import Annotated

from fastapi import Depends, Query


class PaginationParams:
    """统一接收 page/pageSize，并转换为数据库 offset/limit。"""

    def __init__(
        self,
        page: Annotated[
            int,
            Query(
                ge=1,
                description="Page number, starting from 1",
            ),
        ] = 1,
        page_size: Annotated[
            int,
            Query(
                alias="pageSize",
                ge=1,
                le=100,
                description="Number of items per page",
            ),
        ] = 20,
    ) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        # 页码从 1 开始，因此第 N 页要跳过前 N-1 页。
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


PaginationDep = Annotated[
    PaginationParams,
    Depends(PaginationParams),
]
