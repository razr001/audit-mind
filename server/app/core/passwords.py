from pwdlib import PasswordHash

_password_hash = PasswordHash.recommended()
# 未命中用户名时仍执行一次同成本校验，降低通过响应时间枚举用户的风险。
_dummy_password_hash = _password_hash.hash("auditmind-dummy-password")


def hash_password(password: str) -> str:
    """使用 pwdlib 推荐的 Argon2 参数生成不可逆密码哈希。"""
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """错误哈希也按认证失败处理，避免损坏数据导致登录接口返回 500。"""
    try:
        return _password_hash.verify(password, password_hash or _dummy_password_hash)
    except (TypeError, ValueError):
        return False
