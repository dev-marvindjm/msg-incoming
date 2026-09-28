import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import text

from app.api.v1.router import api_v1_router
from app.core.config import settings
from app.core.database import async_engine, async_session_maker, init_db
from app.core.middleware import (
    CorrelationIdAndTimingMiddleware,
    register_exception_handlers,
)
from app.services.telethon_client import telethon_service

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
)
logger = logging.getLogger("msg_ingestor")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages the application lifecycle:
    1. Initializes database tables and default admin operator on startup.
    2. Starts Telethon MTProto client if configured.
    3. Handles graceful teardown of MTProto connections and database pools on shutdown.
    """
    logger.info("=== Booting MSG Incoming Ingestor & Router Microservice ===")

    # 1. Initialize DB tables and seed admin user
    await init_db()

    # 2. Start Telethon client if enabled
    if settings.TELEGRAM_AUTO_START and settings.TELEGRAM_API_ID and settings.TELEGRAM_API_HASH:
        logger.info("Starting Telethon MTProto worker...")
        await telethon_service.start()
    else:
        logger.info("Telethon background client auto-start is disabled or credentials not provided.")

    logger.info(f"MSG Incoming is running on port {settings.PORT} ({settings.ENVIRONMENT})")

    yield

    logger.info("=== Shutting down MSG Incoming Ingestor & Router ===")
    # 1. Stop Telethon client
    await telethon_service.stop()

    # 2. Close Async Database Engine Pool
    await async_engine.dispose()
    logger.info("All background tasks and connection pools cleanly terminated.")


def create_app() -> FastAPI:
    """
    FastAPI Application Factory.
    """
    application = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=(
            "Selective Messaging Ingestor and Router microservice for Telegram (Telethon), Threads, "
            "and external Webhooks. Performs sender-based filtering (ALLOW / BLOCK / DEFAULT), "
            "dynamic global filtering (allow_any_sender / strict_whitelist), multi-tenant isolation, "
            "idempotent persistence, and automated dispatch to MetaTrader 5 / binary trading engines."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # 1. CORS Middleware
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 2. Request Correlation & Latency Middleware
    application.add_middleware(CorrelationIdAndTimingMiddleware)

    # 3. Global Exception Handling Handlers
    register_exception_handlers(application)

    # 4. Include API Routers (/api/v1)
    application.include_router(api_v1_router, prefix="/api")

    # 5. Root Info Endpoint
    @application.get("/", tags=["System"])
    async def root():
        return {
            "name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "environment": settings.ENVIRONMENT,
            "status": "operational",
            "docs": "/docs",
            "api_v1": "/api/v1",
        }

    # 6. Comprehensive Health & Readiness Check
    @application.get("/health", tags=["System"])
    async def health():
        db_status = "unknown"
        try:
            async with async_session_maker() as session:
                await session.exec(text("SELECT 1"))
                db_status = "healthy"
        except Exception as e:
            db_status = f"unhealthy: {str(e)}"

        telethon_connected = telethon_service.is_connected()

        return {
            "status": "healthy" if db_status == "healthy" else "degraded",
            "database": db_status,
            "telethon": {
                "configured": bool(settings.TELEGRAM_API_ID and settings.TELEGRAM_API_HASH),
                "connected": telethon_connected,
            },
            "broker_emitter_target": settings.BROKER_API_URL,
            "rust_app_target": settings.RUST_APP_URL,
        }

    return application


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
