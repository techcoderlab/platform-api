# ─────────────────────────────────────────────────────
# Module   : app.core.middleware
# ─────────────────────────────────────────────────────
import time
import uuid
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

def setup_cors(app):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

class RequestTrackingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        
        start_time = time.time()
        
        response = await call_next(request)
        
        process_time = time.time() - start_time
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = str(process_time)
        
        # Log request if not health check
        if not request.url.path.startswith("/healthz"):
            logger.info(
                f"{request.method} {request.url.path} {response.status_code}",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_s": round(process_time, 4)
                }
            )
            
        return response

class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Exempt health checks and open docs from auth
        if request.url.path.startswith("/healthz") or request.url.path in ["/docs", "/redoc", "/openapi.json"]:
            return await call_next(request)
            
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            # Also check query param as fallback for simple clients
            api_key = request.query_params.get("api_key")
            
        if api_key != settings.API_KEY:
            logger.warning(f"Unauthorized access attempt to {request.url.path}")
            return JSONResponse(
                status_code=401,
                content={
                    "status": "error",
                    "error": "Unauthorized: Invalid or missing API key",
                    "request_id": getattr(request.state, "request_id", None)
                }
            )
            
        return await call_next(request)

def setup_middlewares(app):
    # Setup CORS
    setup_cors(app)
    
    # Custom middlewares (added in reverse order of execution)
    app.add_middleware(APIKeyAuthMiddleware)
    app.add_middleware(RequestTrackingMiddleware)
