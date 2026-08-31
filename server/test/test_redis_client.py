from unittest.mock import patch

from app.infrastructure.redis_client import RedisClient, settings


def test_redis_client_bounds_connect_and_socket_waits() -> None:
    """分布式锁使用的所有命令都必须继承连接池的有限网络超时。"""
    with patch("app.infrastructure.redis_client.redis.from_url") as from_url:
        RedisClient()

    from_url.assert_called_once_with(
        settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT_SECONDS,
        socket_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS,
        socket_keepalive=True,
        health_check_interval=settings.REDIS_HEALTH_CHECK_INTERVAL_SECONDS,
    )
