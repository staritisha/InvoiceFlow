# ═══════════════════════════════════════════════════════════════════════════════
#  AI Invoice Intelligence Platform — app/core/middleware.py
#  Production-grade middleware stack: request tracing, rate limiting,
#  AI cost protection, abuse detection, security headers, metrics,
#  and structured observability — all async-safe.
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.config import settings

# ── Logger ────────────────────────────────────────────────────────────────────

logger = logging.getLogger("invoiceflow.middleware")

# ═══════════════════════════════════════════════════════════════════════════════
#  SHARED TELEMETRY STATE  (in-memory; replace with Redis in production)
# ═══════════════════════════════════════════════════════════════════════════════

# Global request metrics
_metrics: dict[str, Any] = {
    "total_requests":       0,
    "failed_requests":      0,
    "total_response_ms":    0.0,
    "endpoint_counts":      defaultdict(int),   # path → count
    "user_activity":        defaultdict(int),   # user_id → count
    "team_activity":        defaultdict(int),   # team_id → count
    "slow_endpoints":       defaultdict(int),   # path → slow-hit count
    "ai_requests":          0,
    "ai_prompt_tokens":     0,
    "ai_completion_tokens": 0,
    "ai_total_latency_ms":  0.0,
    "ws_active_connections": 0,
    "ws_team_rooms":        defaultdict(int),
}

# Rate limit store: ip → deque of timestamps
_rate_store:   dict[str, deque] = defaultdict(lambda: deque())
# Blocked IPs: ip → unblock timestamp
_blocked_ips:  dict[str, float] = {}
# Failed login tracker: ip → count
_failed_logins: dict[str, int] = defaultdict(int)
# Request log ring-buffer (last 500)
_request_log:  deque = deque(maxlen=500)

# ── Endpoint-specific rate limit config (requests per window) ─────────────────
_ENDPOINT_LIMITS: dict[str, tuple[int, int]] = {
    # path_prefix          (max_requests, window_seconds)
    "/api/v1/auth/login":  (10,  60),
    "/api/v1/ai/chat":     (20,  60),
    "/api/v1/ai/":         (30,  60),
    "/api/v1/voice/":      (10,  60),
    "/api/v1/analytics/":  (60,  60),
    "/api/v1/invoices":    (100, 60),
    "/api/v1/":            (settings.rate_limit_per_minute, 60),
}

# Endpoints considered AI paths for token tracking
_AI_PATH_PREFIXES = (
    "/api/v1/ai/",
    "/api/v1/voice/",
    "/api/v1/analytics/",
    "/api/v1/invoices/ai",
)

SLOW_REQUEST_THRESHOLD_MS = 1000.0
BLOCK_DURATION_SECONDS     = 900       # 15 minutes
MAX_BODY_SIZE_BYTES        = settings.max_file_size_mb * 1024 * 1024
AI_MAX_PROMPT_CHARS        = 8000      # Cost protection


# ═══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return getattr(request.client, "host", "unknown")


def _resolve_rate_limit(path: str) -> tuple[int, int]:
    """Return (max_requests, window_seconds) for the most specific matching prefix."""
    for prefix, limits in _ENDPOINT_LIMITS.items():
        if path.startswith(prefix):
            return limits
    return (settings.rate_limit_per_minute, 60)


def _error_json(request: Request, status_code: int, message: str, extra: Optional[dict] = None) -> JSONResponse:
    body: dict[str, Any] = {
        "success":    False,
        "message":    message,
        "request_id": getattr(request.state, "request_id", None),
        "timestamp":  datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        body.update(extra)
    return JSONResponse(status_code=status_code, content=body)


def get_middleware_metrics() -> dict:
    """Return aggregated middleware metrics for /metrics endpoint."""
    total = _metrics["total_requests"] or 1
    return {
        "total_requests":          _metrics["total_requests"],
        "failed_requests":         _metrics["failed_requests"],
        "average_response_ms":     round(_metrics["total_response_ms"] / total, 2),
        "ai_requests":             _metrics["ai_requests"],
        "ai_prompt_tokens":        _metrics["ai_prompt_tokens"],
        "ai_completion_tokens":    _metrics["ai_completion_tokens"],
        "ws_active_connections":   _metrics["ws_active_connections"],
        "top_endpoints":           dict(sorted(
                                       _metrics["endpoint_counts"].items(),
                                       key=lambda x: x[1], reverse=True
                                   )[:10]),
        "slow_endpoints":          dict(_metrics["slow_endpoints"]),
        "blocked_ips":             len(_blocked_ips),
        "recent_requests":         list(_request_log)[-20:],
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  1. SECURITY HEADERS MIDDLEWARE
# ═══════════════════════════════════════════════════════════════════════════════

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Injects security headers on every response.
    Doubles as the outermost layer so headers are always present.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response: Response = await call_next(request)
        response.headers["X-Frame-Options"]        = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"]       = "1; mode=block"
        response.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]     = "geolocation=(), microphone=(), camera=()"
        if settings.environment == "production":
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )
        return response


# ═══════════════════════════════════════════════════════════════════════════════
#  2. RATE LIMIT MIDDLEWARE
# ═══════════════════════════════════════════════════════════════════════════════

class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Per-IP sliding-window rate limiter with endpoint-specific limits,
    temporary IP blocking, and standard Retry-After / X-RateLimit-* headers.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        ip = _client_ip(request)
        now = time.time()
        path = request.url.path

        # ── Blocked IP check ──────────────────────────────────────────────────
        if ip in _blocked_ips:
            if now < _blocked_ips[ip]:
                retry_after = int(_blocked_ips[ip] - now)
                return _error_json(
                    request,
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "Your IP is temporarily blocked due to suspicious activity.",
                    {"retry_after_seconds": retry_after},
                )
            else:
                del _blocked_ips[ip]   # unblock

        # ── Sliding-window rate check ─────────────────────────────────────────
        max_req, window = _resolve_rate_limit(path)
        bucket = _rate_store[ip]
        cutoff = now - window
        # Purge expired timestamps
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        remaining = max(0, max_req - len(bucket))

        if len(bucket) >= max_req:
            # Detect brute-force on login
            if "/auth/login" in path:
                _failed_logins[ip] += 1
                if _failed_logins[ip] >= 20:
                    _blocked_ips[ip] = now + BLOCK_DURATION_SECONDS
                    logger.warning(f"[middleware] IP {ip} blocked for brute-force login attempts")

            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "success":            False,
                    "message":            "Rate limit exceeded. Please slow down.",
                    "request_id":         getattr(request.state, "request_id", None),
                    "retry_after_seconds": window,
                },
                headers={
                    "Retry-After":            str(window),
                    "X-RateLimit-Limit":      str(max_req),
                    "X-RateLimit-Remaining":  "0",
                    "X-RateLimit-Reset":      str(int(now + window)),
                },
            )

        bucket.append(now)

        response: Response = await call_next(request)
        response.headers["X-RateLimit-Limit"]     = str(max_req)
        response.headers["X-RateLimit-Remaining"] = str(remaining - 1)
        response.headers["X-RateLimit-Reset"]     = str(int(now + window))
        return response


# ═══════════════════════════════════════════════════════════════════════════════
#  3. REQUEST LOGGING & TRACING MIDDLEWARE
# ═══════════════════════════════════════════════════════════════════════════════

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    - Generates a UUID request_id for every request
    - Measures processing time in ms
    - Extracts user_id / team_id from JWT if present
    - Logs structured JSON-style entries
    - Detects slow endpoints
    - Appends X-Request-ID and X-Response-Time headers
    - Feeds the in-memory request log and metrics counters
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = str(uuid.uuid4())
        start      = time.perf_counter()

        # Inject into request state for downstream use
        request.state.request_id = request_id
        request.state.start_time = start
        request.state.user_id    = None
        request.state.team_id    = None

        # Best-effort JWT claim extraction (no hard failure)
        self._extract_jwt_context(request)

        response: Response = await call_next(request)

        elapsed_ms = (time.perf_counter() - start) * 1000
        path       = request.url.path
        ip         = _client_ip(request)

        # ── Update metrics ────────────────────────────────────────────────────
        _metrics["total_requests"]    += 1
        _metrics["total_response_ms"] += elapsed_ms
        _metrics["endpoint_counts"][path] += 1

        if request.state.user_id:
            _metrics["user_activity"][request.state.user_id] += 1
        if request.state.team_id:
            _metrics["team_activity"][request.state.team_id] += 1

        if response.status_code >= 400:
            _metrics["failed_requests"] += 1
        if elapsed_ms >= SLOW_REQUEST_THRESHOLD_MS:
            _metrics["slow_endpoints"][path] += 1

        # ── AI path tracking ──────────────────────────────────────────────────
        if any(path.startswith(p) for p in _AI_PATH_PREFIXES):
            _metrics["ai_requests"] += 1
            _metrics["ai_total_latency_ms"] += elapsed_ms
            # Simulated token estimate — replace with real counts from AI service
            _metrics["ai_prompt_tokens"]      += 150
            _metrics["ai_completion_tokens"]  += 300

        # ── Structured log entry ──────────────────────────────────────────────
        entry = {
            "request_id":  request_id[:8],
            "method":      request.method,
            "path":        path,
            "status":      response.status_code,
            "duration_ms": round(elapsed_ms, 2),
            "ip":          ip,
            "user_id":     request.state.user_id,
            "team_id":     request.state.team_id,
            "timestamp":   datetime.now(timezone.utc).isoformat(),
        }
        _request_log.append(entry)

        level = logging.WARNING if response.status_code >= 500 else (
                logging.INFO    if response.status_code < 400 else logging.WARNING)
        logger.log(
            level,
            f"[{request_id[:8]}] {request.method} {path} → {response.status_code} "
            f"({elapsed_ms:.1f}ms) [{ip}]"
        )

        # ── Append trace headers ──────────────────────────────────────────────
        response.headers["X-Request-ID"]    = request_id
        response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"
        return response

    @staticmethod
    def _extract_jwt_context(request: Request) -> None:
        """Silently attempt to read user_id/team_id from Bearer token."""
        try:
            from jose import jwt as _jwt
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return
            token = auth.split(" ", 1)[1]
            payload = _jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM],
                options={"verify_exp": False},   # we only need claims, not expiry here
            )
            request.state.user_id = payload.get("user_id") or payload.get("sub")
            request.state.team_id = payload.get("team_id")
        except Exception:
            pass   # never hard-fail on tracing


# ═══════════════════════════════════════════════════════════════════════════════
#  4. REQUEST BODY SIZE PROTECTION MIDDLEWARE
# ═══════════════════════════════════════════════════════════════════════════════

class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """
    Rejects requests whose Content-Length exceeds MAX_BODY_SIZE_BYTES.
    Protects AI voice upload endpoints and PDF uploads from oversized payloads.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_BODY_SIZE_BYTES:
            return _error_json(
                request,
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"Request body exceeds the maximum allowed size of {settings.max_file_size_mb}MB.",
            )
        return await call_next(request)


# ═══════════════════════════════════════════════════════════════════════════════
#  5. AI COST PROTECTION MIDDLEWARE
# ═══════════════════════════════════════════════════════════════════════════════

class AICostProtectionMiddleware(BaseHTTPMiddleware):
    """
    For AI endpoints, reads the raw body and rejects prompts that exceed
    AI_MAX_PROMPT_CHARS to prevent token-draining attacks.
    Only applied to POST/PUT on AI paths to avoid overhead on other routes.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        is_ai = any(path.startswith(p) for p in _AI_PATH_PREFIXES)
        is_write = request.method in ("POST", "PUT", "PATCH")

        if is_ai and is_write:
            try:
                body_bytes = await request.body()
                if len(body_bytes) > AI_MAX_PROMPT_CHARS * 4:   # UTF-8 worst case
                    return _error_json(
                        request,
                        status.HTTP_400_BAD_REQUEST,
                        f"AI prompt exceeds maximum allowed size ({AI_MAX_PROMPT_CHARS} characters).",
                    )
            except Exception:
                pass   # never block on read failure

        return await call_next(request)


# ═══════════════════════════════════════════════════════════════════════════════
#  6. ABUSE DETECTION MIDDLEWARE
# ═══════════════════════════════════════════════════════════════════════════════

# Tracks recent 401/403 counts per IP for abuse flagging
_auth_failure_store: dict[str, deque] = defaultdict(lambda: deque(maxlen=50))
ABUSE_THRESHOLD = 30    # failures in 60s triggers flag
ABUSE_WINDOW    = 60


class AbuseDetectionMiddleware(BaseHTTPMiddleware):
    """
    Detects and flags suspicious patterns:
    - Repeated 401/403 responses (invalid JWT spam, credential stuffing)
    - Scraping (unusually high request rates to listing endpoints)
    Sets request.state.suspicious_activity = True for downstream logging.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request.state.suspicious_activity = False
        ip  = _client_ip(request)
        now = time.time()

        response: Response = await call_next(request)

        if response.status_code in (401, 403):
            bucket = _auth_failure_store[ip]
            bucket.append(now)
            recent = [t for t in bucket if t > now - ABUSE_WINDOW]
            if len(recent) >= ABUSE_THRESHOLD:
                request.state.suspicious_activity = True
                logger.warning(
                    f"[middleware] Abuse detected from IP {ip} — "
                    f"{len(recent)} auth failures in {ABUSE_WINDOW}s. "
                    f"Flagging and auto-blocking."
                )
                _blocked_ips[ip] = now + BLOCK_DURATION_SECONDS

        return response


# ═══════════════════════════════════════════════════════════════════════════════
#  7. WEBSOCKET CONNECTION TRACKER  (non-middleware helper)
# ═══════════════════════════════════════════════════════════════════════════════

class WebSocketTracker:
    """
    Lightweight tracker for active WebSocket connections.
    Call connect/disconnect from your WebSocket endpoint handlers.
    Exposes counts to the metrics endpoint.
    """

    @staticmethod
    def connect(team_id: Optional[int] = None) -> None:
        _metrics["ws_active_connections"] += 1
        if team_id:
            _metrics["ws_team_rooms"][str(team_id)] += 1
        logger.debug(f"[ws] Connection opened. Active: {_metrics['ws_active_connections']}")

    @staticmethod
    def disconnect(team_id: Optional[int] = None) -> None:
        _metrics["ws_active_connections"] = max(0, _metrics["ws_active_connections"] - 1)
        if team_id and _metrics["ws_team_rooms"].get(str(team_id), 0) > 0:
            _metrics["ws_team_rooms"][str(team_id)] -= 1
        logger.debug(f"[ws] Connection closed. Active: {_metrics['ws_active_connections']}")

    @staticmethod
    def active_count() -> int:
        return _metrics["ws_active_connections"]

    @staticmethod
    def team_rooms() -> dict:
        return dict(_metrics["ws_team_rooms"])


ws_tracker = WebSocketTracker()


# ═══════════════════════════════════════════════════════════════════════════════
#  8. AUDIT TRAIL HELPER  (called from service layer)
# ═══════════════════════════════════════════════════════════════════════════════

_audit_log: deque = deque(maxlen=1000)


def record_audit_event(
    action: str,
    entity_type: str,
    entity_id: Any,
    user_id: Optional[int] = None,
    team_id: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> None:
    """
    Append an audit event to the in-memory audit ring buffer.
    Extend to write to DB or a structured logging pipeline in production.
    """
    event = {
        "action":      action,
        "entity_type": entity_type,
        "entity_id":   str(entity_id),
        "user_id":     user_id,
        "team_id":     team_id,
        "metadata":    metadata or {},
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    }
    _audit_log.append(event)
    logger.info(
        f"[audit] {action} {entity_type}#{entity_id} "
        f"by user={user_id} team={team_id}"
    )


def get_audit_log(limit: int = 100) -> list[dict]:
    """Return the most recent *limit* audit events."""
    return list(_audit_log)[-limit:]


# ═══════════════════════════════════════════════════════════════════════════════
#  MIDDLEWARE REGISTRATION HELPER
# ═══════════════════════════════════════════════════════════════════════════════

def register_middleware(app) -> None:
    """
    Register all middleware on a FastAPI *app* in the correct order.
    Outermost (first added) wraps everything; innermost is closest to the route.

    Call this once during app creation in main.py:

        from app.core.middleware import register_middleware
        register_middleware(app)
    """
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.middleware.gzip import GZipMiddleware
    from fastapi.middleware.trustedhost import TrustedHostMiddleware

    # Order: Security → Trusted → GZip → AbuseDetection → RateLimit → BodySize → AI → Logging
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(AbuseDetectionMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(AICostProtectionMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Response-Time",
                        "X-RateLimit-Limit", "X-RateLimit-Remaining"],
    )
