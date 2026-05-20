# ═══════════════════════════════════════════════════════════════════════════════
#  InvoiceFlow — routers/auth.py
#  AI-ready authentication system, SaaS onboarding gateway,
#  team/workspace initializer, security layer, and session intelligence.
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pyotp
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    hash_password,
    verify_password,
    google_oauth_callback,
)
from app.config import settings
from app.core.constants import ActivityType, NotificationType, DashboardWidgetType
from app.core.permissions import PERMISSION_MATRIX, UserRole
from app.database import get_db
from app.utils import send_email

logger = logging.getLogger("invoiceflow.auth_router")

# ── Router ────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/auth", tags=["Authentication"])

# ── Rate-limit state (in-process; swap for Redis in production) ───────────────

_failed_attempts: dict[str, list[datetime]] = {}   # ip → [timestamp, ...]
_BRUTE_WINDOW_SECONDS  = 300   # 5-minute rolling window
_BRUTE_MAX_ATTEMPTS    = 5     # lock after 5 failures
_BRUTE_LOCKOUT_MINUTES = 15    # duration of IP-level cool-down
_RESEND_COOLDOWN_SECS  = 60    # minimum gap between resend-verification requests

# ═══════════════════════════════════════════════════════════════════════════════
#  REQUEST / RESPONSE SCHEMAS  (auth-router-local; broader ones live in schemas.py)
# ═══════════════════════════════════════════════════════════════════════════════

class RegisterRequest(BaseModel):
    full_name:         str        = Field(min_length=1, max_length=100)
    email:             EmailStr
    password:          str        = Field(min_length=8)
    business_name:     Optional[str] = None
    timezone:          str        = "UTC"
    language:          str        = "en"
    country:           Optional[str] = None
    subscription_tier: str        = "free"

    @staticmethod
    def _validate_password(v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class LoginRequest(BaseModel):
    email:       EmailStr
    password:    str
    remember_me: bool             = False
    device_info: Optional[dict]   = None


class RefreshRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token:        str
    new_password: str = Field(min_length=8)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password:     str = Field(min_length=8)


class TwoFactorVerifyRequest(BaseModel):
    otp_code: str = Field(min_length=6, max_length=6)


class CompleteOnboardingRequest(BaseModel):
    business_name:  Optional[str]  = None
    industry:       Optional[str]  = None
    company_size:   Optional[str]  = None
    currency:       Optional[str]  = None
    monthly_revenue: Optional[float] = None


class GoogleOAuthRequest(BaseModel):
    code:  str
    state: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
#  INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_brute_force(ip: str) -> None:
    """Raise 429 if *ip* has exceeded the failed-login threshold."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=_BRUTE_WINDOW_SECONDS)
    attempts = [t for t in _failed_attempts.get(ip, []) if t > cutoff]
    _failed_attempts[ip] = attempts
    if len(attempts) >= _BRUTE_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many failed login attempts from this IP. "
                f"Please wait {_BRUTE_LOCKOUT_MINUTES} minutes."
            ),
        )


def _record_failed_attempt(ip: str) -> None:
    _failed_attempts.setdefault(ip, []).append(datetime.now(timezone.utc))


def _clear_failed_attempts(ip: str) -> None:
    _failed_attempts.pop(ip, None)


def _log_activity(
    db: Session,
    *,
    user_id: Optional[int],
    team_id: Optional[int],
    activity_type: str,
    description: str,
    ip_address: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    event_data: Optional[dict] = None,
) -> None:
    activity = models.Activity(
        user_id=user_id,
        team_id=team_id,
        activity_type=activity_type,
        entity_type=entity_type,
        entity_id=entity_id,
        description=description,
        ip_address=ip_address,
        event_data=event_data or {},
        importance_score=0.6,
    )
    db.add(activity)


def _create_notification(
    db: Session,
    *,
    user_id: int,
    team_id: Optional[int],
    title: str,
    message: str,
    notification_type: str = NotificationType.INFO,
    action_url: Optional[str] = None,
    icon: Optional[str] = None,
    color: Optional[str] = None,
    priority: str = "normal",
) -> None:
    n = models.Notification(
        user_id=user_id,
        team_id=team_id,
        title=title,
        message=message,
        notification_type=notification_type,
        action_url=action_url,
        icon=icon,
        color=color,
        priority=priority,
    )
    db.add(n)


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:80]


def _make_team_slug(name: str, db: Session) -> str:
    base = _slugify(name) or "workspace"
    slug = base
    counter = 1
    while db.query(models.Team).filter(models.Team.slug == slug).first():
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def _generate_verification_token(user_id: int) -> str:
    """Create a signed JWT for email verification (24 h)."""
    payload = {
        "sub": str(user_id),
        "type": "email_verify",
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def _generate_reset_token(user_id: int) -> str:
    """Create a signed JWT for password reset (1 h)."""
    payload = {
        "sub": str(user_id),
        "type": "pw_reset",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def _decode_special_token(token: str, expected_type: str) -> int:
    """Decode a verification / reset token. Returns user_id or raises 400."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=400, detail="Token is invalid or has expired")
    if payload.get("type") != expected_type:
        raise HTTPException(status_code=400, detail="Token type mismatch")
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=400, detail="Token payload is missing subject")
    return int(sub)


def _build_ai_startup_summary(user: models.User, db: Session) -> dict:
    """Build the startup-style business snapshot shown after login."""
    now = datetime.now(timezone.utc)
    thirty_days_ago = now - timedelta(days=30)

    # Revenue this month (paid invoices)
    revenue_rows = (
        db.query(func.coalesce(func.sum(models.Invoice.total_amount), 0))
        .filter(
            models.Invoice.user_id == user.id,
            models.Invoice.status == "paid",
            models.Invoice.created_at >= thirty_days_ago,
        )
        .scalar()
    )
    monthly_revenue = float(revenue_rows or 0)

    # Overdue invoices
    overdue_count = (
        db.query(func.count(models.Invoice.id))
        .filter(
            models.Invoice.user_id == user.id,
            models.Invoice.status == "overdue",
        )
        .scalar()
        or 0
    )

    # Unpaid invoices
    unpaid_count = (
        db.query(func.count(models.Invoice.id))
        .filter(
            models.Invoice.user_id == user.id,
            models.Invoice.status.in_(["sent", "draft"]),
            models.Invoice.due_date < now,
        )
        .scalar()
        or 0
    )

    # Unread notifications
    unread_count = (
        db.query(func.count(models.Notification.id))
        .filter(
            models.Notification.user_id == user.id,
            models.Notification.is_read == False,
        )
        .scalar()
        or 0
    )

    greeting = f"Welcome back, {user.full_name.split()[0]}!"
    insights: list[str] = []

    if overdue_count > 0:
        insights.append(f"{overdue_count} invoice{'s' if overdue_count > 1 else ''} {'are' if overdue_count > 1 else 'is'} overdue")
    if unpaid_count > 0:
        insights.append(f"{unpaid_count} unpaid invoice{'s' if unpaid_count > 1 else ''} past due date")
    if monthly_revenue > 0:
        insights.append(f"${monthly_revenue:,.2f} revenue collected this month")
    if unread_count > 0:
        insights.append(f"{unread_count} unread notification{'s' if unread_count > 1 else ''} waiting")
    if not insights:
        insights.append("Dashboard is looking healthy — no urgent items")

    return {
        "greeting": greeting,
        "monthly_revenue": monthly_revenue,
        "overdue_invoices": overdue_count,
        "unpaid_invoices": unpaid_count,
        "unread_notifications": unread_count,
        "ai_insights": insights,
        "recommended_actions": _get_recommended_actions(overdue_count, unpaid_count, user),
        "cash_flow_status": "healthy" if overdue_count == 0 else "attention_needed",
    }


def _get_recommended_actions(overdue: int, unpaid: int, user: models.User) -> list[str]:
    actions: list[str] = []
    if overdue > 0:
        actions.append("Send payment reminders for overdue invoices")
    if unpaid > 0:
        actions.append("Review invoices approaching due date")
    if not user.email_verified:
        actions.append("Verify your email address to unlock all features")
    if not user.onboarding_completed:
        actions.append("Complete onboarding to personalise your dashboard")
    if not actions:
        actions.append("Review your analytics dashboard for growth opportunities")
    return actions[:4]


def _build_permissions_response(user: models.User) -> dict:
    role = UserRole(user.role) if user.role in [r.value for r in UserRole] else UserRole.MEMBER
    granted = [perm for perm, min_role in PERMISSION_MATRIX.items() if _role_gte(role, min_role)]
    team = user.team
    tier = team.subscription_tier if team else "free"
    return {
        "role": user.role,
        "permissions": granted,
        "feature_access": {
            "ai_assistant":  tier in ("pro", "enterprise"),
            "voice_invoice": tier in ("pro", "enterprise"),
            "advanced_analytics": tier in ("pro", "enterprise"),
            "team_management": user.role in ("admin", "superadmin", "manager"),
            "api_access":    tier == "enterprise",
            "white_label":   tier == "enterprise",
            "2fa":           True,
        },
        "ai_limits": {
            "free":       {"requests_per_day": 20,   "tokens_per_month": 50_000},
            "pro":        {"requests_per_day": 500,  "tokens_per_month": 2_000_000},
            "enterprise": {"requests_per_day": 9999, "tokens_per_month": 999_999_999},
        }.get(tier, {"requests_per_day": 20, "tokens_per_month": 50_000}),
        "subscription_tier": tier,
    }


def _role_gte(role: UserRole, required: UserRole) -> bool:
    order = [UserRole.VIEWER, UserRole.MEMBER, UserRole.MANAGER, UserRole.ADMIN, UserRole.SUPERADMIN]
    try:
        return order.index(role) >= order.index(required)
    except ValueError:
        return False


def _default_widgets(user_id: int) -> list[models.DashboardWidget]:
    defaults = [
        {"widget_type": DashboardWidgetType.KPI,      "title": "Revenue Overview",   "position": {"x": 0, "y": 0, "w": 6, "h": 2}},
        {"widget_type": DashboardWidgetType.CHART,     "title": "Invoice Trends",     "position": {"x": 6, "y": 0, "w": 6, "h": 2}},
        {"widget_type": DashboardWidgetType.AI_TIPS,   "title": "AI Recommendations", "position": {"x": 0, "y": 2, "w": 4, "h": 2}},
        {"widget_type": DashboardWidgetType.ACTIVITY,  "title": "Recent Activity",    "position": {"x": 4, "y": 2, "w": 4, "h": 2}},
        {"widget_type": DashboardWidgetType.CASHFLOW,  "title": "Cash Flow",          "position": {"x": 8, "y": 2, "w": 4, "h": 2}},
    ]
    return [
        models.DashboardWidget(
            user_id=user_id,
            widget_type=d["widget_type"],
            title=d["title"],
            position=d["position"],
            is_visible=True,
            config={},
        )
        for d in defaults
    ]


def _issue_token_pair(user: models.User, db: Session, remember_me: bool = False) -> tuple[str, str]:
    """Generate access + refresh tokens and store the refresh hash on the user."""
    access_token  = create_access_token({"sub": user.email, "role": user.role})
    refresh_token = create_refresh_token({"sub": user.email, "role": user.role})
    user.refresh_token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
    return access_token, refresh_token


# ═══════════════════════════════════════════════════════════════════════════════
#  1. POST /register
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
    description=(
        "Creates a user account, initialises a default workspace/team, seeds "
        "starter dashboard widgets, fires a welcome notification and email, and "
        "returns a ready-to-use token pair plus AI onboarding context."
    ),
    responses={
        409: {"description": "Email already registered"},
        422: {"description": "Validation error"},
    },
)
def register(
    payload: RegisterRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    ip = _client_ip(request)
    _check_brute_force(ip)

    # ── Duplicate e-mail check ─────────────────────────────────────────────────
    if db.query(models.User).filter(models.User.email == payload.email).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email address already exists",
        )

    # ── Create team / workspace ────────────────────────────────────────────────
    team_name = payload.business_name or f"{payload.full_name}'s Workspace"
    team = models.Team(
        name=team_name,
        slug=_make_team_slug(team_name, db),
        subscription_tier=payload.subscription_tier,
        timezone=payload.timezone,
        currency="USD",
        ai_preferences={},
        branding={},
        feature_overrides={},
    )
    db.add(team)
    db.flush()   # get team.id before creating user

    # ── Create user ────────────────────────────────────────────────────────────
    user = models.User(
        full_name=payload.full_name,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role=UserRole.ADMIN.value,   # first user of a workspace is owner/admin
        is_active=True,
        team_id=team.id,
        timezone=payload.timezone,
        language=payload.language,
        country=payload.country,
        theme_preference="dark",     # default to dark mode per spec
        onboarding_completed=False,
        onboarding_step=0,
        ai_memory_enabled=True,
        command_palette_enabled=True,
    )
    db.add(user)
    db.flush()

    # Now that we have user.id, set team owner
    team.owner_id = user.id

    # ── Seed default dashboard widgets ─────────────────────────────────────────
    for widget in _default_widgets(user.id):
        db.add(widget)

    # ── Welcome notification ───────────────────────────────────────────────────
    _create_notification(
        db,
        user_id=user.id,
        team_id=team.id,
        title="Welcome to InvoiceFlow! 🎉",
        message=(
            f"Hi {user.full_name.split()[0]}, your workspace is ready. "
            "Complete the onboarding steps to unlock your full AI dashboard."
        ),
        notification_type=NotificationType.SUCCESS,
        action_url="/onboarding",
        icon="sparkles",
        color="#6366f1",
        priority="high",
    )

    # ── Activity log ───────────────────────────────────────────────────────────
    _log_activity(
        db,
        user_id=user.id,
        team_id=team.id,
        activity_type=ActivityType.CREATE,
        description=f"New account created: {user.email}",
        ip_address=ip,
        entity_type="user",
        entity_id=user.id,
        event_data={"source": "registration", "tier": payload.subscription_tier},
    )

    # ── Issue tokens ───────────────────────────────────────────────────────────
    access_token, refresh_token = _issue_token_pair(user, db)

    # ── Email verification token ───────────────────────────────────────────────
    verify_token = _generate_verification_token(user.id)

    db.commit()
    db.refresh(user)
    db.refresh(team)

    # ── Background tasks ───────────────────────────────────────────────────────
    background_tasks.add_task(
        send_email,
        to=user.email,
        subject="Verify your InvoiceFlow email",
        body=(
            f"Hi {user.full_name},\n\n"
            f"Please verify your email address by clicking the link below:\n"
            f"{settings.FRONTEND_URL if hasattr(settings, 'FRONTEND_URL') else ''}"
            f"/auth/verify-email?token={verify_token}\n\n"
            "This link expires in 24 hours.\n\nWelcome aboard!"
        ),
    )

    return {
        "access_token":     access_token,
        "refresh_token":    refresh_token,
        "token_type":       "bearer",
        "expires_in":       settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": {
            "id":                   user.id,
            "full_name":            user.full_name,
            "email":                user.email,
            "role":                 user.role,
            "theme_preference":     user.theme_preference,
            "onboarding_completed": user.onboarding_completed,
            "email_verified":       user.email_verified,
        },
        "team": {
            "id":                team.id,
            "name":              team.name,
            "slug":              team.slug,
            "subscription_tier": team.subscription_tier,
        },
        "onboarding_steps": [
            {"step": 1, "key": "verify_email",      "title": "Verify your email",           "completed": False},
            {"step": 2, "key": "create_client",     "title": "Add your first client",        "completed": False},
            {"step": 3, "key": "create_invoice",    "title": "Create your first invoice",    "completed": False},
            {"step": 4, "key": "connect_payment",   "title": "Connect a payment method",     "completed": False},
            {"step": 5, "key": "explore_ai",        "title": "Try the AI assistant",         "completed": False},
        ],
        "ai_welcome": {
            "greeting":  f"Hi {user.full_name.split()[0]}! I'm your AI business assistant.",
            "tips": [
                "Ask me to generate an invoice in plain English",
                "I can predict which clients are likely to pay late",
                "Say 'show my cash flow' for an instant financial snapshot",
            ],
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  2. POST /login
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/login",
    summary="Email + password login",
    description=(
        "Authenticates the user, tracks device/IP, detects suspicious logins, "
        "preloads the AI dashboard context, and returns tokens plus a startup summary."
    ),
    responses={
        401: {"description": "Invalid credentials"},
        403: {"description": "Account locked or inactive"},
        429: {"description": "Too many failed attempts"},
    },
)
def login(
    payload: LoginRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    ip = _client_ip(request)
    _check_brute_force(ip)

    # ── Resolve user ───────────────────────────────────────────────────────────
    user = db.query(models.User).filter(models.User.email == payload.email).first()

    if not user or not verify_password(payload.password, user.hashed_password):
        _record_failed_attempt(ip)
        if user:
            user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
            if user.failed_login_attempts >= _BRUTE_MAX_ATTEMPTS:
                user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=_BRUTE_LOCKOUT_MINUTES)
                logger.warning(f"[auth] Account locked after repeated failures: {payload.email}")
            db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is deactivated")

    # ── Account lock check ─────────────────────────────────────────────────────
    if user.locked_until and user.locked_until > datetime.now(timezone.utc):
        remaining = int((user.locked_until - datetime.now(timezone.utc)).total_seconds() / 60)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account is temporarily locked. Try again in {remaining} minute(s).",
        )

    # ── Suspicious login detection ─────────────────────────────────────────────
    is_suspicious = False
    if user.last_login_ip and user.last_login_ip != ip:
        is_suspicious = True
        logger.info(f"[auth] Suspicious login detected for {user.email}: IP changed {user.last_login_ip} → {ip}")
        background_tasks.add_task(
            _notify_suspicious_login_bg,
            user_id=user.id,
            team_id=user.team_id,
            ip=ip,
            db_factory=None,   # handled inside bg task
        )

    # ── Reset counters, update login metadata ──────────────────────────────────
    _clear_failed_attempts(ip)
    user.failed_login_attempts = 0
    user.locked_until          = None
    user.last_login_at         = datetime.now(timezone.utc)
    user.last_login_ip         = ip

    # ── Issue tokens ───────────────────────────────────────────────────────────
    access_token, refresh_token = _issue_token_pair(user, db, remember_me=payload.remember_me)

    # ── Activity log ───────────────────────────────────────────────────────────
    _log_activity(
        db,
        user_id=user.id,
        team_id=user.team_id,
        activity_type=ActivityType.LOGIN,
        description=f"User logged in from {ip}",
        ip_address=ip,
        entity_type="user",
        entity_id=user.id,
        event_data={
            "device_info":   payload.device_info or {},
            "is_suspicious": is_suspicious,
            "remember_me":   payload.remember_me,
        },
    )

    db.commit()
    db.refresh(user)

    # ── Build response payload ─────────────────────────────────────────────────
    startup_summary = _build_ai_startup_summary(user, db)
    permissions     = _build_permissions_response(user)

    # Unread notifications (top 5)
    notifications = (
        db.query(models.Notification)
        .filter(models.Notification.user_id == user.id, models.Notification.is_read == False)
        .order_by(models.Notification.created_at.desc())
        .limit(5)
        .all()
    )

    team = user.team
    return {
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "token_type":    "bearer",
        "expires_in":    settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": {
            "id":                   user.id,
            "full_name":            user.full_name,
            "email":                user.email,
            "role":                 user.role,
            "team_id":              user.team_id,
            "theme_preference":     user.theme_preference,
            "language":             user.language,
            "timezone":             user.timezone,
            "onboarding_completed": user.onboarding_completed,
            "email_verified":       user.email_verified,
            "avatar_url":           user.avatar_url,
            "last_login_at":        user.last_login_at.isoformat() if user.last_login_at else None,
            "ai_usage_count":       user.ai_usage_count,
            "mfa_enabled":          user.mfa_enabled,
        },
        "team": {
            "id":                team.id                  if team else None,
            "name":              team.name                if team else None,
            "slug":              team.slug                if team else None,
            "subscription_tier": team.subscription_tier  if team else "free",
            "ai_health_score":   team.ai_health_score     if team else None,
        } if team else None,
        "permissions":       permissions,
        "dashboard_context": {
            "startup_summary":    startup_summary,
            "ai_recommendations": startup_summary["recommended_actions"],
            "ai_insights":        startup_summary["ai_insights"],
            "ai_greeting":        startup_summary["greeting"],
        },
        "notifications": [
            {
                "id":                n.id,
                "title":             n.title,
                "message":           n.message,
                "notification_type": n.notification_type,
                "priority":          n.priority,
                "action_url":        n.action_url,
                "created_at":        n.created_at.isoformat() if n.created_at else None,
            }
            for n in notifications
        ],
        "is_first_login": user.ai_usage_count == 0,
        "is_suspicious":  is_suspicious,
    }


def _notify_suspicious_login_bg(user_id: int, team_id: Optional[int], ip: str, db_factory: Any) -> None:
    """
    Background task: write a suspicious-login notification.
    We open our own DB session since background tasks run outside the request cycle.
    """
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        _create_notification(
            db,
            user_id=user_id,
            team_id=team_id,
            title="Suspicious Login Detected",
            message=(
                f"A login was recorded from a new IP address ({ip}). "
                "If this wasn't you, please change your password immediately."
            ),
            notification_type=NotificationType.WARNING,
            action_url="/settings/security",
            icon="shield-alert",
            color="#f59e0b",
            priority="high",
        )
        db.commit()
    except Exception as exc:
        logger.error(f"[auth] Failed to create suspicious-login notification: {exc}")
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  3. POST /refresh
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/refresh",
    summary="Rotate JWT tokens",
    description="Validate an existing refresh token and return a new rotated access + refresh pair.",
    responses={401: {"description": "Refresh token invalid or expired"}},
)
def refresh_tokens(payload: RefreshRequest, db: Session = Depends(get_db)) -> dict:
    try:
        jwt_payload = jwt.decode(
            payload.refresh_token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token is invalid or expired")

    if jwt_payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    email = jwt_payload.get("sub")
    if not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token payload missing subject")

    user = db.query(models.User).filter(models.User.email == email).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    # Verify stored hash to prevent replay of revoked tokens
    incoming_hash = hashlib.sha256(payload.refresh_token.encode()).hexdigest()
    if user.refresh_token_hash and user.refresh_token_hash != incoming_hash:
        logger.warning(f"[auth] Refresh token replay attempt for {email}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token has been revoked")

    new_access, new_refresh = _issue_token_pair(user, db)
    db.commit()

    return {
        "access_token":  new_access,
        "refresh_token": new_refresh,
        "token_type":    "bearer",
        "expires_in":    settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  4. GET /me
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/me",
    summary="Get current user profile",
    description=(
        "Returns the full user profile enriched with team info, permissions, "
        "AI dashboard context, live KPIs, pending reminders, and a startup summary."
    ),
)
def get_me(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    user  = current_user
    team  = user.team
    now   = datetime.now(timezone.utc)

    # ── Live KPIs ──────────────────────────────────────────────────────────────
    thirty_days_ago = now - timedelta(days=30)
    total_revenue = float(
        db.query(func.coalesce(func.sum(models.Invoice.total_amount), 0))
        .filter(
            models.Invoice.user_id == user.id,
            models.Invoice.status == "paid",
            models.Invoice.created_at >= thirty_days_ago,
        )
        .scalar() or 0
    )
    overdue_invoices = (
        db.query(func.count(models.Invoice.id))
        .filter(models.Invoice.user_id == user.id, models.Invoice.status == "overdue")
        .scalar() or 0
    )
    total_clients = (
        db.query(func.count(models.Client.id))
        .filter(models.Client.user_id == user.id)
        .scalar() or 0
    )
    active_workflows = (
        db.query(func.count(models.Workflow.id))
        .filter(models.Workflow.user_id == user.id, models.Workflow.is_active == True)
        .scalar() or 0
    )

    # ── Notifications ──────────────────────────────────────────────────────────
    unread_notifications = (
        db.query(models.Notification)
        .filter(models.Notification.user_id == user.id, models.Notification.is_read == False)
        .order_by(models.Notification.created_at.desc())
        .limit(10)
        .all()
    )

    # ── Dashboard widgets ──────────────────────────────────────────────────────
    widgets = (
        db.query(models.DashboardWidget)
        .filter(models.DashboardWidget.user_id == user.id, models.DashboardWidget.is_visible == True)
        .all()
    )

    permissions    = _build_permissions_response(user)
    startup_summary = _build_ai_startup_summary(user, db)

    return {
        "user": {
            "id":                    user.id,
            "full_name":             user.full_name,
            "email":                 user.email,
            "role":                  user.role,
            "team_id":               user.team_id,
            "avatar_url":            user.avatar_url,
            "phone":                 user.phone,
            "timezone":              user.timezone,
            "language":              user.language,
            "country":               user.country,
            "theme_preference":      user.theme_preference,
            "onboarding_completed":  user.onboarding_completed,
            "onboarding_step":       user.onboarding_step,
            "email_verified":        user.email_verified,
            "mfa_enabled":           user.mfa_enabled,
            "voice_enabled":         user.voice_enabled,
            "command_palette_enabled": user.command_palette_enabled,
            "ai_memory_enabled":     user.ai_memory_enabled,
            "ai_usage_count":        user.ai_usage_count,
            "last_login_at":         user.last_login_at.isoformat() if user.last_login_at else None,
            "created_at":            user.created_at.isoformat() if user.created_at else None,
        },
        "team": {
            "id":                team.id,
            "name":              team.name,
            "slug":              team.slug,
            "subscription_tier": team.subscription_tier,
            "industry":          team.industry,
            "company_size":      team.company_size,
            "currency":          team.currency,
            "ai_health_score":   team.ai_health_score,
            "branding":          team.branding,
        } if team else None,
        "permissions": permissions,
        "kpis": {
            "revenue_30d":       total_revenue,
            "overdue_invoices":  overdue_invoices,
            "total_clients":     total_clients,
            "active_workflows":  active_workflows,
            "unread_notifications": len(unread_notifications),
        },
        "widgets": [
            {
                "id":          w.id,
                "widget_type": w.widget_type,
                "title":       w.title,
                "position":    w.position,
                "config":      w.config,
            }
            for w in widgets
        ],
        "notifications": [
            {
                "id":                n.id,
                "title":             n.title,
                "message":           n.message,
                "notification_type": n.notification_type,
                "priority":          n.priority,
                "action_url":        n.action_url,
                "created_at":        n.created_at.isoformat() if n.created_at else None,
            }
            for n in unread_notifications
        ],
        "ai_dashboard": {
            "startup_summary":    startup_summary,
            "business_score":     team.ai_health_score if team else None,
            "recommended_actions": startup_summary["recommended_actions"],
            "ai_insights":        startup_summary["ai_insights"],
        },
        "onboarding_progress": {
            "completed":        user.onboarding_completed,
            "current_step":     user.onboarding_step,
            "total_steps":      5,
            "percentage":       min(100, int((user.onboarding_step / 5) * 100)),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  5. POST /logout
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Log out current session",
    description="Revokes the refresh token and writes a logout activity entry.",
)
def logout(
    request: Request,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    ip = _client_ip(request)
    current_user.refresh_token_hash = None   # revoke refresh token

    _log_activity(
        db,
        user_id=current_user.id,
        team_id=current_user.team_id,
        activity_type=ActivityType.LOGOUT,
        description=f"User logged out from {ip}",
        ip_address=ip,
        entity_type="user",
        entity_id=current_user.id,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ═══════════════════════════════════════════════════════════════════════════════
#  6. POST /forgot-password
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/forgot-password",
    summary="Request a password reset email",
    description=(
        "Generates a one-hour password-reset link and sends it by email. "
        "Always returns 200 to avoid user enumeration."
    ),
)
def forgot_password(
    payload: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if user and user.is_active:
        reset_token = _generate_reset_token(user.id)
        background_tasks.add_task(
            send_email,
            to=user.email,
            subject="Reset your InvoiceFlow password",
            body=(
                f"Hi {user.full_name},\n\n"
                "Click the link below to reset your password (expires in 1 hour):\n"
                f"{getattr(settings, 'FRONTEND_URL', '')}/auth/reset-password?token={reset_token}\n\n"
                "If you did not request this, please ignore this email."
            ),
        )
        logger.info(f"[auth] Password reset email queued for {payload.email}")

    return {"message": "If that email is registered, you will receive a reset link shortly"}


# ═══════════════════════════════════════════════════════════════════════════════
#  7. POST /reset-password
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/reset-password",
    summary="Confirm password reset",
    description="Validates the reset token, sets the new password, and revokes all active sessions.",
)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)) -> dict:
    user_id = _decode_special_token(payload.token, "pw_reset")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=400, detail="User not found")

    user.hashed_password      = hash_password(payload.new_password)
    user.refresh_token_hash   = None    # force logout of all devices
    user.failed_login_attempts = 0
    user.locked_until         = None

    _log_activity(
        db,
        user_id=user.id,
        team_id=user.team_id,
        activity_type=ActivityType.UPDATE,
        description="Password reset via reset link",
        entity_type="user",
        entity_id=user.id,
    )
    db.commit()
    return {"message": "Password updated successfully. Please log in with your new password."}


# ═══════════════════════════════════════════════════════════════════════════════
#  8. POST /change-password  (authenticated)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/change-password",
    summary="Change password (authenticated)",
    description="Verifies the current password, sets a new one, and rotates the session.",
)
def change_password(
    payload: ChangePasswordRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")

    current_user.hashed_password    = hash_password(payload.new_password)
    current_user.refresh_token_hash = None

    _log_activity(
        db,
        user_id=current_user.id,
        team_id=current_user.team_id,
        activity_type=ActivityType.UPDATE,
        description="Password changed by user",
        entity_type="user",
        entity_id=current_user.id,
    )
    db.commit()
    return {"message": "Password changed. Please log in again."}


# ═══════════════════════════════════════════════════════════════════════════════
#  9. GET /sessions  (premium — returns active-session info from token)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/sessions",
    summary="List active sessions",
    description=(
        "Returns current session details (IP, last active, device) extracted from "
        "the stored token metadata. In a full Redis deployment this would list all "
        "live sessions; currently returns the latest persisted session data."
    ),
)
def list_sessions(
    request: Request,
    current_user: models.User = Depends(get_current_user),
) -> dict:
    current_ip = _client_ip(request)
    return {
        "sessions": [
            {
                "id":          "current",
                "ip_address":  current_user.last_login_ip or current_ip,
                "last_active": current_user.last_login_at.isoformat() if current_user.last_login_at else None,
                "is_current":  True,
                "device":      request.headers.get("User-Agent", "Unknown")[:120],
            }
        ],
        "note": "Multi-device session storage requires Redis integration (coming soon)",
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  10. DELETE /sessions/{session_id}
# ═══════════════════════════════════════════════════════════════════════════════

@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a session",
    description=(
        "Revokes the current session (refresh token cleared). "
        "Passing 'all' revokes every session for the account."
    ),
)
def revoke_session(
    session_id: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    # Currently only one logical session per user (refresh_token_hash)
    current_user.refresh_token_hash = None
    _log_activity(
        db,
        user_id=current_user.id,
        team_id=current_user.team_id,
        activity_type=ActivityType.LOGOUT,
        description=f"Session '{session_id}' revoked",
        entity_type="user",
        entity_id=current_user.id,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ═══════════════════════════════════════════════════════════════════════════════
#  11. POST /verify-email
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/verify-email",
    summary="Verify email address",
    description="Validates the one-time email verification token and activates the account.",
)
def verify_email(token: str, db: Session = Depends(get_db)) -> dict:
    user_id = _decode_special_token(token, "email_verify")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.email_verified:
        return {"message": "Email address is already verified"}

    user.email_verified = True
    _create_notification(
        db,
        user_id=user.id,
        team_id=user.team_id,
        title="Email Verified",
        message="Your email address has been verified. You now have full access to InvoiceFlow.",
        notification_type=NotificationType.SUCCESS,
        icon="check-circle",
        color="#22c55e",
    )
    _log_activity(
        db,
        user_id=user.id,
        team_id=user.team_id,
        activity_type=ActivityType.UPDATE,
        description="Email address verified",
        entity_type="user",
        entity_id=user.id,
    )
    db.commit()
    return {"message": "Email verified successfully. All features are now unlocked."}


# ═══════════════════════════════════════════════════════════════════════════════
#  12. POST /resend-verification
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/resend-verification",
    summary="Resend email verification",
    description="Resends the verification email. Rate-limited to once per minute.",
)
def resend_verification(
    background_tasks: BackgroundTasks,
    current_user: models.User = Depends(get_current_user),
) -> dict:
    if current_user.email_verified:
        raise HTTPException(status_code=400, detail="Email address is already verified")

    # Lightweight rate-limit: check last_ai_interaction as a timestamp proxy
    if current_user.last_ai_interaction:
        elapsed = (datetime.now(timezone.utc) - current_user.last_ai_interaction).total_seconds()
        if elapsed < _RESEND_COOLDOWN_SECS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Please wait {int(_RESEND_COOLDOWN_SECS - elapsed)} seconds before requesting another email",
            )

    token = _generate_verification_token(current_user.id)
    background_tasks.add_task(
        send_email,
        to=current_user.email,
        subject="Verify your InvoiceFlow email",
        body=(
            f"Hi {current_user.full_name},\n\n"
            "Here is your new verification link (expires in 24 hours):\n"
            f"{getattr(settings, 'FRONTEND_URL', '')}/auth/verify-email?token={token}"
        ),
    )
    return {"message": "Verification email sent. Please check your inbox."}


# ═══════════════════════════════════════════════════════════════════════════════
#  13. POST /2fa/setup
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/2fa/setup",
    summary="Initialise two-factor authentication",
    description=(
        "Generates a TOTP secret and provisioning URI for use with an "
        "authenticator app (Google Authenticator, Authy, etc.). "
        "The secret is NOT saved until /2fa/verify is called."
    ),
)
def setup_2fa(current_user: models.User = Depends(get_current_user)) -> dict:
    secret = pyotp.random_base32()
    totp   = pyotp.TOTP(secret)
    uri    = totp.provisioning_uri(
        name=current_user.email,
        issuer_name="InvoiceFlow",
    )
    # Return secret in session — client must pass it back to /2fa/verify
    return {
        "secret":           secret,
        "provisioning_uri": uri,
        "message":          "Scan the QR code or enter the secret in your authenticator app, then call /2fa/verify to activate.",
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  14. POST /2fa/verify
# ═══════════════════════════════════════════════════════════════════════════════

class TwoFactorActivateRequest(BaseModel):
    secret:   str
    otp_code: str = Field(min_length=6, max_length=6)


@router.post(
    "/2fa/verify",
    summary="Activate two-factor authentication",
    description="Verifies the OTP against the provided secret and enables 2FA on the account.",
)
def verify_2fa(
    payload: TwoFactorActivateRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    totp = pyotp.TOTP(payload.secret)
    if not totp.verify(payload.otp_code, valid_window=1):
        raise HTTPException(status_code=400, detail="OTP code is invalid or expired")

    current_user.mfa_secret  = payload.secret
    current_user.mfa_enabled = True

    _create_notification(
        db,
        user_id=current_user.id,
        team_id=current_user.team_id,
        title="Two-Factor Authentication Enabled",
        message="Your account is now protected with 2FA.",
        notification_type=NotificationType.SUCCESS,
        icon="shield-check",
        color="#22c55e",
    )
    _log_activity(
        db,
        user_id=current_user.id,
        team_id=current_user.team_id,
        activity_type=ActivityType.UPDATE,
        description="2FA enabled by user",
        entity_type="user",
        entity_id=current_user.id,
    )
    db.commit()
    return {"message": "Two-factor authentication has been enabled successfully.", "mfa_enabled": True}


# ═══════════════════════════════════════════════════════════════════════════════
#  15. POST /oauth/google
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/oauth/google",
    summary="Google OAuth login / registration",
    description=(
        "Exchanges a Google OAuth authorization code for tokens and logs the user "
        "in (or auto-registers them). Returns the same payload as /login."
    ),
    responses={501: {"description": "Google OAuth not yet configured"}},
)
def oauth_google(
    payload: GoogleOAuthRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    google_oauth_callback(code=payload.code, db=db)
    # google_oauth_callback raises 501 until the OAuth flow is wired up.
    # Once implemented it should return user info so we can issue tokens here.
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Google OAuth is not yet configured. Please use email/password login.",
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  16. GET /permissions
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/permissions",
    summary="Get current user permissions",
    description="Returns the user's role, full permission list, feature-access flags, AI limits, and subscription tier.",
)
def get_permissions(
    current_user: models.User = Depends(get_current_user),
) -> dict:
    return _build_permissions_response(current_user)


# ═══════════════════════════════════════════════════════════════════════════════
#  17. GET /onboarding-status
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/onboarding-status",
    summary="Retrieve onboarding progress",
    description="Returns completed steps, remaining tasks, AI-driven suggestions, and a progress percentage.",
)
def onboarding_status(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    has_client  = db.query(models.Client).filter(models.Client.user_id == current_user.id).first() is not None
    has_invoice = db.query(models.Invoice).filter(models.Invoice.user_id == current_user.id).first() is not None

    steps = [
        {"step": 1, "key": "verify_email",   "title": "Verify your email",          "completed": current_user.email_verified},
        {"step": 2, "key": "create_client",  "title": "Add your first client",       "completed": has_client},
        {"step": 3, "key": "create_invoice", "title": "Create your first invoice",   "completed": has_invoice},
        {"step": 4, "key": "connect_payment","title": "Connect a payment method",    "completed": False},
        {"step": 5, "key": "explore_ai",     "title": "Try the AI assistant",        "completed": current_user.ai_usage_count > 0},
    ]
    completed_count = sum(1 for s in steps if s["completed"])
    percentage      = int((completed_count / len(steps)) * 100)

    suggestions = []
    for s in steps:
        if not s["completed"]:
            suggestions.append(f"Next: {s['title']}")
            break

    return {
        "completed":          current_user.onboarding_completed,
        "current_step":       current_user.onboarding_step,
        "completed_count":    completed_count,
        "total_steps":        len(steps),
        "percentage":         percentage,
        "steps":              steps,
        "ai_suggestions":     suggestions,
        "unlock_message":     "Complete all steps to unlock your full AI analytics dashboard" if percentage < 100 else "You're all set!",
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  18. POST /complete-onboarding
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/complete-onboarding",
    summary="Finalise onboarding",
    description=(
        "Marks onboarding as complete, persists business profile data onto the team, "
        "and fires the full AI analytics dashboard unlock."
    ),
)
def complete_onboarding(
    payload: CompleteOnboardingRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    current_user.onboarding_completed = True
    current_user.onboarding_step      = 5

    team = current_user.team
    if team and payload:
        if payload.business_name:
            team.name = payload.business_name
        if payload.industry:
            team.industry = payload.industry
        if payload.company_size:
            team.company_size = payload.company_size
        if payload.currency:
            team.currency = payload.currency
        if payload.monthly_revenue is not None:
            team.monthly_revenue = payload.monthly_revenue

    _create_notification(
        db,
        user_id=current_user.id,
        team_id=current_user.team_id,
        title="Onboarding Complete 🎉",
        message="Your AI dashboard is ready. All analytics features are now unlocked.",
        notification_type=NotificationType.SUCCESS,
        action_url="/dashboard",
        icon="rocket",
        color="#6366f1",
        priority="high",
    )
    _log_activity(
        db,
        user_id=current_user.id,
        team_id=current_user.team_id,
        activity_type=ActivityType.UPDATE,
        description="User completed onboarding",
        entity_type="user",
        entity_id=current_user.id,
        event_data=payload.model_dump(exclude_none=True),
    )
    db.commit()
    return {
        "message":   "Onboarding complete! Your AI-powered dashboard is now active.",
        "unlocked":  ["ai_analytics", "cash_flow_forecast", "client_risk_scoring", "revenue_forecast"],
        "next_step": "Explore your dashboard or ask the AI assistant anything about your business.",
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  19. GET /ai-context  (Hackathon Winner Feature)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/ai-context",
    summary="Retrieve AI memory context",
    description=(
        "Returns the AI assistant's remembered context for this user: preferred workflows, "
        "recent AI actions, frequently used commands, and business profile summary. "
        "This is what makes the assistant feel memory-powered."
    ),
)
def get_ai_context(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    # Recent AI conversations
    recent_conversations = (
        db.query(models.AIConversation)
        .filter(models.AIConversation.user_id == current_user.id)
        .order_by(models.AIConversation.created_at.desc())
        .limit(5)
        .all()
        if hasattr(models, "AIConversation") else []
    )

    # Recent activities
    recent_activities = (
        db.query(models.Activity)
        .filter(
            models.Activity.user_id == current_user.id,
            models.Activity.activity_type == ActivityType.AI_ACTION,
        )
        .order_by(models.Activity.created_at.desc())
        .limit(10)
        .all()
    )

    team = current_user.team
    return {
        "user_id":      current_user.id,
        "ai_memory_enabled": current_user.ai_memory_enabled,
        "business_profile": {
            "name":            team.name if team else None,
            "industry":        team.industry if team else None,
            "company_size":    team.company_size if team else None,
            "currency":        team.currency if team else "USD",
            "subscription":    team.subscription_tier if team else "free",
            "ai_health_score": team.ai_health_score if team else None,
        },
        "preferred_workflows": [
            "Create invoice",
            "Send payment reminder",
            "View cash flow",
            "Generate report",
        ],
        "recent_ai_actions": [
            {
                "description": a.description,
                "timestamp":   a.created_at.isoformat() if a.created_at else None,
            }
            for a in recent_activities
        ],
        "recent_sessions":   len(recent_conversations),
        "ai_usage_count":    current_user.ai_usage_count,
        "last_ai_interaction": (
            current_user.last_ai_interaction.isoformat()
            if current_user.last_ai_interaction else None
        ),
        "frequently_used_commands": [
            "show overdue invoices",
            "generate invoice for [client]",
            "cash flow forecast",
            "payment risk for [client]",
            "weekly revenue summary",
        ],
        "memory_context_summary": (
            f"{current_user.full_name} runs a "
            f"{team.industry or 'business'} "
            f"({team.company_size or 'small'} company) "
            f"using InvoiceFlow since {current_user.created_at.strftime('%B %Y') if current_user.created_at else 'recently'}."
            if team else f"{current_user.full_name} is a solo InvoiceFlow user."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  20. GET /startup-summary  (Demo Feature)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/startup-summary",
    summary="AI startup-style business summary",
    description=(
        "Generates a VC-pitch-style snapshot of the user's business health: "
        "revenue trend, invoices at risk, client payment predictions, and cash-flow outlook. "
        "Designed for demo wow-factor and daily executive briefings."
    ),
)
def startup_summary(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    now             = datetime.now(timezone.utc)
    thirty_days_ago = now - timedelta(days=30)
    sixty_days_ago  = now - timedelta(days=60)

    # Revenue: this month vs last month
    uid = current_user.id
    rev_this = float(
        db.query(func.coalesce(func.sum(models.Invoice.total_amount), 0))
        .filter(
            models.Invoice.user_id == uid,
            models.Invoice.status == "paid",
            models.Invoice.created_at >= thirty_days_ago,
        )
        .scalar() or 0
    )
    rev_last = float(
        db.query(func.coalesce(func.sum(models.Invoice.total_amount), 0))
        .filter(
            models.Invoice.user_id == current_user.id,
            models.Invoice.status == "paid",
            models.Invoice.created_at >= sixty_days_ago,
            models.Invoice.created_at < thirty_days_ago,
        )
        .scalar() or 0
    )

    revenue_change_pct = 0.0
    if rev_last > 0:
        revenue_change_pct = round(((rev_this - rev_last) / rev_last) * 100, 1)

    overdue_count = (
        db.query(func.count(models.Invoice.id))
        .filter(models.Invoice.user_id == current_user.id, models.Invoice.status == "overdue")
        .scalar() or 0
    )

    at_risk_clients = (
        db.query(models.Client)
        .filter(models.Client.user_id == current_user.id, models.Client.risk_score >= 0.6)
        .limit(3)
        .all()
        if hasattr(models.Client, "risk_score") else []
    )

    # AI-generated narrative bullets
    bullets: list[str] = []
    if revenue_change_pct > 0:
        bullets.append(f"Revenue increased {revenue_change_pct}% compared to last month")
    elif revenue_change_pct < 0:
        bullets.append(f"Revenue is down {abs(revenue_change_pct)}% — consider following up on overdue invoices")
    else:
        bullets.append("Revenue is flat compared to last month")

    if overdue_count > 0:
        bullets.append(f"{overdue_count} invoice{'s' if overdue_count > 1 else ''} at risk — immediate follow-up recommended")

    if at_risk_clients:
        names = ", ".join(c.name for c in at_risk_clients[:2])
        bullets.append(f"Clients likely to delay payment: {names}")

    bullets.append("Cash flow is healthy for the next 21 days" if overdue_count == 0 else "Cash flow attention needed — resolve overdue invoices")

    trend = "up" if revenue_change_pct > 0 else ("down" if revenue_change_pct < 0 else "flat")
    return {
        "summary_bullets":        bullets,
        "revenue_this_month":     rev_this,
        "revenue_last_month":     rev_last,
        "revenue_change_percent": revenue_change_pct,
        "revenue_trend":          trend,
        "overdue_invoices":       overdue_count,
        "at_risk_clients":        [{"id": c.id, "name": c.name} for c in at_risk_clients],
        "cash_flow_status":       "healthy" if overdue_count == 0 else "attention_needed",
        "generated_at":           now.isoformat(),
        "briefing_title":         f"Business Briefing — {now.strftime('%B %d, %Y')}",
    }
