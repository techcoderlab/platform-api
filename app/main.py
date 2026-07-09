# ─────────────────────────────────────────────────────
# Module   : app.main
# ─────────────────────────────────────────────────────
import asyncio
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

# Import scraper dependencies
from app.modules.web_scraper.infrastructure.session_store import InMemorySessionStore
from app.modules.web_scraper.infrastructure.browser_pool import BrowserPool
from app.modules.web_scraper.infrastructure.scraper import PlaywrightScraper
from app.modules.web_scraper.infrastructure.repository import InMemoryAnalysisRepository
from app.modules.web_scraper.application.task_queue import TaskQueue
from app.modules.web_scraper.application.analysis_service import AnalysisService
from app.modules.web_scraper.presentation.routes import router as web_scraper_router

logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    setup_logging()
    logger.info(f"Starting {settings.SERVICE_NAME} in {settings.ENV} environment")
    
    # ── Web Scraper Dependencies ──
    if settings.ENABLE_WEB_SCRAPER:
        logger.info("Initializing Web Scraper dependencies")
        session_store = InMemorySessionStore()
        app.state.session_cleanup_task = asyncio.create_task(session_store.start_cleanup_task())

        browser_pool = BrowserPool(settings, session_store)
        await browser_pool.start()

        scraper = PlaywrightScraper(pool=browser_pool)
        repository = InMemoryAnalysisRepository()

        task_queue = TaskQueue(
            max_size=settings.MAX_QUEUE_SIZE,
            worker_count=settings.WORKER_COUNT,
        )
        await task_queue.start()

        analysis_service = AnalysisService(
            browser=scraper,
            repository=repository,
            queue=task_queue,
        )

        # Attach to app.state
        app.state.browser_pool = browser_pool
        app.state.task_queue = task_queue
        app.state.analysis_service = analysis_service
        app.state.scraper = scraper

    app.state.settings = settings
    yield
    
    # Shutdown
    logger.info(f"Shutting down {settings.SERVICE_NAME}")
    
    if settings.ENABLE_WEB_SCRAPER:
        if hasattr(app.state, "session_cleanup_task"):
            app.state.session_cleanup_task.cancel()
            
        await app.state.task_queue.stop()
        await app.state.scraper.close()
        await app.state.browser_pool.stop()
        logger.info("web_scraper shutdown complete")

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
    
    if settings.ENABLE_IMAGE_FACTORY:
        app.include_router(
            image_factory_router,
            prefix=f"{settings.api_base}/image-factory"
        )
        logger.info("Registered Image Factory endpoints")
        
    if settings.ENABLE_WEB_SCRAPER:
        app.include_router(
            web_scraper_router,
            prefix=f"{settings.api_base}/web-scraper"
        )
        logger.info("Registered Web Scraper endpoints")
    
    return app

app = create_app()
