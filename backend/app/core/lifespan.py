import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.state import app_state
from app.database import engine, Base
from app.scheduler import start_scheduler, stop_scheduler, is_scheduler_running
from app.services.ai_service import ai_service
from app.services.redis_client import redis_client
from app.services.websocket_manager import ws_manager
from app.config import settings

logger = logging.getLogger("invoiceflow")


def _print_startup_banner() -> None:
    redis_status = "Connected" if app_state.redis_connected else "In-memory"
    scheduler_status = "Running" if app_state.scheduler_running else "Stopped"
    print(
        "\n=====================================\n"
        " AI Invoice Intelligence Platform\n"
        f" Environment: {settings.environment.title()}\n"
        f" AI Provider: {settings.ai_provider}\n"
        f" Redis: {redis_status}\n"
        f" Scheduler: {scheduler_status}\n"
        " WebSockets: Enabled\n"
        "=====================================\n"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)

    app_state.redis_connected = await redis_client.connect()
    start_scheduler()
    app_state.scheduler_running = is_scheduler_running()

    if settings.enable_ai:
        await ai_service.warmup()
        app_state.ai_warmed_up = ai_service.is_ready

    if settings.enable_workflows:
        app_state.record_workflow()

    ws_manager.initialize()
    _print_startup_banner()
    logger.info("Application startup complete")

    yield

    stop_scheduler()
    app_state.scheduler_running = False
    await redis_client.close()
    await ws_manager.shutdown()
    logger.info("Application shutdown complete — logs flushed")
