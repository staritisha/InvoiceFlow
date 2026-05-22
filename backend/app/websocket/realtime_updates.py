"""
Real-time WebSocket Updates
Endpoint + event handlers for live dashboard, activity stream, and KPI refresh.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.websocket.manager import manager
from app.database import get_db
from app.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ws", tags=["websocket"])


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@router.websocket("/connect")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(..., description="JWT access token"),
    db: AsyncSession = Depends(get_db),
):
    """
    Main WebSocket endpoint.
    Connect: ws://<host>/ws/connect?token=<jwt>
    """
    user = None
    try:
        # Validate token
        from app.core.security import decode_token
        from app.models import User
        from sqlalchemy import select
        payload = decode_token(token)
        user_id = payload.get("sub")
        if not user_id:
            await websocket.close(code=4001)
            return

        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            await websocket.close(code=4003)
            return

        team_id = str(user.team_id) if user.team_id else str(user.id)
        await manager.connect(websocket, str(user.id), team_id)

        # Send initial state snapshot
        await _send_initial_snapshot(websocket, user, db)

        # Listen for client messages
        while True:
            try:
                data = await websocket.receive_json()
                await _handle_client_message(data, websocket, user, db)
            except Exception as e:
                logger.warning(f"[WS] Message handling error: {e}")
                break

    except WebSocketDisconnect:
        logger.info(f"[WS] Client disconnected: user={getattr(user, 'id', 'unknown')}")
    except Exception as e:
        logger.error(f"[WS] Unexpected error: {e}")
    finally:
        await manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# Initial snapshot
# ---------------------------------------------------------------------------

async def _send_initial_snapshot(websocket, user, db: AsyncSession) -> None:
    """Send current KPIs and unread notification count on connect."""
    try:
        from app.models import Invoice, Notification
        from sqlalchemy import select, func, and_
        now = datetime.now(timezone.utc)

        # Quick KPI snapshot
        inv_result = await db.execute(
            select(
                func.count(Invoice.id).label("total"),
                func.sum(Invoice.balance_due).label("outstanding"),
            ).where(Invoice.user_id == user.id)
        )
        row = inv_result.first()

        notif_result = await db.execute(
            select(func.count(Notification.id)).where(
                and_(Notification.user_id == user.id, Notification.read == False)
            )
        )
        unread = notif_result.scalar() or 0

        snapshot = {
            "type": "initial_snapshot",
            "kpis": {
                "total_invoices": row.total or 0,
                "outstanding": float(row.outstanding or 0),
                "unread_notifications": unread,
            },
            "timestamp": now.isoformat(),
        }
        await websocket.send_json(snapshot)
    except Exception as e:
        logger.warning(f"[WS] Initial snapshot error: {e}")


# ---------------------------------------------------------------------------
# Client message handler
# ---------------------------------------------------------------------------

async def _handle_client_message(data: dict, websocket, user, db: AsyncSession) -> None:
    """Route incoming WebSocket messages from the client."""
    msg_type = data.get("type", "")

    if msg_type == "ping":
        await websocket.send_json({"type": "pong", "timestamp": datetime.now(timezone.utc).isoformat()})

    elif msg_type == "request_kpi_refresh":
        await _send_kpi_refresh(websocket, user, db)

    elif msg_type == "request_activity_feed":
        await _send_activity_feed(websocket, user, db)

    elif msg_type == "ai_chat":
        # Streaming AI response
        message = data.get("message", "")
        session_id = data.get("session_id", "default")
        await _stream_ai_response(message, session_id, websocket, user, db)

    else:
        await websocket.send_json({"type": "error", "message": f"Unknown event type: {msg_type}"})


# ---------------------------------------------------------------------------
# Event senders
# ---------------------------------------------------------------------------

async def _send_kpi_refresh(websocket, user, db: AsyncSession) -> None:
    try:
        from app.services.analytics_service import get_kpi_snapshot
        kpis = await get_kpi_snapshot(user.id, db)
        await websocket.send_json({"type": "kpi_refresh", "data": kpis, "timestamp": datetime.now(timezone.utc).isoformat()})
    except Exception as e:
        logger.warning(f"[WS] KPI refresh error: {e}")
        await websocket.send_json({"type": "kpi_refresh", "data": {}, "error": str(e)})


async def _send_activity_feed(websocket, user, db: AsyncSession) -> None:
    try:
        from app.models import Activity
        from sqlalchemy import select
        result = await db.execute(
            select(Activity)
            .where(Activity.team_id == user.team_id)
            .order_by(Activity.created_at.desc())
            .limit(20)
        )
        activities = result.scalars().all()
        feed = [
            {
                "id": str(a.id),
                "action_type": a.action_type,
                "entity_type": a.entity_type,
                "description": a.description,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in activities
        ]
        await websocket.send_json({"type": "activity_feed", "data": feed})
    except Exception as e:
        logger.warning(f"[WS] Activity feed error: {e}")


async def _stream_ai_response(message: str, session_id: str, websocket, user, db: AsyncSession) -> None:
    """Stream AI assistant tokens back to the client."""
    try:
        import httpx, json
        from app.config import settings
        async with httpx.AsyncClient(timeout=30) as client:
            async with client.stream(
                "POST",
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": "You are InvoiceFlow AI, a helpful business assistant."},
                        {"role": "user", "content": message},
                    ],
                    "stream": True,
                    "max_tokens": 500,
                },
            ) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        try:
                            chunk = json.loads(line[6:])
                            token = chunk["choices"][0]["delta"].get("content", "")
                            if token:
                                await websocket.send_json({"type": "ai_token", "token": token, "done": False})
                        except Exception:
                            pass
        await websocket.send_json({"type": "ai_token", "token": "", "done": True})
    except Exception as e:
        logger.error(f"[WS] AI stream error: {e}")
        await websocket.send_json({"type": "ai_error", "message": "AI response failed. Please try again."})


# ---------------------------------------------------------------------------
# Broadcast helpers (called from other parts of the app)
# ---------------------------------------------------------------------------

async def broadcast_invoice_created(team_id: str, invoice_data: dict) -> None:
    await manager.broadcast_team(team_id, {"type": "invoice_created", "data": invoice_data, "timestamp": datetime.now(timezone.utc).isoformat()})


async def broadcast_invoice_paid(team_id: str, invoice_data: dict) -> None:
    await manager.broadcast_team(team_id, {"type": "invoice_paid", "data": invoice_data, "timestamp": datetime.now(timezone.utc).isoformat()})


async def broadcast_analytics_updated(team_id: str, analytics: dict) -> None:
    await manager.broadcast_team(team_id, {"type": "analytics_updated", "data": analytics, "timestamp": datetime.now(timezone.utc).isoformat()})


async def broadcast_insight(team_id: str, insight: dict) -> None:
    await manager.broadcast_team(team_id, {"type": "ai_insight", "data": insight, "timestamp": datetime.now(timezone.utc).isoformat()})


async def get_ws_stats() -> dict:
    return manager.get_stats()