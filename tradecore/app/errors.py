"""Consistent error envelope across the API."""
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(HTTPException):
    """HTTPException with a machine-readable error code."""

    def __init__(self, status_code: int, message: str, code: str):
        super().__init__(status_code=status_code, detail=message)
        self.code = code


def _envelope(message: str, code: str) -> dict:
    return {"error": message, "code": code}


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=_envelope(exc.detail, exc.code))


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT",
        422: "UNPROCESSABLE",
        429: "RATE_LIMITED",
    }.get(exc.status_code, "HTTP_ERROR")
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(str(exc.detail), code),
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={**_envelope("Validation error", "VALIDATION_ERROR"), "details": exc.errors()},
    )
