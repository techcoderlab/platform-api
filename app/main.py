# ─────────────────────────────────────────────────────
# Module   : app.main
# ─────────────────────────────────────────────────────
from contextlib import asynccontextmanager
from fastapi import FastAPI
import importlib

from app.core.config import settings
from app.core.logging import setup_logging, get_logger
from app.core.middleware import setup_middlewares
from app.core.errors import AppError, global_exception_handler, app_error_handler

# Import feature modules
from app.modules.health.routes import router as health_router
from app.modules.image_factory.routes import router as image_factory_router

logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    setup_logging()
    logger.info(f"Starting {settings.SERVICE_NAME} in {settings.ENV} environment")
    yield
    # Shutdown
    logger.info(f"Shutting down {settings.SERVICE_NAME}")

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.SERVICE_NAME,
        version=settings.API_VERSION,
        lifespan=lifespan
    )
    
    # Register Middlewares
    setup_middlewares(app)
    
    # Register Exception Handlers
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(Exception, global_exception_handler)
    
    # Register Routers
    app.include_router(health_router)
    app.include_router(
        image_factory_router,
        prefix=f"{settings.api_base}/image-factory"
    )
    
    return app

app = create_app()
