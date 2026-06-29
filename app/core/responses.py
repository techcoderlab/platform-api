# ─────────────────────────────────────────────────────
# Module   : app.core.responses
# ─────────────────────────────────────────────────────
from typing import Any, Generic, TypeVar, Optional
from pydantic import BaseModel
from fastapi.responses import JSONResponse

T = TypeVar('T')

class SuccessResponse(BaseModel, Generic[T]):
    status: str = "success"
    data: T
    request_id: Optional[str] = None

class ErrorResponse(BaseModel):
    status: str = "error"
    error: str
    request_id: Optional[str] = None
    details: Optional[Any] = None

def success_response(data: Any, request_id: Optional[str] = None, status_code: int = 200) -> JSONResponse:
    content = SuccessResponse(data=data, request_id=request_id).model_dump(exclude_none=True)
    return JSONResponse(status_code=status_code, content=content)

def error_response(error: str, request_id: Optional[str] = None, status_code: int = 400, details: Optional[Any] = None) -> JSONResponse:
    content = ErrorResponse(error=error, request_id=request_id, details=details).model_dump(exclude_none=True)
    return JSONResponse(status_code=status_code, content=content)
