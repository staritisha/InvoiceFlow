import logging
import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("invoiceflow.access")
REQUEST_ID_HEADER = "X-Request-ID"


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        request.state.request_id = request_id

        team_id = request.headers.get("X-Team-ID", "default")
        request.state.team_id = team_id

        user_id = request.headers.get("X-User-ID", "anonymous")
        request.state.user_id = user_id

        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)

        ai_tokens = getattr(request.state, "ai_tokens", 0)
        client_ip = request.client.host if request.client else "unknown"

        response.headers[REQUEST_ID_HEADER] = request_id

        logger.info(
            "request completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "response_time_ms": elapsed_ms,
                "team_id": team_id,
                "user_id": user_id,
                "endpoint": request.url.path,
                "ai_tokens": ai_tokens,
                "ip": client_ip,
            },
        )
        return response
