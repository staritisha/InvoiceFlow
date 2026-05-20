from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.state import app_state


class TeamContextMiddleware(BaseHTTPMiddleware):
    """Injects team context from headers for multi-tenant style requests."""

    async def dispatch(self, request: Request, call_next):
        team_id = request.headers.get("X-Team-ID", "default")
        request.state.team_id = team_id
        app_state.active_teams.add(team_id)
        return await call_next(request)
