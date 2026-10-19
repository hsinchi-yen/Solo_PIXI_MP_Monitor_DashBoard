"""One error shape for every endpoint:
{"error": <中文 message>, "error_en": <English message>, "code": <machine code>}.

Messages are written as Msg("中文", "English"); the web UI shows the one matching its language.
ApiError and its helpers are plain Python (services raise them and unit tests import them
without FastAPI installed); install() wires the handlers into the app.
"""
import logging

log = logging.getLogger("pixi.api")


class Msg(str):
    """A user-facing message: the str value is Traditional Chinese, `.en` is English."""

    def __new__(cls, zh, en):
        obj = super().__new__(cls, zh)
        obj.en = en
        return obj


def english(message):
    """The English version of a message (a plain str has none)."""
    return getattr(message, "en", None)


class ApiError(Exception):
    def __init__(self, status, message, code="error", **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.extra = extra


def bad_request(message, code="bad_request", **extra):
    return ApiError(400, message, code, **extra)


def not_found(message=Msg("找不到資料", "Not found"), code="not_found"):
    return ApiError(404, message, code)


def conflict(message, code="conflict", **extra):
    return ApiError(409, message, code, **extra)


def error_response(status, message, code="error", **extra):
    from fastapi.responses import JSONResponse
    body = {"error": str(message), "code": code, **extra}
    if english(message):
        body["error_en"] = english(message)
    return JSONResponse(body, status_code=status)


def install(app):
    from fastapi import Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError):
        return error_response(exc.status, exc.message, exc.code, **exc.extra)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException):
        if isinstance(exc.detail, dict):
            return JSONResponse(exc.detail, status_code=exc.status_code, headers=getattr(exc, "headers", None))
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "error")
        return error_response(exc.status_code, str(exc.detail), code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError):
        fields = [".".join(str(p) for p in e.get("loc", [])[1:]) or "body" for e in exc.errors()]
        return error_response(400, Msg(f"參數格式不正確：{', '.join(fields)}", f"Invalid parameters: {', '.join(fields)}"),
                              "validation")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        # Never echo exception text (it can carry DB host/user); the log has the details.
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return error_response(500, Msg("伺服器發生錯誤，請稍後再試", "Server error. Please try again later."), "internal")
