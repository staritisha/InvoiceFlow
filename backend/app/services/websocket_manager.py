import logging
from fastapi import WebSocket

from app.core.state import app_state

logger = logging.getLogger("invoiceflow")


class WebSocketManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    def initialize(self) -> None:
        logger.info("WebSocket manager initialized")

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)
        team_id = websocket.headers.get("x-team-id", "default")
        app_state.active_teams.add(team_id)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        dead = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead.append(connection)
        for connection in dead:
            self.disconnect(connection)
        app_state.events_broadcasted += len(self.active_connections)

    async def shutdown(self) -> None:
        for connection in list(self.active_connections):
            try:
                await connection.close()
            except Exception:
                pass
        self.active_connections.clear()


ws_manager = WebSocketManager()
