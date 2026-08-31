import json

from app.core.logger import _json_dumps


def test_json_log_serializer_preserves_non_ascii_text():
    rendered = _json_dumps({"answer": "必要个人信息包括手机号码"})

    assert "必要个人信息包括手机号码" in rendered
    assert "\\u" not in rendered
    assert json.loads(rendered) == {"answer": "必要个人信息包括手机号码"}
