import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Callable
from fastapi import Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("msg_ingestor.middleware")


class CorrelationIdAndTimingMiddleware(BaseHTTPMiddleware):
    """
    Assigns a unique correlation ID to every incoming request, logs timing,
    and returns processing time in HTTP response headers.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        request.state.correlation_id = correlation_id
        start_time = time.perf_counter()

        response = await call_next(request)

        process_time_ms = round((time.perf_counter() - start_time) * 1000, 2)
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Process-Time-Ms"] = str(process_time_ms)
        return response


def register_exception_handlers(app):
    """
    Registers centralized global exception handlers to guarantee zero unhandled 500 exceptions
    leaking internal stack traces, and delivering consistent API error responses.
    """

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        correlation_id = getattr(request.state, "correlation_id", str(uuid.uuid4()))
        logger.warning(f"[{correlation_id}] Validation Error: {exc.errors()}")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Input validation failed on request body or parameters.",
                    "details": exc.errors(),
                },
                "correlation_id": correlation_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        correlation_id = getattr(request.state, "correlation_id", str(uuid.uuid4()))
        logger.exception(f"[{correlation_id}] Unhandled Exception: {str(exc)}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "An unexpected error occurred while processing the request.",
                    "details": {"type": exc.__class__.__name__, "info": str(exc)},
                },
                "correlation_id": correlation_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
