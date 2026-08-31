from sqlalchemy import text

from app.infrastructure.db.engine import engine


async def ping_database() -> bool:
    """执行最小查询验证数据库可连接且可执行 SQL。"""
    try:
        async with engine.connect() as connection:
            value = await connection.scalar(text("SELECT 1"))
        return value == 1
    except Exception:
        return False
