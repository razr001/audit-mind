import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import AsyncIO

from app.core.config import get_settings

settings = get_settings()

# 队列和业务锁可以共用 Redis，但使用独立 namespace，
# 避免 Dramatiq 内部键与 AuditMind 业务锁混在一起。
task_broker = RedisBroker(
    url=settings.REDIS_URL,
    namespace="auditmind-dramatiq",
)

# 当前流水线都是 async def。Dramatiq 需要 AsyncIO middleware
# 为每个 Worker 进程提供长期运行的事件循环。
task_broker.add_middleware(AsyncIO())

# actor 装饰器和 send() 默认使用当前全局 Broker。
dramatiq.set_broker(task_broker)
