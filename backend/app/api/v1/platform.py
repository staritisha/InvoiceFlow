import resource
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from starlette.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import models
from app.api.deps import get_db
from app.config import settings
from app.core.state import app_state
from app.database import engine
from app.scheduler import is_scheduler_running
from app.services.ai_service import ai_service
from app.services.redis_client import redis_client
from app.services.websocket_manager import ws_manager

router = APIRouter(tags=["Platform"])


@router.get("/")
def root():
    return {"message": "AI Invoice Intelligence Platform running"}


@router.get("/health")
async def health():
    db_ok = False
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        db_ok = False

    redis_ok = await redis_client.ping() if settings.redis_url else app_state.redis_connected
    return {
        "status": "healthy" if db_ok else "degraded",
        "database": "connected" if db_ok else "disconnected",
        "redis": "connected" if redis_ok else "in-memory",
        "ai_provider": "online" if ai_service.is_ready else "offline",
        "websocket": "active",
        "scheduler": "running" if is_scheduler_running() else "stopped",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ready")
async def readiness():
    db_ok = False
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        pass

    redis_ok = await redis_client.ping() if settings.redis_url else True
    scheduler_ok = is_scheduler_running()

    payload = {
        "ready": db_ok and redis_ok and scheduler_ok,
        "database": db_ok,
        "redis": redis_ok,
        "scheduler": scheduler_ok,
    }
    if not payload["ready"]:
        return JSONResponse(status_code=503, content=payload)
    return payload


@router.get("/db-check")
def db_check():
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1"))
        return {"database": "connected", "result": result.scalar()}


@router.get("/metrics")
def metrics(db: Session = Depends(get_db)):
    return {
        "total_invoices": db.query(models.Invoice).count(),
        "total_users": db.query(models.User).count(),
        "ai_requests_count": app_state.ai_requests_today,
        "reminders_sent": app_state.reminders_sent,
        "workflows_executed": app_state.workflows_executed,
        "active_websocket_clients": len(ws_manager.active_connections),
        "active_teams": len(app_state.active_teams),
        "events_broadcasted": app_state.events_broadcasted,
    }


@router.get("/system/info")
def system_info():
    usage = resource.getrusage(resource.RUSAGE_SELF)
    memory_mb = round(usage.ru_maxrss / (1024 * 1024), 2)
    return {
        "app_version": settings.app_version,
        "environment": settings.environment,
        "uptime_seconds": round(app_state.uptime_seconds(), 2),
        "queue_stats": {"pending_jobs": 0, "processed_today": app_state.workflows_executed},
        "memory_usage_mb": memory_mb,
        "ai_model_active": settings.ai_model,
        "feature_flags": {
            "ENABLE_AI": settings.enable_ai,
            "ENABLE_WORKFLOWS": settings.enable_workflows,
            "ENABLE_VOICE": settings.enable_voice,
        },
    }


@router.get("/ai/status")
def ai_status():
    return ai_service.status()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_json({"type": "echo", "payload": data})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
