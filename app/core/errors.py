# ─────────────────────────────────────────────────────
# Module   : app.core.errors
# ─────────────────────────────────────────────────────
from fastapi import Request
from fastapi.responses import JSONResponse
import traceback
from app.core.responses import error_response
import logging

logger = logging.getLogger(__name__)

class AppError(Exception):
    """Base exception for application-level errors"""
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code

class ValidationError(AppError):
    def __init__(self, message: str):
        super().__init__(message, status_code=400)

class NotFoundError(AppError):
    def __init__(self, message: str):
        super().__init__(message, status_code=404)

class UnauthorizedError(AppError):
    def __init__(self, message: str = "Unauthorized"):
        super().__init__(message, status_code=401)

class ConflictError(AppError):
    def __init__(self, message: str):
        super().__init__(message, status_code=409)

class ExternalServiceError(AppError):
    def __init__(self, message: str):
        super().__init__(message, status_code=502)

async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    return error_response(
        error=exc.message,
        request_id=request_id,
        status_code=exc.status_code
    )

async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    logger.error(
        f"Unhandled error: {str(exc)}", 
        extra={"request_id": request_id, "traceback": traceback.format_exc()}
    )
    return error_response(
        error="Internal Server Error",
        request_id=request_id,
        status_code=500
    )
