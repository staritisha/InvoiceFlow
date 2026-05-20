import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import settings
from app.middleware.logging import get_request_id

_buckets: dict[str, list[float]] = defaultdict(list)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in ("/health", "/ready", "/metrics", "/"):
            return await call_next(request)

        client = request.client.host if request.client else "unknown"
        now = time.time()
        window = _buckets[client]
        window[:] = [t for t in window if now - t < 60]

        if len(window) >= settings.rate_limit_per_minute:
            return JSONResponse(
                status_code=429,
                content={
                    "success": False,
                    "message": "Rate limit exceeded",
                    "request_id": get_request_id(request),
                },
            )

        window.append(now)
        return await call_next(request)
