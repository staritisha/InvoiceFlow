"""
WebSocket Connection Manager
Handles connection pooling, team rooms, personal channels, and broadcasting.
"""

import logging
import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages WebSocket connections with team-room grouping and personal channels.
    Thread-safe for use with asyncio.
    """

    def __init__(self):
        # team_id → set of WebSocket connections
        self._team_connections: dict[str, set[WebSocket]] = {}
        # user_id → WebSocket (one active socket per user)
        self._user_connections: dict[str, WebSocket] = {}
        # track metadata per socket
        self._socket_meta: dict[WebSocket, dict] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self, websocket: WebSocket, user_id: str, team_id: str) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        async with self._lock:
            # Register in team room
            if team_id not in self._team_connections:
                self._team_connections[team_id] = set()
            self._team_connections[team_id].add(websocket)

            # Register personal channel
            self._user_connections[user_id] = websocket

            # Store metadata
            self._socket_meta[websocket] = {
                "user_id": user_id,
                "team_id": team_id,
                "connected_at": datetime.now(timezone.utc).isoformat(),
            }

        logger.info(f"[WS] Connected user={user_id} team={team_id}. Total team connections: {len(self._team_connections.get(team_id, set()))}")
        await self._send_json(websocket, {"type": "connected", "user_id": user_id, "timestamp": datetime.now(timezone.utc).isoformat()})

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket from all registries."""
        async with self._lock:
            meta = self._socket_meta.pop(websocket, {})
            team_id = meta.get("team_id")
            user_id = meta.get("user_id")

            if team_id and team_id in self._team_connections:
                self._team_connections[team_id].discard(websocket)
                if not self._team_connections[team_id]:
                    del self._team_connections[team_id]

            if user_id and self._user_connections.get(user_id) is websocket:
                del self._user_connections[user_id]

        logger.info(f"[WS] Disconnected user={user_id}")

    # ------------------------------------------------------------------
    # Sending helpers
    # ------------------------------------------------------------------

    async def _send_json(self, websocket: WebSocket, data: dict) -> bool:
        """Send JSON to a single socket. Returns False if failed."""
        try:
            await websocket.send_json(data)
            return True
        except Exception as e:
            logger.warning(f"[WS] Send failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Broadcast methods
    # ------------------------------------------------------------------

    async def broadcast_team(self, team_id: str, event: dict) -> int:
        """Broadcast event to all connections in a team room. Returns send count."""
        sockets = list(self._team_connections.get(team_id, set()))
        dead: list[WebSocket] = []
        sent = 0
        for ws in sockets:
            ok = await self._send_json(ws, event)
            if ok:
                sent += 1
            else:
                dead.append(ws)
        # Cleanup dead sockets
        for ws in dead:
            await self.disconnect(ws)
        return sent

    async def send_personal_message(self, user_id: str, event: dict) -> bool:
        """Send event to a specific user's personal channel."""
        ws = self._user_connections.get(user_id)
        if not ws:
            return False
        ok = await self._send_json(ws, event)
        if not ok:
            await self.disconnect(ws)
        return ok

    async def broadcast_dashboard_update(self, team_id: str, update_type: str, data: dict) -> None:
        """Convenience wrapper for dashboard refresh events."""
        event = {
            "type": "dashboard_refresh",
            "update_type": update_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.broadcast_team(team_id, event)

    async def broadcast_kpi_update(self, team_id: str, kpis: dict) -> None:
        event = {"type": "kpi_update", "kpis": kpis, "timestamp": datetime.now(timezone.utc).isoformat()}
        await self.broadcast_team(team_id, event)

    async def broadcast_activity(self, team_id: str, activity: dict) -> None:
        event = {"type": "activity_feed", "activity": activity, "timestamp": datetime.now(timezone.utc).isoformat()}
        await self.broadcast_team(team_id, event)

    async def broadcast_notification(self, user_id: str, notification: dict) -> None:
        event = {"type": "notification", "notification": notification, "timestamp": datetime.now(timezone.utc).isoformat()}
        await self.send_personal_message(user_id, event)

    async def stream_ai_token(self, user_id: str, token: str, done: bool = False) -> None:
        """Stream individual AI response tokens to a user."""
        event = {"type": "ai_stream", "token": token, "done": done}
        await self.send_personal_message(user_id, event)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_active_users(self) -> list[str]:
        return list(self._user_connections.keys())

    def get_team_connection_count(self, team_id: str) -> int:
        return len(self._team_connections.get(team_id, set()))

    def get_total_connections(self) -> int:
        return len(self._socket_meta)

    def is_user_online(self, user_id: str) -> bool:
        return user_id in self._user_connections

    def get_stats(self) -> dict:
        return {
            "total_connections": self.get_total_connections(),
            "active_users": len(self._user_connections),
            "active_teams": len(self._team_connections),
            "team_details": {tid: len(sockets) for tid, sockets in self._team_connections.items()},
        }


# Global singleton
manager = ConnectionManager()