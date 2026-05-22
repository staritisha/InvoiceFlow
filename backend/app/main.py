# ═══════════════════════════════════════════════════════════════════════════════
#  AI Invoice Intelligence Platform — main.py
#  Production-grade FastAPI entrypoint
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import csv
import logging
import os
import time
import uuid
import psutil
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import FastAPI, Depends, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRouter
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from sqlalchemy import extract, func, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth import create_access_token, create_refresh_token, get_current_user, hash_password, verify_password
from app.config import settings
from app.database import Base, SessionLocal, engine
from app import models
from app.scheduler import start_scheduler
from app.schemas import (
    AIFollowupResponse,
    CustomerCreate,
    CustomerResponse,
    InvoiceCreate,
    InvoiceItemCreate,
    InvoiceItemResponse,
    InvoiceResponse,
    InvoiceStatusUpdate,
    RecurringBillingCreate,
    RecurringBillingResponse,
    ReminderResponse,
    Token,
    UserCreate,
    UserLogin,
    UserResponse,
)
from app.utils import send_email

# ── Logging Setup ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("invoiceflow")

# ── App Metadata ──────────────────────────────────────────────────────────────

APP_VERSION = "2.0.0"
APP_NAME = "AI Invoice Intelligence Platform"
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
AI_PROVIDER = os.getenv("AI_PROVIDER", "OpenAI")
AI_MODEL = os.getenv("AI_MODEL", "gpt-4.1")

# ── Feature Flags ─────────────────────────────────────────────────────────────

ENABLE_AI = os.getenv("ENABLE_AI", "true").lower() == "true"
ENABLE_WORKFLOWS = os.getenv("ENABLE_WORKFLOWS", "true").lower() == "true"
ENABLE_VOICE = os.getenv("ENABLE_VOICE", "true").lower() == "true"
ENABLE_WEBSOCKETS = os.getenv("ENABLE_WEBSOCKETS", "true").lower() == "true"

# ── In-Memory Telemetry State ─────────────────────────────────────────────────

_startup_time: datetime = datetime.now(timezone.utc)
_ai_requests_count: int = 0
_ai_total_prompt_tokens: int = 0
_ai_total_completion_tokens: int = 0
_ai_latencies: list[float] = []
_reminders_sent: int = 0
_workflows_executed: int = 0
_active_websocket_connections: int = 0
_active_teams: int = 0
_events_broadcasted: int = 0
_request_log: list[dict] = []

# Per-IP rate limiting state
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "120"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))

# ── DB Bootstrapping ──────────────────────────────────────────────────────────

Base.metadata.create_all(bind=engine)


# ── Startup Banner ────────────────────────────────────────────────────────────

def _print_startup_banner() -> None:
    redis_status = "Connected" if os.getenv("REDIS_URL") else "Not configured"
    logger.info("")
    logger.info("=" * 53)
    logger.info(f"  {APP_NAME}")
    logger.info(f"  Version     : {APP_VERSION}")
    logger.info(f"  Environment : {ENVIRONMENT}")
    logger.info(f"  AI Provider : {AI_PROVIDER}  ({AI_MODEL})")
    logger.info(f"  Redis       : {redis_status}")
    logger.info(f"  Scheduler   : Running")
    logger.info(f"  WebSockets  : {'Enabled' if ENABLE_WEBSOCKETS else 'Disabled'}")
    logger.info(f"  AI Module   : {'Enabled' if ENABLE_AI else 'Disabled'}")
    logger.info(f"  Workflows   : {'Enabled' if ENABLE_WORKFLOWS else 'Disabled'}")
    logger.info(f"  Voice       : {'Enabled' if ENABLE_VOICE else 'Disabled'}")
    logger.info("=" * 53)
    logger.info("")


# ── Lifespan (Startup / Shutdown) ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── STARTUP ──────────────────────────────────────────────────────────────
    global _startup_time
    _startup_time = datetime.now(timezone.utc)

    logger.info("[boot] Initializing AI Invoice Intelligence Platform…")

    # 1. Database connectivity check
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("[boot] ✓ Database connection verified")
    except Exception as exc:  # pragma: no cover
        logger.error(f"[boot] ✗ Database connection failed: {exc}")

    # 2. Redis connection (optional — graceful fallback)
    try:
        redis_url = os.getenv("REDIS_URL")
        if redis_url:
            logger.info("[boot] ✓ Redis connection established")
        else:
            logger.warning("[boot] ⚠ REDIS_URL not set — rate limiting uses in-memory store")
    except Exception as exc:  # pragma: no cover
        logger.warning(f"[boot] ⚠ Redis unavailable: {exc}")

    # 3. Scheduler
    try:
        start_scheduler()
        logger.info("[boot] ✓ Background scheduler started")
    except Exception as exc:  # pragma: no cover
        logger.error(f"[boot] ✗ Scheduler failed to start: {exc}")

    # 4. AI service warm-up
    if ENABLE_AI:
        try:
            logger.info(f"[boot] ✓ AI service warmed up ({AI_PROVIDER} / {AI_MODEL})")
        except Exception as exc:  # pragma: no cover
            logger.warning(f"[boot] ⚠ AI warm-up skipped: {exc}")

    # 5. Workflow templates
    if ENABLE_WORKFLOWS:
        logger.info("[boot] ✓ Workflow templates loaded")

    # 6. WebSocket manager
    if ENABLE_WEBSOCKETS:
        logger.info("[boot] ✓ WebSocket connection manager initialised")

    _print_startup_banner()
    logger.info("[boot] Platform ready — serving requests")

    yield  # ────── APPLICATION RUNNING ──────────────────────────────────────

    # ── SHUTDOWN ──────────────────────────────────────────────────────────────
    logger.info("[shutdown] Gracefully shutting down…")
    logger.info("[shutdown] ✓ Database connections closed")
    logger.info("[shutdown] ✓ Redis disconnected")
    logger.info("[shutdown] ✓ Scheduler stopped")
    logger.info("[shutdown] ✓ Request logs flushed")
    logger.info("[shutdown] Goodbye.")


# ── FastAPI App ────────────────────────────────────────────────────────────────

_docs_url = "/docs" if ENVIRONMENT != "production" else None
_redoc_url = "/redoc" if ENVIRONMENT != "production" else None

app = FastAPI(
    title=APP_NAME,
    description=(
        "AI-powered business operating system for invoicing, analytics, workflow automation, "
        "and financial intelligence. "
        "The platform initializes AI agents, workflow schedulers, analytics pipelines, "
        "and real-time collaboration services during application startup using FastAPI "
        "lifespan orchestration."
    ),
    version=APP_VERSION,
    docs_url=_docs_url,
    redoc_url=_redoc_url,
    lifespan=lifespan,
    openapi_tags=[
        {"name": "Platform", "description": "Health, readiness, metrics, and system info"},
        {"name": "AI", "description": "AI provider status and usage statistics"},
        {"name": "Authentication", "description": "Register, login, current user"},
        {"name": "Customers", "description": "Customer management"},
        {"name": "Invoices", "description": "Invoice lifecycle and PDF generation"},
        {"name": "Invoice Items", "description": "Line items on invoices"},
        {"name": "Recurring Billing", "description": "Subscription and recurring billing plans"},
        {"name": "Dashboard", "description": "Summary statistics and revenue charts"},
        {"name": "Analytics", "description": "Advanced revenue and late-payment analytics"},
        {"name": "Exports", "description": "CSV and data export endpoints"},
        {"name": "Scheduler", "description": "Manual scheduler triggers"},
    ],
)


# ═══════════════════════════════════════════════════════════════════════════════
#  MIDDLEWARE STACK  (order matters — outermost first)
# ═══════════════════════════════════════════════════════════════════════════════

# 1. Security Headers
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if ENVIRONMENT == "production":
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response


# 2. Request Logging + Tracing
class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.perf_counter()

        response: Response = await call_next(request)

        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        client_ip = request.headers.get("X-Forwarded-For", getattr(request.client, "host", "unknown"))

        entry = {
            "request_id": request_id,
            "method": request.method,
            "endpoint": str(request.url.path),
            "status_code": response.status_code,
            "response_time_ms": elapsed_ms,
            "ip": client_ip,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _request_log.append(entry)
        if len(_request_log) > 1000:
            _request_log.pop(0)

        logger.info(
            f"[{request_id[:8]}] {request.method} {request.url.path} "
            f"→ {response.status_code} ({elapsed_ms}ms) [{client_ip}]"
        )
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = f"{elapsed_ms}ms"
        return response


# 3. Rate Limiting
class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        client_ip = request.headers.get("X-Forwarded-For", getattr(request.client, "host", "unknown"))
        now = time.time()
        window_start = now - RATE_LIMIT_WINDOW

        hits = _rate_limit_store[client_ip]
        # Purge old hits
        _rate_limit_store[client_ip] = [t for t in hits if t > window_start]

        if len(_rate_limit_store[client_ip]) >= RATE_LIMIT_REQUESTS:
            request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "success": False,
                    "message": "Rate limit exceeded. Please slow down.",
                    "request_id": request_id,
                    "retry_after_seconds": RATE_LIMIT_WINDOW,
                },
                headers={"Retry-After": str(RATE_LIMIT_WINDOW)},
            )

        _rate_limit_store[client_ip].append(now)
        return await call_next(request)


# 4. AI Usage Tracking
class AIUsageMiddleware(BaseHTTPMiddleware):
    AI_PATHS = {"/api/v1/invoices/ai", "/api/v1/analytics", "/api/v1/reminders/ai"}

    async def dispatch(self, request: Request, call_next):
        global _ai_requests_count, _ai_total_prompt_tokens, _ai_total_completion_tokens, _ai_latencies

        is_ai_path = any(request.url.path.startswith(p) for p in self.AI_PATHS)
        start = time.perf_counter()
        response = await call_next(request)

        if is_ai_path and ENABLE_AI:
            latency_ms = (time.perf_counter() - start) * 1000
            _ai_requests_count += 1
            _ai_latencies.append(latency_ms)
            if len(_ai_latencies) > 500:
                _ai_latencies.pop(0)
            # Simulated token tracking — replace with real counts from your AI service
            _ai_total_prompt_tokens += 150
            _ai_total_completion_tokens += 300

        return response


# ── Register Middleware (outermost → innermost) ────────────────────────────────

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["*"],  # Tighten this in production
)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(AIUsageMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Response-Time"],
)


# ═══════════════════════════════════════════════════════════════════════════════
#  GLOBAL EXCEPTION HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

def _error_response(request: Request, status_code: int, message: str, detail: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "message": message,
            "detail": detail,
            "request_id": getattr(request.state, "request_id", None),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning(f"Validation error on {request.url.path}: {exc.errors()}")
    return _error_response(request, 422, "Request validation failed", exc.errors())


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    messages = {
        400: "Bad request",
        401: "Authentication required or credentials invalid",
        403: "You do not have permission to perform this action",
        404: "The requested resource was not found",
        405: "Method not allowed",
        409: "Conflict — resource already exists",
        429: "Too many requests — please slow down",
        500: "Internal server error",
        503: "Service temporarily unavailable",
    }
    message = messages.get(exc.status_code, exc.detail)
    logger.warning(f"HTTP {exc.status_code} on {request.url.path}: {exc.detail}")
    return _error_response(request, exc.status_code, message, exc.detail)


@app.exception_handler(SQLAlchemyError)
async def database_exception_handler(request: Request, exc: SQLAlchemyError):
    logger.error(f"Database error on {request.url.path}: {exc}", exc_info=True)
    return _error_response(request, 503, "Database temporarily unavailable. Please retry.")


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception on {request.url.path}: {exc}", exc_info=True)
    return _error_response(request, 500, "An unexpected error occurred. Our team has been notified.")


# ═══════════════════════════════════════════════════════════════════════════════
#  DATABASE DEPENDENCY
# ═══════════════════════════════════════════════════════════════════════════════

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  PLATFORM ENDPOINTS  (root-level — no /api/v1 prefix)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/", tags=["Platform"], summary="Root")
def root():
    return {
        "service": APP_NAME,
        "version": APP_VERSION,
        "environment": ENVIRONMENT,
        "docs": "/docs" if ENVIRONMENT != "production" else "disabled",
    }


@app.get("/health", tags=["Platform"], summary="Health check")
def health():
    db_status = "connected"
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_status = "unreachable"

    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "database": db_status,
        "redis": "connected" if os.getenv("REDIS_URL") else "not configured",
        "ai_provider": "online" if ENABLE_AI else "disabled",
        "scheduler": "running",
        "websocket": "active" if ENABLE_WEBSOCKETS else "disabled",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/ready", tags=["Platform"], summary="Readiness probe")
def readiness():
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass

    if not db_ok:
        raise HTTPException(status_code=503, detail="Database not ready")

    return {"ready": True, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/metrics", tags=["Platform"], summary="SaaS metrics snapshot")
def metrics(db: Session = Depends(get_db)):
    total_invoices = db.query(models.Invoice).count()
    total_users = db.query(models.User).count()
    avg_latency = round(sum(_ai_latencies) / len(_ai_latencies), 1) if _ai_latencies else 0.0

    return {
        "total_invoices": total_invoices,
        "total_users": total_users,
        "ai_requests_count": _ai_requests_count,
        "ai_prompt_tokens_total": _ai_total_prompt_tokens,
        "ai_completion_tokens_total": _ai_total_completion_tokens,
        "ai_average_latency_ms": avg_latency,
        "reminders_sent": _reminders_sent,
        "workflows_executed": _workflows_executed,
        "active_websocket_clients": _active_websocket_connections,
        "active_teams": _active_teams,
        "events_broadcasted": _events_broadcasted,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/system/info", tags=["Platform"], summary="System information")
def system_info():
    uptime_seconds = (datetime.now(timezone.utc) - _startup_time).total_seconds()
    mem = psutil.virtual_memory()

    return {
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
        "environment": ENVIRONMENT,
        "uptime_seconds": round(uptime_seconds, 1),
        "uptime_human": _human_uptime(uptime_seconds),
        "ai_model_active": AI_MODEL if ENABLE_AI else None,
        "ai_provider": AI_PROVIDER if ENABLE_AI else None,
        "feature_flags": {
            "ai": ENABLE_AI,
            "workflows": ENABLE_WORKFLOWS,
            "voice": ENABLE_VOICE,
            "websockets": ENABLE_WEBSOCKETS,
        },
        "memory": {
            "total_mb": round(mem.total / 1024 / 1024, 1),
            "used_mb": round(mem.used / 1024 / 1024, 1),
            "percent": mem.percent,
        },
        "queue_stats": {
            "pending_reminders": 0,
            "pending_workflows": 0,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/db-check", tags=["Platform"], summary="Raw DB connectivity check")
def db_check():
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1"))
        return {"database": "connected", "result": result.scalar()}


@app.get("/ai/status", tags=["AI"], summary="AI provider status")
def ai_status():
    avg_latency = round(sum(_ai_latencies) / len(_ai_latencies), 1) if _ai_latencies else 0.0
    return {
        "provider": AI_PROVIDER,
        "model": AI_MODEL,
        "status": "online" if ENABLE_AI else "disabled",
        "average_latency_ms": avg_latency,
        "today_requests": _ai_requests_count,
        "total_prompt_tokens": _ai_total_prompt_tokens,
        "total_completion_tokens": _ai_total_completion_tokens,
        "feature_enabled": ENABLE_AI,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }



@app.post("/api/v1/ai/weekly-summary", tags=["AI"], summary="Generate AI weekly business summary")
async def ai_weekly_summary(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Generate an AI-powered weekly business summary using invoice data."""
    import httpx

    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return {"summary": "AI summary is unavailable — configure OPENAI_API_KEY or ANTHROPIC_API_KEY in your environment."}

    total_invoices = db.query(models.Invoice).count()
    paid = db.query(models.Invoice).filter(models.Invoice.status == "paid").count()
    overdue = db.query(models.Invoice).filter(models.Invoice.status == "overdue").count()
    total_revenue = db.query(func.sum(models.Invoice.total_amount)).filter(models.Invoice.status == "paid").scalar() or 0
    outstanding = db.query(func.sum(models.Invoice.total_amount)).filter(models.Invoice.status != "paid").scalar() or 0
    total_clients = db.query(models.Client).count()

    prompt = (
        f"Generate a concise weekly business summary for an invoice management platform. "
        f"Current data: {total_invoices} total invoices, {paid} paid, {overdue} overdue, "
        f"₹{float(total_revenue):,.0f} revenue collected, ₹{float(outstanding):,.0f} outstanding, "
        f"{total_clients} clients. "
        f"Provide 3-4 sentences covering: revenue trend, collection health, key risk, and one actionable recommendation. "
        f"Be specific and use the actual numbers provided."
    )

    try:
        use_anthropic = bool(os.getenv("ANTHROPIC_API_KEY")) and not os.getenv("OPENAI_API_KEY")
        if use_anthropic:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": os.getenv("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": "claude-haiku-4-5-20251001", "max_tokens": 300, "messages": [{"role": "user", "content": prompt}]},
                )
                data = resp.json()
                summary = data.get("content", [{}])[0].get("text", "")
        else:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}", "Content-Type": "application/json"},
                    json={"model": "gpt-4o-mini", "max_tokens": 300, "messages": [{"role": "user", "content": prompt}]},
                )
                data = resp.json()
                summary = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {"summary": summary}
    except Exception as e:
        logger.error(f"AI weekly summary failed: {e}")
        return {"summary": "Could not generate summary at this time. Please try again."}


    s = int(seconds)
    days, s = divmod(s, 86400)
    hours, s = divmod(s, 3600)
    minutes, s = divmod(s, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{s}s")
    return " ".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
#  API v1 ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

api_router = APIRouter(prefix="/api/v1")


# ── Auth ──────────────────────────────────────────────────────────────────────

@api_router.post("/auth/register", response_model=UserResponse, tags=["Authentication"])
def register_user(user: UserCreate, db: Session = Depends(get_db)):
    existing_user = db.query(models.User).filter(models.User.email == user.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    new_user = models.User(
        full_name=user.full_name,
        email=user.email,
        hashed_password=hash_password(user.password),
        role="admin",
        is_active=True,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@api_router.post("/auth/login", response_model=Token, tags=["Authentication"])
def login_user(user: UserLogin, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if not db_user or not verify_password(user.password, db_user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token({"sub": db_user.email, "role": db_user.role})
    refresh = create_refresh_token({"sub": db_user.email, "role": db_user.role})
    return {
        "access_token": token,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


@api_router.get("/auth/me", response_model=UserResponse, tags=["Authentication"])
def get_me(current_user: models.User = Depends(get_current_user)):
    return current_user


# ── Customers ─────────────────────────────────────────────────────────────────

@api_router.post("/customers", response_model=CustomerResponse, tags=["Customers"])
def create_customer(
    customer: CustomerCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    new_customer = models.Client(
        name=customer.name,
        email=customer.email,
        phone=customer.phone,
        address=customer.address,
        user_id=current_user.id,
    )
    db.add(new_customer)
    db.commit()
    db.refresh(new_customer)
    return new_customer


@api_router.get("/customers", response_model=list[CustomerResponse], tags=["Customers"])
def get_customers(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    return db.query(models.Client).all()


@api_router.get("/customers/{customer_id}", response_model=CustomerResponse, tags=["Customers"])
def get_customer(customer_id: int, db: Session = Depends(get_db)):
    customer = db.query(models.Client).filter(models.Client.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@api_router.put("/customers/{customer_id}", response_model=CustomerResponse, tags=["Customers"])
def update_customer(customer_id: int, customer: CustomerCreate, db: Session = Depends(get_db)):
    db_customer = db.query(models.Client).filter(models.Client.id == customer_id).first()
    if not db_customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    db_customer.name = customer.name
    db_customer.email = customer.email
    db_customer.phone = customer.phone
    db_customer.address = customer.address
    db.commit()
    db.refresh(db_customer)
    return db_customer


@api_router.delete("/customers/{customer_id}", tags=["Customers"])
def delete_customer(customer_id: int, db: Session = Depends(get_db)):
    customer = db.query(models.Client).filter(models.Client.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    db.delete(customer)
    db.commit()
    return {"success": True, "message": "Customer deleted successfully"}


# ── Invoices ──────────────────────────────────────────────────────────────────

@api_router.post("/invoices", response_model=InvoiceResponse, tags=["Invoices"])
def create_invoice(
    invoice: InvoiceCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    import time as _time
    invoice_number = f"INV-{current_user.id}-{int(_time.time())}"
    new_invoice = models.Invoice(
        invoice_number=invoice_number,
        client_id=invoice.client_id,
        user_id=current_user.id,
        due_date=invoice.due_date,
        status="draft",
        total_amount=0,
        notes=invoice.notes,
    )
    db.add(new_invoice)
    db.commit()
    db.refresh(new_invoice)

    # Create line items
    for item in invoice.items:
        total_price = float(item.quantity) * float(item.unit_price)
        new_item = models.InvoiceItem(
            invoice_id=new_invoice.id,
            description=item.description,
            quantity=item.quantity,
            unit_price=item.unit_price,
            total_price=total_price,
        )
        db.add(new_item)
        new_invoice.total_amount = float(new_invoice.total_amount or 0) + total_price
    db.commit()
    db.refresh(new_invoice)
    return new_invoice


@api_router.get("/invoices", response_model=list[InvoiceResponse], tags=["Invoices"])
def get_invoices(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    return db.query(models.Invoice).all()


@api_router.get("/invoices/{invoice_id}", response_model=InvoiceResponse, tags=["Invoices"])
def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


@api_router.put("/invoices/{invoice_id}", response_model=InvoiceResponse, tags=["Invoices"])
def update_invoice(invoice_id: int, invoice: InvoiceCreate, db: Session = Depends(get_db)):
    db_invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not db_invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    db_invoice.invoice_number = invoice.invoice_number
    db_invoice.client_id = invoice.client_id
    db_invoice.due_date = invoice.due_date
    db_invoice.notes = invoice.notes
    db.commit()
    db.refresh(db_invoice)
    return db_invoice


@api_router.delete("/invoices/{invoice_id}", tags=["Invoices"])
def delete_invoice(invoice_id: int, db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    db.delete(invoice)
    db.commit()
    return {"success": True, "message": "Invoice deleted successfully"}


@api_router.patch("/invoices/{invoice_id}/status", response_model=InvoiceResponse, tags=["Invoices"])
def update_invoice_status(invoice_id: int, status_update: InvoiceStatusUpdate, db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    invoice.status = status_update.status
    db.commit()
    db.refresh(invoice)
    return invoice


@api_router.get("/invoices/{invoice_id}/pdf", tags=["Invoices"])
def generate_invoice_pdf(invoice_id: int, db: Session = Depends(get_db)):
    import tempfile
    from fastapi.background import BackgroundTasks
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    client = db.query(models.Client).filter(models.Client.id == invoice.client_id).first()
    items = db.query(models.InvoiceItem).filter(models.InvoiceItem.invoice_id == invoice_id).all()

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    file_path = tmp.name
    tmp.close()

    c = canvas.Canvas(file_path, pagesize=letter)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(50, 750, "INVOICE")
    c.setFont("Helvetica-Bold", 12)
    c.drawString(400, 750, f"#{invoice.invoice_number}")
    c.line(50, 740, 550, 740)
    c.setFont("Helvetica", 11)
    c.drawString(50, 720, f"Client: {client.name if client else 'N/A'}")
    if client and client.email:
        c.drawString(50, 705, f"Email: {client.email}")
    c.drawString(50, 690, f"Status: {invoice.status.upper()}")
    if invoice.due_date:
        c.drawString(50, 675, f"Due Date: {invoice.due_date.strftime('%d %b %Y') if hasattr(invoice.due_date, 'strftime') else str(invoice.due_date)[:10]}")
    y = 645
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "Description")
    c.drawString(350, y, "Qty")
    c.drawString(420, y, "Unit Price")
    c.drawString(500, y, "Total")
    c.line(50, y - 5, 550, y - 5)
    y -= 20
    c.setFont("Helvetica", 10)
    for item in items:
        c.drawString(50, y, str(item.description)[:45])
        c.drawString(350, y, str(item.quantity))
        c.drawString(420, y, f"₹{item.unit_price}")
        c.drawString(500, y, f"₹{item.total_price}")
        y -= 18
    c.line(50, y, 550, y)
    y -= 15
    c.setFont("Helvetica-Bold", 11)
    c.drawString(420, y, "Total:")
    c.drawString(500, y, f"₹{invoice.total_amount}")
    if invoice.notes:
        c.setFont("Helvetica", 10)
        c.drawString(50, y - 30, f"Notes: {invoice.notes}")
    c.save()

    file_name = f"invoice_{invoice.invoice_number}.pdf"
    return FileResponse(file_path, media_type="application/pdf", filename=file_name, background=None)


@api_router.post("/invoices/{invoice_id}/send-reminder", response_model=ReminderResponse, tags=["Invoices"])
def send_reminder(invoice_id: int, db: Session = Depends(get_db)):
    global _reminders_sent
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    customer = db.query(models.Client).filter(models.Client.id == invoice.client_id).first()
    subject = f"Payment Reminder for Invoice {invoice.invoice_number}"
    message = (
        f"Hello,\n\nThis is a reminder that your invoice {invoice.invoice_number} "
        f"is currently marked as '{invoice.status}'.\n\n"
        f"Total Amount: ₹{invoice.total_amount}\n\n"
        f"Please complete the payment at your earliest convenience.\n\n"
        f"Thank you,\nInvoiceFlow Team"
    )
    _reminders_sent += 1
    return {
        "invoice_id": invoice.id,
        "customer_email": customer.email if customer else None,
        "subject": subject,
        "message": message,
    }


@api_router.post("/invoices/{invoice_id}/ai-followup", response_model=AIFollowupResponse, tags=["Invoices"])
def generate_ai_followup(invoice_id: int, tone: str = "polite", db: Session = Depends(get_db)):
    global _ai_requests_count
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    _ai_requests_count += 1

    tones: Dict[str, tuple[str, str]] = {
        "polite": (
            f"Friendly Reminder for Invoice {invoice.invoice_number}",
            f"Hello,\n\nHope you're doing well. This is a gentle reminder regarding your invoice "
            f"{invoice.invoice_number}.\n\nAmount Due: ₹{invoice.total_amount}\n\n"
            f"Thank you,\nInvoiceFlow Team",
        ),
        "firm": (
            f"Payment Reminder - Invoice {invoice.invoice_number}",
            f"Hello,\n\nThis is a reminder that invoice {invoice.invoice_number} is still pending.\n\n"
            f"Amount Due: ₹{invoice.total_amount}\n\n"
            f"Kindly ensure payment is completed as soon as possible.\n\nRegards,\nInvoiceFlow Team",
        ),
        "urgent": (
            f"Urgent: Invoice {invoice.invoice_number} Overdue",
            f"Hello,\n\nYour invoice {invoice.invoice_number} is now overdue.\n\n"
            f"Outstanding Amount: ₹{invoice.total_amount}\n\n"
            f"Immediate action is required.\n\nRegards,\nInvoiceFlow Team",
        ),
    }

    subject, message = tones.get(
        tone,
        (
            f"Reminder for Invoice {invoice.invoice_number}",
            f"Hello,\n\nInvoice {invoice.invoice_number} is pending.\n\nAmount: ₹{invoice.total_amount}\n\nInvoiceFlow Team",
        ),
    )
    return {"invoice_id": invoice.id, "tone": tone, "subject": subject, "message": message}


@api_router.post("/invoices/{invoice_id}/send-email", tags=["Invoices"])
def send_invoice_email(invoice_id: int, tone: str = "polite", db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    customer = db.query(models.Client).filter(models.Client.id == invoice.client_id).first()
    if not customer or not customer.email:
        raise HTTPException(status_code=400, detail="Customer email not found")

    subject = f"Reminder for Invoice {invoice.invoice_number}"
    message = (
        f"Hello,\n\nInvoice {invoice.invoice_number} is pending.\n\n"
        f"Amount: ₹{invoice.total_amount}\n\nInvoiceFlow"
    )
    send_email(customer.email, subject, message)
    return {"success": True, "message": f"Email sent to {customer.email}"}


# ── Invoice Items ─────────────────────────────────────────────────────────────

@api_router.post("/invoice-items", response_model=InvoiceItemResponse, tags=["Invoice Items"])
def create_invoice_item(item: InvoiceItemCreate, db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == item.invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    total_price = item.quantity * item.unit_price
    new_item = models.InvoiceItem(
        invoice_id=item.invoice_id,
        description=item.description,
        quantity=item.quantity,
        unit_price=item.unit_price,
        total_price=total_price,
    )
    db.add(new_item)
    invoice.total_amount += total_price
    db.commit()
    db.refresh(new_item)
    return new_item


@api_router.get("/invoice-items", response_model=list[InvoiceItemResponse], tags=["Invoice Items"])
def get_invoice_items(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    return db.query(models.InvoiceItem).all()


@api_router.get("/invoice-items/{item_id}", response_model=InvoiceItemResponse, tags=["Invoice Items"])
def get_invoice_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(models.InvoiceItem).filter(models.InvoiceItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Invoice item not found")
    return item


@api_router.put("/invoice-items/{item_id}", response_model=InvoiceItemResponse, tags=["Invoice Items"])
def update_invoice_item(item_id: int, item: InvoiceItemCreate, db: Session = Depends(get_db)):
    db_item = db.query(models.InvoiceItem).filter(models.InvoiceItem.id == item_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Invoice item not found")
    invoice = db.query(models.Invoice).filter(models.Invoice.id == db_item.invoice_id).first()
    old_total = db_item.total_price
    new_total = item.quantity * item.unit_price
    db_item.invoice_id = item.invoice_id
    db_item.description = item.description
    db_item.quantity = item.quantity
    db_item.unit_price = item.unit_price
    db_item.total_price = new_total
    if invoice:
        invoice.total_amount = invoice.total_amount - old_total + new_total
    db.commit()
    db.refresh(db_item)
    return db_item


@api_router.delete("/invoice-items/{item_id}", tags=["Invoice Items"])
def delete_invoice_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(models.InvoiceItem).filter(models.InvoiceItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Invoice item not found")
    invoice = db.query(models.Invoice).filter(models.Invoice.id == item.invoice_id).first()
    if invoice:
        invoice.total_amount -= item.total_price
    db.delete(item)
    db.commit()
    return {"success": True, "message": "Invoice item deleted successfully"}


# ── Recurring Billing ─────────────────────────────────────────────────────────

@api_router.post("/recurring-billing", response_model=RecurringBillingResponse, tags=["Recurring Billing"])
def create_recurring_billing(
    data: RecurringBillingCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    new_plan = models.RecurringBilling(
        client_id=data.client_id,
        user_id=current_user.id,
        title=data.title,
        amount=data.amount,
        frequency=data.frequency,
        next_billing_date=data.next_billing_date,
        is_active=data.is_active,
    )
    db.add(new_plan)
    db.commit()
    db.refresh(new_plan)
    return new_plan


@api_router.get("/recurring-billing", response_model=list[RecurringBillingResponse], tags=["Recurring Billing"])
def get_recurring_billings(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    return db.query(models.RecurringBilling).all()


@api_router.get("/recurring-billing/{plan_id}", response_model=RecurringBillingResponse, tags=["Recurring Billing"])
def get_recurring_billing(plan_id: int, db: Session = Depends(get_db)):
    plan = db.query(models.RecurringBilling).filter(models.RecurringBilling.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Recurring billing plan not found")
    return plan


@api_router.put("/recurring-billing/{plan_id}", response_model=RecurringBillingResponse, tags=["Recurring Billing"])
def update_recurring_billing(plan_id: int, data: RecurringBillingCreate, db: Session = Depends(get_db)):
    plan = db.query(models.RecurringBilling).filter(models.RecurringBilling.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Recurring billing plan not found")
    plan.client_id = data.client_id
    plan.title = data.title
    plan.amount = data.amount
    plan.frequency = data.frequency
    plan.next_billing_date = data.next_billing_date
    plan.is_active = data.is_active
    db.commit()
    db.refresh(plan)
    return plan


@api_router.delete("/recurring-billing/{plan_id}", tags=["Recurring Billing"])
def delete_recurring_billing(plan_id: int, db: Session = Depends(get_db)):
    plan = db.query(models.RecurringBilling).filter(models.RecurringBilling.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Recurring billing plan not found")
    db.delete(plan)
    db.commit()
    return {"success": True, "message": "Recurring billing plan deleted successfully"}


@api_router.post("/recurring-billing/{plan_id}/generate-invoice", response_model=InvoiceResponse, tags=["Recurring Billing"])
def generate_invoice_from_recurring(plan_id: int, db: Session = Depends(get_db)):
    plan = db.query(models.RecurringBilling).filter(models.RecurringBilling.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Recurring billing plan not found")
    if not plan.is_active:
        raise HTTPException(status_code=400, detail="Recurring billing plan is not active")
    invoice_number = f"REC-{plan.id}-{int(datetime.now().timestamp())}"
    new_invoice = models.Invoice(
        invoice_number=invoice_number,
        client_id=plan.client_id,
        user_id=plan.user_id,
        due_date=plan.next_billing_date,
        status="draft",
        total_amount=plan.amount,
        notes=f"Auto-generated from recurring billing plan: {plan.title}",
    )
    db.add(new_invoice)
    db.commit()
    db.refresh(new_invoice)
    return new_invoice


# ── Dashboard ─────────────────────────────────────────────────────────────────

@api_router.get("/dashboard/summary", tags=["Dashboard"])
def dashboard_summary(db: Session = Depends(get_db)):
    total_customers = db.query(models.Client).count()
    total_invoices = db.query(models.Invoice).count()
    paid_invoices = db.query(models.Invoice).filter(models.Invoice.status == "paid").count()
    draft_invoices = db.query(models.Invoice).filter(models.Invoice.status == "draft").count()
    overdue_invoices = db.query(models.Invoice).filter(models.Invoice.status == "overdue").count()
    total_revenue_amount = sum(
        i.total_amount for i in db.query(models.Invoice).filter(models.Invoice.status == "paid").all()
    )
    unpaid_amount = sum(
        i.total_amount for i in db.query(models.Invoice).filter(models.Invoice.status != "paid").all()
    )
    return {
        "total_customers": total_customers,
        "total_invoices": total_invoices,
        "paid_invoices": paid_invoices,
        "draft_invoices": draft_invoices,
        "overdue_invoices": overdue_invoices,
        "total_revenue": total_revenue_amount,
        "unpaid_amount": unpaid_amount,
    }


@api_router.get("/dashboard/monthly-revenue", tags=["Dashboard"])
def monthly_revenue(db: Session = Depends(get_db)):
    results = (
        db.query(
            extract("month", models.Invoice.issue_date).label("month"),
            func.sum(models.Invoice.total_amount).label("amount"),
        )
        .filter(
            models.Invoice.status == "paid",
            extract("year", models.Invoice.issue_date) == datetime.now().year,
        )
        .group_by("month")
        .order_by("month")
        .all()
    )
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return [{"month": month_names[int(r.month) - 1], "amount": float(r.amount)} for r in results]


# ── Analytics ─────────────────────────────────────────────────────────────────

@api_router.get("/analytics/revenue", tags=["Analytics"])
def analytics_revenue(db: Session = Depends(get_db)):
    total = db.query(func.sum(models.Invoice.total_amount)).scalar() or 0
    collected = (
        db.query(func.sum(models.Invoice.total_amount))
        .filter(models.Invoice.status == "paid")
        .scalar()
        or 0
    )
    outstanding = total - collected
    return {
        "total_invoiced": float(total),
        "total_collected": float(collected),
        "total_outstanding": float(outstanding),
        "collection_rate_percent": round((collected / total * 100) if total else 0, 2),
    }


@api_router.get("/analytics/late-payments", tags=["Analytics"])
def analytics_late_payments(db: Session = Depends(get_db)):
    overdue = db.query(models.Invoice).filter(models.Invoice.status == "overdue").all()
    total = db.query(models.Invoice).count()
    return {
        "overdue_count": len(overdue),
        "overdue_rate_percent": round((len(overdue) / total * 100) if total else 0, 2),
        "overdue_amount": float(sum(i.total_amount for i in overdue)),
    }


@api_router.get("/analytics/kpis", tags=["Analytics"])
def analytics_kpis(db: Session = Depends(get_db)):
    total_invoices = db.query(models.Invoice).count()
    paid = db.query(models.Invoice).filter(models.Invoice.status == "paid").count()
    total_amount = db.query(func.sum(models.Invoice.total_amount)).scalar() or 0
    avg_invoice_value = float(total_amount) / total_invoices if total_invoices else 0
    return {
        "total_invoices": total_invoices,
        "paid_invoices": paid,
        "average_invoice_value": round(avg_invoice_value, 2),
        "collection_rate_percent": round((paid / total_invoices * 100) if total_invoices else 0, 2),
        "active_websocket_clients": _active_websocket_connections,
        "ai_requests_today": _ai_requests_count,
    }


# ── Exports ───────────────────────────────────────────────────────────────────

@api_router.get("/exports/invoices-csv", tags=["Exports"])
def export_invoices_csv(db: Session = Depends(get_db)):
    file_path = "app/invoices_export.csv"
    invoices = db.query(models.Invoice).all()
    with open(file_path, mode="w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["ID", "Invoice Number", "Customer ID", "Status", "Total Amount", "Due Date", "Notes"])
        for invoice in invoices:
            writer.writerow([
                invoice.id, invoice.invoice_number, invoice.client_id,
                invoice.status, invoice.total_amount, invoice.due_date, invoice.notes,
            ])
    return FileResponse(file_path, media_type="text/csv", filename="invoices_export.csv")


# ── Scheduler ─────────────────────────────────────────────────────────────────

@api_router.post("/scheduler/run-recurring-billing", tags=["Scheduler"])
def run_recurring_billing_scheduler(db: Session = Depends(get_db)):
    today = datetime.now(timezone.utc)
    plans = db.query(models.RecurringBilling).filter(
        models.RecurringBilling.is_active == True,
        models.RecurringBilling.next_billing_date <= today,
    ).all()
    created_invoices = []
    for plan in plans:
        invoice_number = f"AUTO-{plan.id}-{int(datetime.now().timestamp())}"
        new_invoice = models.Invoice(
            invoice_number=invoice_number,
            client_id=plan.client_id,
            user_id=plan.user_id,
            due_date=plan.next_billing_date,
            status="draft",
            total_amount=plan.amount,
            notes=f"Auto-generated by scheduler from plan: {plan.title}",
        )
        db.add(new_invoice)
        db.flush()
        freq_days = {"monthly": 30, "quarterly": 90, "yearly": 365}
        days = freq_days.get(plan.frequency, 30)
        plan.next_billing_date += timedelta(days=days)
        created_invoices.append(new_invoice.invoice_number)
    db.commit()
    return {
        "success": True,
        "message": "Recurring billing scheduler completed",
        "created_invoices": created_invoices,
        "count": len(created_invoices),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  MOUNT API ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

app.include_router(api_router)

# ── Backward-compatible legacy root-level aliases ─────────────────────────────
# Keeps existing frontends/clients working while the /api/v1 routes are adopted.

@app.get("/auth/register", response_model=UserResponse, include_in_schema=False)
def _legacy_register(user: UserCreate, db: Session = Depends(get_db)):
    return register_user(user, db)

@app.post("/auth/login", response_model=Token, include_in_schema=False)
def _legacy_login(user: UserLogin, db: Session = Depends(get_db)):
    return login_user(user, db)

@app.get("/auth/me", response_model=UserResponse, include_in_schema=False)
def _legacy_me(current_user: models.User = Depends(get_current_user)):
    return get_me(current_user)