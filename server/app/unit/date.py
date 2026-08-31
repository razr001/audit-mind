from datetime import datetime, timezone


def utc_now() -> datetime:
    """统一生成带 UTC 时区信息的当前时间。"""
    return datetime.now(timezone.utc)
