from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    create_async_engine,
)

from app.core.config import get_settings

settings = get_settings()

engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    # 取出连接前先检测，避免数据库重启后复用已经失效的连接。
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    # 定期回收长连接，降低被数据库或网络设备单方面断开的概率。
    pool_recycle=1800,
)
