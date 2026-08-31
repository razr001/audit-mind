import re
from unittest.mock import patch
from uuid import UUID

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from structlog.contextvars import get_contextvars

from app.core.exceptions import BusinessException, register_exception_handlers
from app.core.request_context import bind_current_user
from app.core.request_id import REQUEST_ID_HEADER, RequestIdMiddleware
from app.core.security import get_jwt_user
from app.schemas.auth import CurrentUser

USER_ID = UUID("9efa0f2d-7e1f-4204-8d0e-f254e36c8e59")


def request_id_app() -> FastAPI:
    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)

    @application.get("/context")
    async def context():
        return {"request_id": get_contextvars().get("request_id")}

    return application


def exception_app() -> FastAPI:
    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    register_exception_handlers(application)
    application.dependency_overrides[get_jwt_user] = lambda: CurrentUser(
        user_id=USER_ID,
        username="alice",
    )

    @application.get("/business", dependencies=[Depends(bind_current_user)])
    async def business_error():
        raise BusinessException(40904, "invalid state")

    @application.get("/unexpected", dependencies=[Depends(bind_current_user)])
    async def unexpected_error():
        raise RuntimeError("internal details")

    return application


def test_frontend_request_id_is_bound_and_returned():
    response = TestClient(request_id_app()).get(
        "/context",
        headers={REQUEST_ID_HEADER: "web-01HZY.test:42"},
    )

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == "web-01HZY.test:42"
    assert response.json()["request_id"] == "web-01HZY.test:42"


def test_missing_request_id_is_generated_by_backend():
    response = TestClient(request_id_app()).get("/context")
    request_id = response.headers[REQUEST_ID_HEADER]

    assert re.fullmatch(r"[0-9a-f]{32}", request_id)
    assert response.json()["request_id"] == request_id


def test_unsafe_request_id_is_replaced_instead_of_reflected():
    response = TestClient(request_id_app()).get(
        "/context",
        headers={REQUEST_ID_HEADER: "invalid request id"},
    )
    request_id = response.headers[REQUEST_ID_HEADER]

    assert request_id != "invalid request id"
    assert re.fullmatch(r"[0-9a-f]{32}", request_id)
    assert response.json()["request_id"] == request_id


def test_business_exception_log_keeps_user_id_after_dependency_cleanup():
    with patch("app.core.exceptions.logger.warning") as warning_log:
        response = TestClient(exception_app()).get(
            "/business",
            headers={REQUEST_ID_HEADER: "frontend-business"},
        )

    assert response.status_code == 409
    assert response.headers[REQUEST_ID_HEADER] == "frontend-business"
    warning_log.assert_called_once_with(
        "business.exception",
        code=40904,
        message="invalid state",
        request_id="frontend-business",
        user_id=str(USER_ID),
    )


def test_unexpected_exception_returns_header_and_logs_user_id():
    with patch("app.core.exceptions.logger.error") as error_log:
        response = TestClient(
            exception_app(),
            raise_server_exceptions=False,
        ).get(
            "/unexpected",
            headers={REQUEST_ID_HEADER: "frontend-unexpected"},
        )

    assert response.status_code == 500
    assert response.headers[REQUEST_ID_HEADER] == "frontend-unexpected"
    assert response.json()["request_id"] == "frontend-unexpected"
    error_log.assert_called_once()
    event, = error_log.call_args.args
    fields = error_log.call_args.kwargs
    assert event == "system.exception"
    assert fields["error_type"] == "RuntimeError"
    assert fields["request_id"] == "frontend-unexpected"
    assert fields["user_id"] == str(USER_ID)

    logged_exception = fields["exc_info"]
    assert isinstance(logged_exception, RuntimeError)
    assert str(logged_exception) == "internal details"
    assert logged_exception.__traceback__ is not None
    traceback_functions: list[str] = []
    exception_traceback = logged_exception.__traceback__
    while exception_traceback is not None:
        traceback_functions.append(exception_traceback.tb_frame.f_code.co_name)
        exception_traceback = exception_traceback.tb_next
    assert "unexpected_error" in traceback_functions
    # 完整异常只进入服务端日志，客户端仍然收到统一错误。
    assert "internal details" not in response.text
