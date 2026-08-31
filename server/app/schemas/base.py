from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiSchema(BaseModel):
    """API Schema 的统一基类，集中处理前端 camelCase 与 ORM 转换。"""

    model_config = ConfigDict(
        alias_generator=to_camel,
        # 接收前端 camelCase
        validate_by_alias=True,
        # 也允许 Python snake_case，方便内部测试
        validate_by_name=True,
        # 返回前端时输出 camelCase
        serialize_by_alias=True,
        # 支持从 SQLAlchemy 对象读取属性
        from_attributes=True,
    )
