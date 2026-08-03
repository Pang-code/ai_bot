import logging
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ai_agents.errors import ServiceError


logger = logging.getLogger(__name__)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", str(uuid4()))


async def handle_service_error(
    request: Request,
    error: ServiceError,
) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={
            "error": {
                "code": error.code,
                "message": error.message,
                "request_id": _request_id(request),
            }
        },
    )


async def handle_validation_error(
    request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    logger.info("Request validation failed: %s", error.errors())
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "请求参数不符合要求。",
                "request_id": _request_id(request),
            }
        },
    )


async def handle_unexpected_error(
    request: Request,
    error: Exception,
) -> JSONResponse:
    logger.exception("Unhandled API error", exc_info=error)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "服务发生内部错误。",
                "request_id": _request_id(request),
            }
        },
    )
