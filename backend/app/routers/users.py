# ═══════════════════════════════════════════════════════════════════════════════
#  InvoiceFlow — routers/users.py
#  User management, team workspaces, dynamic dashboard engine,
#  AI productivity insights, smart activity feed, and team analytics.
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import asc, desc, func, or_
from sqlalchemy.orm import Session

from app import models
from app.auth import get_admin_user, get_current_user, get_manager_user, hash_password
from app.core.constants import (
    ActivityType,
    DashboardWidgetType,
    NotificationType,
)
from app.core.permissions import PERMISSION_MATRIX, UserRole
from app.database import get_db

logger = logging.getLogger("invoiceflow.users")

router = APIRouter(prefix="/users", tags=["Users & Teams"])

# ── Tier seat limits ───────────────────────────────────────────────────────────
_TIER_SEAT_LIMITS: dict[str, int] = {
    "free":       3,
    "starter":    10,
    "pro":        50,
    "enterprise": 9999,
}

# ═══════════════════════════════════════════════════════════════════════════════
#  LOCAL SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class TeamCreateRequest(BaseModel):
    name:              str           = Field(min_length=1, max_length=150)
    industry:          Optional[str] = None
    company_size:      Optional[str] = None
    country:           Optional[str] = None
    currency:          str           = "USD"
    timezone:          str           = "UTC"
    subscription_tier: str           = "free"
    branding:          Optional[dict] = None


class TeamUpdateRequest(BaseModel):
    name:              Optional[str]  = None
    industry:          Optional[str]  = None
    company_size:      Optional[str]  = None
    currency:          Optional[str]  = None
    timezone:          Optional[str]  = None
    ai_preferences:    Optional[dict] = None
    branding:          Optional[dict] = None
    feature_overrides: Optional[dict] = None


class UserUpdateRequest(BaseModel):
    full_name:                Optional[str]  = None
    timezone:                 Optional[str]  = None
    language:                 Optional[str]  = None
    country:                  Optional[str]  = None
    theme_preference:         Optional[str]  = None
    avatar_url:               Optional[str]  = None
    phone:                    Optional[str]  = None
    notification_preferences: Optional[dict] = None
    dashboard_layout:         Optional[dict] = None
    voice_enabled:            Optional[bool] = None
    command_palette_enabled:  Optional[bool] = None
    ai_memory_enabled:        Optional[bool] = None


class WidgetCreateRequest(BaseModel):
    widget_type:       str
    title:             Optional[str] = None
    config:            dict          = Field(default_factory=dict)
    data_source:       Optional[str] = None
    position_x:        int           = 0
    position_y:        int           = 0
    width:             int           = Field(default=2, ge=1, le=12)
    height:            int           = Field(default=2, ge=1, le=6)
    refresh_interval:  int           = 300
    animation_enabled: bool          = True
    theme:             str           = "default"


class WidgetUpdateRequest(BaseModel):
    title:             Optional[str]  = None
    config:            Optional[dict] = None
    position_x:        Optional[int]  = None
    position_y:        Optional[int]  = None
    width:             Optional[int]  = None
    height:            Optional[int]  = None
    refresh_interval:  Optional[int]  = None
    is_visible:        Optional[bool] = None
    minimized:         Optional[bool] = None
    animation_enabled: Optional[bool] = None
    theme:             Optional[str]  = None
    ai_personalized:   Optional[bool] = None


class LayoutUpdateRequest(BaseModel):
    widgets: list[dict]   # [{id, position_x, position_y, width, height}]


# ═══════════════════════════════════════════════════════════════════════════════
#  INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80]


def _unique_team_slug(name: str, db: Session) -> str:
    base, slug, counter = _slugify(name) or "workspace", _slugify(name) or "workspace", 1
    while db.query(models.Team).filter(models.Team.slug == slug).first():
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def _log_activity(
    db: Session,
    *,
    user_id: Optional[int],
    team_id: Optional[int],
    activity_type: str,
    description: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    event_data: Optional[dict] = None,
    importance_score: float = 0.5,
) -> None:
    db.add(models.Activity(
        user_id=user_id,
        team_id=team_id,
        activity_type=activity_type,
        entity_type=entity_type,
        entity_id=entity_id,
        description=description,
        event_data=event_data or {},
        importance_score=importance_score,
    ))


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
    db.add(models.Notification(
        user_id=user_id,
        team_id=team_id,
        title=title,
        message=message,
        notification_type=notification_type,
        action_url=action_url,
        icon=icon,
        color=color,
        priority=priority,
    ))


def _seed_default_widgets(user_id: int, db: Session) -> None:
    """Seed the standard 5-widget starter dashboard for a new user."""
    defaults = [
        {"widget_type": DashboardWidgetType.KPI,      "title": "Revenue Overview",    "x": 0, "y": 0, "w": 6, "h": 2},
        {"widget_type": DashboardWidgetType.CHART,     "title": "Invoice Trends",      "x": 6, "y": 0, "w": 6, "h": 2},
        {"widget_type": DashboardWidgetType.AI_TIPS,   "title": "AI Recommendations",  "x": 0, "y": 2, "w": 4, "h": 2},
        {"widget_type": DashboardWidgetType.ACTIVITY,  "title": "Recent Activity",     "x": 4, "y": 2, "w": 4, "h": 2},
        {"widget_type": DashboardWidgetType.CASHFLOW,  "title": "Cash Flow",           "x": 8, "y": 2, "w": 4, "h": 2},
    ]
    for d in defaults:
        db.add(models.DashboardWidget(
            user_id=user_id,
            widget_type=d["widget_type"],
            title=d["title"],
            position_x=d["x"],
            position_y=d["y"],
            width=d["w"],
            height=d["h"],
            is_visible=True,
            config={},
            refresh_interval=300,
            animation_enabled=True,
        ))


def _serialize_user(user: models.User, include_stats: bool = False, db: Optional[Session] = None) -> dict:
    out: dict = {
        "id":                    user.id,
        "full_name":             user.full_name,
        "email":                 user.email,
        "role":                  user.role,
        "is_active":             user.is_active,
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
        "updated_at":            user.updated_at.isoformat() if user.updated_at else None,
    }
    if include_stats and db:
        now = datetime.now(timezone.utc)
        thirty = now - timedelta(days=30)
        out["stats"] = {
            "total_invoices": db.query(func.count(models.Invoice.id))
                .filter(models.Invoice.user_id == user.id).scalar() or 0,
            "invoices_this_month": db.query(func.count(models.Invoice.id))
                .filter(models.Invoice.user_id == user.id, models.Invoice.created_at >= thirty).scalar() or 0,
            "revenue_this_month": float(
                db.query(func.coalesce(func.sum(models.Invoice.total_amount), 0))
                .filter(models.Invoice.user_id == user.id, models.Invoice.status == "paid",
                        models.Invoice.created_at >= thirty).scalar() or 0
            ),
            "total_clients": db.query(func.count(models.Client.id))
                .filter(models.Client.user_id == user.id).scalar() or 0,
            "overdue_invoices": db.query(func.count(models.Invoice.id))
                .filter(models.Invoice.user_id == user.id, models.Invoice.status == "overdue").scalar() or 0,
            "ai_usage_count": user.ai_usage_count,
        }
    return out


def _serialize_widget(w: models.DashboardWidget) -> dict:
    return {
        "id":               w.id,
        "widget_type":      w.widget_type,
        "title":            w.title,
        "config":           w.config or {},
        "data_source":      w.data_source,
        "position_x":       w.position_x,
        "position_y":       w.position_y,
        "width":            w.width,
        "height":           w.height,
        "refresh_interval": w.refresh_interval,
        "ai_personalized":  w.ai_personalized,
        "animation_enabled": w.animation_enabled,
        "minimized":        w.minimized,
        "theme":            w.theme,
        "is_visible":       w.is_visible,
        "created_at":       w.created_at.isoformat() if w.created_at else None,
    }


def _require_team_member(current_user: models.User, team_id: int) -> None:
    if current_user.team_id != team_id:
        raise HTTPException(status_code=403, detail="You do not belong to this team")


def _require_owner_or_admin(current_user: models.User) -> None:
    allowed = {UserRole.ADMIN.value, UserRole.SUPERADMIN.value}
    if current_user.role not in allowed:
        raise HTTPException(status_code=403, detail="Admin or owner access required")


# ═══════════════════════════════════════════════════════════════════════════════
#  TEAM ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/teams",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new team / workspace",
    description=(
        "Creates a team, assigns the caller as owner, seeds default dashboard "
        "widgets, enforces unique slug, and logs the creation activity."
    ),
)
def create_team(
    payload: TeamCreateRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    # Prevent duplicate names per owner
    existing = (
        db.query(models.Team)
        .filter(models.Team.owner_id == current_user.id, models.Team.name == payload.name)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You already have a team named '{payload.name}'",
        )

    team = models.Team(
        name=payload.name,
        slug=_unique_team_slug(payload.name, db),
        owner_id=current_user.id,
        subscription_tier=payload.subscription_tier,
        industry=payload.industry,
        company_size=payload.company_size,
        country=payload.country,
        timezone=payload.timezone,
        currency=payload.currency,
        ai_preferences={},
        branding=payload.branding or {},
        feature_overrides={},
    )
    db.add(team)
    db.flush()

    # Assign caller to new team
    current_user.team_id = team.id
    current_user.role    = UserRole.ADMIN.value

    # Seed widgets for the owner
    _seed_default_widgets(current_user.id, db)

    _log_activity(
        db,
        user_id=current_user.id,
        team_id=team.id,
        activity_type=ActivityType.CREATE,
        description=f"Created workspace '{team.name}'",
        entity_type="team",
        entity_id=team.id,
        importance_score=0.8,
    )
    _create_notification(
        db,
        user_id=current_user.id,
        team_id=team.id,
        title="Workspace created",
        message=f"Your workspace '{team.name}' is ready. Invite team members to get started.",
        notification_type=NotificationType.SUCCESS,
        action_url="/settings/team",
        icon="building",
        color="#6366f1",
    )
    db.commit()
    db.refresh(team)

    return {
        "success": True,
        "message": f"Workspace '{team.name}' created successfully",
        "team": {
            "id":                team.id,
            "name":              team.name,
            "slug":              team.slug,
            "owner_id":          team.owner_id,
            "subscription_tier": team.subscription_tier,
            "industry":          team.industry,
            "company_size":      team.company_size,
            "country":           team.country,
            "currency":          team.currency,
            "timezone":          team.timezone,
            "branding":          team.branding,
            "ai_preferences":    team.ai_preferences,
            "created_at":        team.created_at.isoformat() if team.created_at else None,
        },
    }


@router.get(
    "/teams/{team_id}",
    summary="Get full team details",
    description=(
        "Returns team metadata, member count, invoice stats, revenue summary, "
        "subscription tier, and an AI analytics snapshot."
    ),
)
def get_team(
    team_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    team = db.query(models.Team).filter(models.Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    _require_team_member(current_user, team_id)

    now = datetime.now(timezone.utc)
    thirty = now - timedelta(days=30)

    member_count = db.query(func.count(models.User.id)).filter(
        models.User.team_id == team_id, models.User.is_active == True
    ).scalar() or 0

    total_invoices = db.query(func.count(models.Invoice.id)).filter(
        models.Invoice.team_id == team_id
    ).scalar() or 0

    revenue_30d = float(
        db.query(func.coalesce(func.sum(models.Invoice.total_amount), 0))
        .filter(
            models.Invoice.team_id == team_id,
            models.Invoice.status == "paid",
            models.Invoice.created_at >= thirty,
        ).scalar() or 0
    )

    overdue_count = db.query(func.count(models.Invoice.id)).filter(
        models.Invoice.team_id == team_id,
        models.Invoice.status == "overdue",
    ).scalar() or 0

    active_workflows = db.query(func.count(models.Workflow.id)).filter(
        models.Workflow.team_id == team_id,
        models.Workflow.is_active == True,
    ).scalar() or 0

    seat_limit = _TIER_SEAT_LIMITS.get(team.subscription_tier, 3)

    return {
        "id":                  team.id,
        "name":                team.name,
        "slug":                team.slug,
        "owner_id":            team.owner_id,
        "subscription_tier":   team.subscription_tier,
        "subscription_expires": (
            team.subscription_expires.isoformat() if team.subscription_expires else None
        ),
        "industry":            team.industry,
        "company_size":        team.company_size,
        "country":             team.country,
        "currency":            team.currency,
        "timezone":            team.timezone,
        "branding":            team.branding,
        "ai_preferences":      team.ai_preferences,
        "feature_overrides":   team.feature_overrides,
        "ai_health_score":     team.ai_health_score,
        "created_at":          team.created_at.isoformat() if team.created_at else None,
        "members": {
            "count":     member_count,
            "seat_limit": seat_limit,
            "seats_available": max(0, seat_limit - member_count),
        },
        "analytics": {
            "total_invoices":    total_invoices,
            "revenue_30d":       revenue_30d,
            "overdue_invoices":  overdue_count,
            "active_workflows":  active_workflows,
        },
    }


@router.put(
    "/teams/{team_id}",
    summary="Update team settings",
    description="Update team name, industry, branding, AI preferences, or feature flags. Admin only.",
)
def update_team(
    team_id: int,
    payload: TeamUpdateRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    _require_team_member(current_user, team_id)
    _require_owner_or_admin(current_user)

    team = db.query(models.Team).filter(models.Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    if payload.name is not None:
        team.name = payload.name
    if payload.industry is not None:
        team.industry = payload.industry
    if payload.company_size is not None:
        team.company_size = payload.company_size
    if payload.currency is not None:
        team.currency = payload.currency
    if payload.timezone is not None:
        team.timezone = payload.timezone
    if payload.ai_preferences is not None:
        team.ai_preferences = {**(team.ai_preferences or {}), **payload.ai_preferences}
    if payload.branding is not None:
        team.branding = {**(team.branding or {}), **payload.branding}
    if payload.feature_overrides is not None:
        team.feature_overrides = {**(team.feature_overrides or {}), **payload.feature_overrides}

    _log_activity(
        db,
        user_id=current_user.id,
        team_id=team_id,
        activity_type=ActivityType.UPDATE,
        description=f"Updated workspace settings for '{team.name}'",
        entity_type="team",
        entity_id=team_id,
    )
    db.commit()
    return {"success": True, "message": "Team settings updated"}


# ═══════════════════════════════════════════════════════════════════════════════
#  USER LIST
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/",
    summary="List all team users",
    description=(
        "Paginated, searchable, filterable list of users in the caller's team. "
        "Includes last-active timestamps and online/offline status."
    ),
)
def list_users(
    page:     int    = Query(default=1, ge=1),
    per_page: int    = Query(default=20, ge=1, le=100),
    search:   Optional[str]  = Query(default=None, description="Search by name or email"),
    role:     Optional[str]  = Query(default=None, description="Filter by role"),
    sort:     str    = Query(default="created_at", description="Sort field"),
    order:    str    = Query(default="desc", enum=["asc", "desc"]),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not current_user.team_id:
        raise HTTPException(status_code=403, detail="You are not a member of any team")

    q = db.query(models.User).filter(models.User.team_id == current_user.team_id)

    if search:
        term = f"%{search}%"
        q = q.filter(
            or_(models.User.full_name.ilike(term), models.User.email.ilike(term))
        )
    if role:
        q = q.filter(models.User.role == role)

    total = q.count()

    sort_col = getattr(models.User, sort, models.User.created_at)
    q = q.order_by(desc(sort_col) if order == "desc" else asc(sort_col))
    users = q.offset((page - 1) * per_page).limit(per_page).all()

    # "online" = logged in within the last 15 minutes
    online_cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)

    return {
        "success": True,
        "data": [
            {
                **_serialize_user(u),
                "is_online": bool(u.last_login_at and u.last_login_at > online_cutoff),
            }
            for u in users
        ],
        "metadata": {
            "total":    total,
            "page":     page,
            "per_page": per_page,
            "pages":    (total + per_page - 1) // per_page,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  SINGLE USER
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/me",
    summary="Get current user's own profile",
    description="Shortcut for /users/{id} — returns the authenticated user's full profile with stats.",
)
def get_me(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return _get_user_profile(current_user.id, current_user, db)


@router.get(
    "/{user_id}",
    summary="Get a user's full profile",
    description=(
        "Returns user stats, recent activities, dashboard config, "
        "assigned invoice counts, and productivity metrics. "
        "Viewers can only access their own profile; managers/admins can access any team member."
    ),
)
def get_user(
    user_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    # Viewers can only see themselves
    if current_user.id != user_id:
        allowed = {UserRole.MANAGER.value, UserRole.ADMIN.value, UserRole.SUPERADMIN.value}
        if current_user.role not in allowed:
            raise HTTPException(status_code=403, detail="You can only view your own profile")
    return _get_user_profile(user_id, current_user, db)


def _get_user_profile(user_id: int, current_user: models.User, db: Session) -> dict:
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Team isolation: non-superadmin can only see same-team users
    if current_user.role != UserRole.SUPERADMIN.value:
        if user.team_id != current_user.team_id:
            raise HTTPException(status_code=403, detail="Access denied — user is in a different team")

    now     = datetime.now(timezone.utc)
    thirty  = now - timedelta(days=30)

    # Recent activities (last 10)
    recent_activities = (
        db.query(models.Activity)
        .filter(models.Activity.user_id == user_id)
        .order_by(models.Activity.created_at.desc())
        .limit(10)
        .all()
    )

    # Productivity metrics
    invoices_this_month = db.query(func.count(models.Invoice.id)).filter(
        models.Invoice.user_id == user_id,
        models.Invoice.created_at >= thirty,
    ).scalar() or 0

    paid_this_month = db.query(func.count(models.Invoice.id)).filter(
        models.Invoice.user_id == user_id,
        models.Invoice.status == "paid",
        models.Invoice.created_at >= thirty,
    ).scalar() or 0

    revenue_this_month = float(
        db.query(func.coalesce(func.sum(models.Invoice.total_amount), 0))
        .filter(
            models.Invoice.user_id == user_id,
            models.Invoice.status == "paid",
            models.Invoice.created_at >= thirty,
        ).scalar() or 0
    )

    collection_rate = round((paid_this_month / invoices_this_month) * 100, 1) if invoices_this_month else 0.0

    return {
        "success":    True,
        "user":       _serialize_user(user, include_stats=True, db=db),
        "activities": [
            {
                "id":            a.id,
                "activity_type": a.activity_type,
                "description":   a.description,
                "entity_type":   a.entity_type,
                "entity_id":     a.entity_id,
                "created_at":    a.created_at.isoformat() if a.created_at else None,
            }
            for a in recent_activities
        ],
        "productivity": {
            "invoices_created_30d":  invoices_this_month,
            "invoices_collected_30d": paid_this_month,
            "revenue_collected_30d": revenue_this_month,
            "collection_rate":       collection_rate,
            "ai_interactions":       user.ai_usage_count,
        },
        "preferences": {
            "theme":                  user.theme_preference,
            "language":               user.language,
            "timezone":               user.timezone,
            "notification_prefs":     user.notification_preferences or {},
            "dashboard_layout":       user.dashboard_layout or {},
            "voice_enabled":          user.voice_enabled,
            "command_palette_enabled": user.command_palette_enabled,
            "ai_memory_enabled":      user.ai_memory_enabled,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  UPDATE USER
# ═══════════════════════════════════════════════════════════════════════════════

@router.put(
    "/{user_id}",
    summary="Update a user's profile",
    description=(
        "Update avatar, preferences, theme, notification settings, language, "
        "and AI personalization settings. Users can update themselves; "
        "admins can update any team member."
    ),
)
def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    is_self = current_user.id == user_id
    is_admin = current_user.role in {UserRole.ADMIN.value, UserRole.SUPERADMIN.value}
    if not is_self and not is_admin:
        raise HTTPException(status_code=403, detail="You can only update your own profile")

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.team_id != current_user.team_id and current_user.role != UserRole.SUPERADMIN.value:
        raise HTTPException(status_code=403, detail="Cannot update user from a different team")

    # Apply updates
    for field in (
        "full_name", "timezone", "language", "country", "theme_preference",
        "avatar_url", "phone", "voice_enabled", "command_palette_enabled", "ai_memory_enabled",
    ):
        val = getattr(payload, field, None)
        if val is not None:
            setattr(user, field, val)

    if payload.notification_preferences is not None:
        user.notification_preferences = {
            **(user.notification_preferences or {}),
            **payload.notification_preferences,
        }
    if payload.dashboard_layout is not None:
        user.dashboard_layout = {
            **(user.dashboard_layout or {}),
            **payload.dashboard_layout,
        }

    _log_activity(
        db,
        user_id=current_user.id,
        team_id=current_user.team_id,
        activity_type=ActivityType.UPDATE,
        description=f"Updated profile for {user.email}",
        entity_type="user",
        entity_id=user_id,
    )
    db.commit()
    db.refresh(user)
    return {"success": True, "message": "Profile updated", "user": _serialize_user(user)}


# ═══════════════════════════════════════════════════════════════════════════════
#  DEACTIVATE USER  (soft delete)
# ═══════════════════════════════════════════════════════════════════════════════

@router.delete(
    "/{user_id}",
    status_code=status.HTTP_200_OK,
    summary="Deactivate (soft-delete) a user",
    description=(
        "Sets is_active=False, revokes the session, and logs the action. "
        "Prevents deactivating the workspace owner. Admin only."
    ),
)
def deactivate_user(
    user_id: int,
    current_user: models.User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> dict:
    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account from this endpoint")

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.team_id != current_user.team_id:
        raise HTTPException(status_code=403, detail="Cannot deactivate a user from a different team")

    # Prevent deactivating the team owner
    team = db.query(models.Team).filter(models.Team.id == user.team_id).first()
    if team and team.owner_id == user_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot deactivate the workspace owner. Transfer ownership first.",
        )

    user.is_active          = False
    user.refresh_token_hash = None   # revoke session immediately

    _log_activity(
        db,
        user_id=current_user.id,
        team_id=current_user.team_id,
        activity_type=ActivityType.DELETE,
        description=f"Deactivated user account: {user.email}",
        entity_type="user",
        entity_id=user_id,
        importance_score=0.9,
    )
    db.commit()
    return {"success": True, "message": f"User {user.email} has been deactivated"}


# ═══════════════════════════════════════════════════════════════════════════════
#  DASHBOARD WIDGET ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/me/widgets",
    summary="Get personalised dashboard widgets",
    description=(
        "Returns the caller's widgets ordered by AI-priority score (AI_TIPS first, "
        "then KPI, then others). Includes visibility state, positions, and refresh intervals."
    ),
)
def get_my_widgets(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    widgets = (
        db.query(models.DashboardWidget)
        .filter(models.DashboardWidget.user_id == current_user.id)
        .all()
    )

    # AI-priority ordering: AI_TIPS → KPI → CHART → INSIGHT → everything else
    priority_order = {
        DashboardWidgetType.AI_TIPS:    0,
        DashboardWidgetType.KPI:        1,
        DashboardWidgetType.CHART:      2,
        DashboardWidgetType.INSIGHT:    3,
        DashboardWidgetType.CASHFLOW:   4,
        DashboardWidgetType.FORECAST:   5,
        DashboardWidgetType.ACTIVITY:   6,
        DashboardWidgetType.LEADERBOARD:7,
        DashboardWidgetType.HEATMAP:    8,
    }
    sorted_widgets = sorted(
        widgets,
        key=lambda w: (priority_order.get(w.widget_type, 99), w.position_y, w.position_x),
    )

    tier = current_user.team.subscription_tier if current_user.team else "free"
    premium_types = {DashboardWidgetType.HEATMAP, DashboardWidgetType.FORECAST, DashboardWidgetType.LEADERBOARD}

    return {
        "success": True,
        "widgets": [_serialize_widget(w) for w in sorted_widgets],
        "metadata": {
            "total":             len(widgets),
            "visible":           sum(1 for w in widgets if w.is_visible),
            "ai_personalized":   sum(1 for w in widgets if w.ai_personalized),
            "subscription_tier": tier,
            "premium_available": tier in ("pro", "enterprise"),
            "locked_widget_types": list(premium_types) if tier == "free" else [],
        },
    }


@router.post(
    "/me/widgets",
    status_code=status.HTTP_201_CREATED,
    summary="Create a dashboard widget",
    description=(
        "Adds a new widget to the caller's dashboard. "
        "Premium widget types (HEATMAP, FORECAST, LEADERBOARD) require a pro/enterprise plan."
    ),
)
def create_widget(
    payload: WidgetCreateRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    # Tier gate on premium widget types
    tier = current_user.team.subscription_tier if current_user.team else "free"
    premium_types = {DashboardWidgetType.HEATMAP, DashboardWidgetType.FORECAST, DashboardWidgetType.LEADERBOARD}
    if payload.widget_type in premium_types and tier not in ("pro", "enterprise"):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Widget type '{payload.widget_type}' requires a Pro or Enterprise subscription",
        )

    # Validate widget type
    valid_types = {t.value for t in DashboardWidgetType}
    if payload.widget_type not in valid_types:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid widget_type '{payload.widget_type}'. Valid: {sorted(valid_types)}",
        )

    widget = models.DashboardWidget(
        user_id=current_user.id,
        widget_type=payload.widget_type,
        title=payload.title or _default_widget_title(payload.widget_type),
        config=payload.config,
        data_source=payload.data_source,
        position_x=payload.position_x,
        position_y=payload.position_y,
        width=payload.width,
        height=payload.height,
        refresh_interval=payload.refresh_interval,
        animation_enabled=payload.animation_enabled,
        theme=payload.theme,
        is_visible=True,
    )
    db.add(widget)
    db.commit()
    db.refresh(widget)
    return {"success": True, "widget": _serialize_widget(widget)}


def _default_widget_title(widget_type: str) -> str:
    return {
        DashboardWidgetType.KPI:        "KPI Tracker",
        DashboardWidgetType.CHART:      "Invoice Chart",
        DashboardWidgetType.INSIGHT:    "AI Insight",
        DashboardWidgetType.ACTIVITY:   "Activity Feed",
        DashboardWidgetType.CASHFLOW:   "Cash Flow",
        DashboardWidgetType.FORECAST:   "Revenue Forecast",
        DashboardWidgetType.LEADERBOARD:"Client Leaderboard",
        DashboardWidgetType.HEATMAP:    "Payment Heatmap",
        DashboardWidgetType.AI_TIPS:    "AI Recommendations",
    }.get(widget_type, "Widget")


@router.put(
    "/me/widgets/{widget_id}",
    summary="Update a dashboard widget",
    description=(
        "Resize, reposition, toggle visibility, update config, change theme, "
        "or enable AI personalization on a single widget."
    ),
)
def update_widget(
    widget_id: int,
    payload: WidgetUpdateRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    widget = db.query(models.DashboardWidget).filter(
        models.DashboardWidget.id == widget_id,
        models.DashboardWidget.user_id == current_user.id,
    ).first()
    if not widget:
        raise HTTPException(status_code=404, detail="Widget not found")

    for field in (
        "title", "position_x", "position_y", "width", "height",
        "refresh_interval", "is_visible", "minimized",
        "animation_enabled", "theme", "ai_personalized",
    ):
        val = getattr(payload, field, None)
        if val is not None:
            setattr(widget, field, val)

    if payload.config is not None:
        widget.config = {**(widget.config or {}), **payload.config}

    db.commit()
    db.refresh(widget)
    return {"success": True, "widget": _serialize_widget(widget)}


@router.delete(
    "/me/widgets/{widget_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a dashboard widget",
)
def delete_widget(
    widget_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    widget = db.query(models.DashboardWidget).filter(
        models.DashboardWidget.id == widget_id,
        models.DashboardWidget.user_id == current_user.id,
    ).first()
    if not widget:
        raise HTTPException(status_code=404, detail="Widget not found")

    db.delete(widget)
    db.commit()
    return {"success": True, "message": "Widget deleted"}


@router.put(
    "/me/widgets/layout",
    summary="Batch update widget layout (drag-and-drop)",
    description=(
        "Accepts a list of {id, position_x, position_y, width, height} objects "
        "and applies them atomically — supports drag-and-drop grid saves."
    ),
)
def update_widget_layout(
    payload: LayoutUpdateRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    widget_ids = [w.get("id") for w in payload.widgets if w.get("id")]
    widgets = (
        db.query(models.DashboardWidget)
        .filter(
            models.DashboardWidget.id.in_(widget_ids),
            models.DashboardWidget.user_id == current_user.id,
        )
        .all()
    )
    widget_map = {w.id: w for w in widgets}

    updated = 0
    for item in payload.widgets:
        wid = item.get("id")
        if not wid or wid not in widget_map:
            continue
        w = widget_map[wid]
        if "position_x" in item:
            w.position_x = item["position_x"]
        if "position_y" in item:
            w.position_y = item["position_y"]
        if "width" in item:
            w.width = item["width"]
        if "height" in item:
            w.height = item["height"]
        updated += 1

    db.commit()
    return {"success": True, "updated_widgets": updated, "message": "Dashboard layout saved"}


# ═══════════════════════════════════════════════════════════════════════════════
#  ACTIVITY FEED
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/me/activity-feed",
    summary="Smart real-time activity feed",
    description=(
        "Returns a combined team activity feed ranked by importance_score. "
        "Covers invoice events, payments, reminders, workflow runs, team joins, "
        "and AI recommendation triggers."
    ),
)
def get_activity_feed(
    page:     int          = Query(default=1, ge=1),
    per_page: int          = Query(default=20, ge=1, le=100),
    types:    Optional[str] = Query(default=None, description="Comma-separated activity types to filter"),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    q = db.query(models.Activity).filter(
        or_(
            models.Activity.user_id == current_user.id,
            models.Activity.team_id == current_user.team_id,
        )
    )
    if types:
        type_list = [t.strip() for t in types.split(",")]
        q = q.filter(models.Activity.activity_type.in_(type_list))

    total = q.count()
    activities = (
        q.order_by(
            desc(models.Activity.importance_score),
            desc(models.Activity.created_at),
        )
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return {
        "success": True,
        "feed": [
            {
                "id":              a.id,
                "activity_type":   a.activity_type,
                "description":     a.description,
                "entity_type":     a.entity_type,
                "entity_id":       a.entity_id,
                "entity_name":     a.entity_name,
                "importance_score": a.importance_score,
                "user_id":         a.user_id,
                "team_id":         a.team_id,
                "event_data":      a.event_data or {},
                "created_at":      a.created_at.isoformat() if a.created_at else None,
            }
            for a in activities
        ],
        "metadata": {
            "total":    total,
            "page":     page,
            "per_page": per_page,
            "pages":    (total + per_page - 1) // per_page,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  AI RECOMMENDATIONS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/me/ai-recommendations",
    summary="Get AI recommendations for the current user",
    description=(
        "Returns pending (non-dismissed) AI recommendations sorted by priority. "
        "Includes estimated impact, effort level, and action steps."
    ),
)
def get_ai_recommendations(
    limit: int = Query(default=10, ge=1, le=50),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    recs = (
        db.query(models.AIRecommendation)
        .filter(
            models.AIRecommendation.user_id == current_user.id,
            models.AIRecommendation.is_dismissed == False,
            or_(
                models.AIRecommendation.expires_at == None,
                models.AIRecommendation.expires_at > datetime.now(timezone.utc),
            ),
        )
        .order_by(
            desc(models.AIRecommendation.confidence),
            desc(models.AIRecommendation.created_at),
        )
        .limit(limit)
        .all()
    )

    priority_sort = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    sorted_recs = sorted(recs, key=lambda r: priority_sort.get(r.priority, 99))

    return {
        "success": True,
        "recommendations": [
            {
                "id":               r.id,
                "title":            r.title,
                "description":      r.description,
                "category":         r.category,
                "priority":         r.priority,
                "estimated_impact": r.estimated_impact,
                "effort_level":     r.effort_level,
                "action_steps":     r.action_steps or [],
                "confidence":       r.confidence,
                "is_accepted":      r.is_accepted,
                "created_at":       r.created_at.isoformat() if r.created_at else None,
            }
            for r in sorted_recs
        ],
        "total": len(sorted_recs),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  PRODUCTIVITY METRICS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/me/productivity",
    summary="AI productivity insights for the current user",
    description=(
        "Computes invoice creation rate, collection success rate, "
        "average turnaround time, and AI usage trend over the last 30 days."
    ),
)
def get_productivity(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    now    = datetime.now(timezone.utc)
    thirty = now - timedelta(days=30)
    sixty  = now - timedelta(days=60)

    # This month
    inv_this = db.query(func.count(models.Invoice.id)).filter(
        models.Invoice.user_id == current_user.id,
        models.Invoice.created_at >= thirty,
    ).scalar() or 0

    paid_this = db.query(func.count(models.Invoice.id)).filter(
        models.Invoice.user_id == current_user.id,
        models.Invoice.status == "paid",
        models.Invoice.created_at >= thirty,
    ).scalar() or 0

    rev_this = float(
        db.query(func.coalesce(func.sum(models.Invoice.total_amount), 0))
        .filter(
            models.Invoice.user_id == current_user.id,
            models.Invoice.status == "paid",
            models.Invoice.created_at >= thirty,
        ).scalar() or 0
    )

    # Last month for trend
    inv_last = db.query(func.count(models.Invoice.id)).filter(
        models.Invoice.user_id == current_user.id,
        models.Invoice.created_at >= sixty,
        models.Invoice.created_at < thirty,
    ).scalar() or 0

    rev_last = float(
        db.query(func.coalesce(func.sum(models.Invoice.total_amount), 0))
        .filter(
            models.Invoice.user_id == current_user.id,
            models.Invoice.status == "paid",
            models.Invoice.created_at >= sixty,
            models.Invoice.created_at < thirty,
        ).scalar() or 0
    )

    invoice_trend = round(((inv_this - inv_last) / max(inv_last, 1)) * 100, 1)
    revenue_trend = round(((rev_this - rev_last) / max(rev_last, 1)) * 100, 1)
    collection_rate = round((paid_this / max(inv_this, 1)) * 100, 1)

    return {
        "success": True,
        "period": "last_30_days",
        "invoices": {
            "created_this_month": inv_this,
            "created_last_month": inv_last,
            "trend_percent":      invoice_trend,
            "paid_this_month":    paid_this,
            "collection_rate":    collection_rate,
        },
        "revenue": {
            "this_month":     rev_this,
            "last_month":     rev_last,
            "trend_percent":  revenue_trend,
        },
        "ai_usage": {
            "total_interactions": current_user.ai_usage_count,
            "tokens_consumed":    current_user.ai_tokens_consumed,
            "last_interaction":   (
                current_user.last_ai_interaction.isoformat()
                if current_user.last_ai_interaction else None
            ),
        },
        "performance_score": _compute_performance_score(collection_rate, invoice_trend, revenue_trend),
    }


def _compute_performance_score(collection_rate: float, invoice_trend: float, revenue_trend: float) -> float:
    """Simple composite 0–100 performance score."""
    score = (
        (collection_rate * 0.5) +
        (min(max(invoice_trend + 50, 0), 100) * 0.25) +
        (min(max(revenue_trend + 50, 0), 100) * 0.25)
    )
    return round(min(100.0, max(0.0, score)), 1)


# ═══════════════════════════════════════════════════════════════════════════════
#  TEAM ANALYTICS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/teams/{team_id}/analytics",
    summary="AI-powered team analytics",
    description=(
        "Returns team efficiency score, member leaderboard, collection success rate, "
        "invoice turnaround time, and business growth trends. Manager+ access."
    ),
)
def get_team_analytics(
    team_id: int,
    current_user: models.User = Depends(get_manager_user),
    db: Session = Depends(get_db),
) -> dict:
    _require_team_member(current_user, team_id)

    now    = datetime.now(timezone.utc)
    thirty = now - timedelta(days=30)

    team_users = db.query(models.User).filter(
        models.User.team_id == team_id,
        models.User.is_active == True,
    ).all()

    member_stats = []
    for u in team_users:
        inv_count = db.query(func.count(models.Invoice.id)).filter(
            models.Invoice.user_id == u.id,
            models.Invoice.created_at >= thirty,
        ).scalar() or 0

        paid_count = db.query(func.count(models.Invoice.id)).filter(
            models.Invoice.user_id == u.id,
            models.Invoice.status == "paid",
            models.Invoice.created_at >= thirty,
        ).scalar() or 0

        revenue = float(
            db.query(func.coalesce(func.sum(models.Invoice.total_amount), 0))
            .filter(
                models.Invoice.user_id == u.id,
                models.Invoice.status == "paid",
                models.Invoice.created_at >= thirty,
            ).scalar() or 0
        )

        member_stats.append({
            "user_id":        u.id,
            "full_name":      u.full_name,
            "avatar_url":     u.avatar_url,
            "role":           u.role,
            "invoices_created": inv_count,
            "invoices_paid":  paid_count,
            "revenue":        revenue,
            "collection_rate": round((paid_count / max(inv_count, 1)) * 100, 1),
        })

    # Sort leaderboard by revenue
    leaderboard = sorted(member_stats, key=lambda m: m["revenue"], reverse=True)

    # Team totals
    total_inv = sum(m["invoices_created"] for m in member_stats)
    total_paid = sum(m["invoices_paid"] for m in member_stats)
    total_rev = sum(m["revenue"] for m in member_stats)
    team_collection_rate = round((total_paid / max(total_inv, 1)) * 100, 1)

    # Overdue count
    overdue_count = db.query(func.count(models.Invoice.id)).filter(
        models.Invoice.team_id == team_id,
        models.Invoice.status == "overdue",
    ).scalar() or 0

    # Active workflows
    active_wf = db.query(func.count(models.Workflow.id)).filter(
        models.Workflow.team_id == team_id,
        models.Workflow.is_active == True,
    ).scalar() or 0

    # AI-derived efficiency score
    efficiency_score = _compute_performance_score(team_collection_rate, 0, 0)

    team = db.query(models.Team).filter(models.Team.id == team_id).first()

    return {
        "success": True,
        "team_id": team_id,
        "period":  "last_30_days",
        "summary": {
            "total_members":      len(team_users),
            "total_invoices":     total_inv,
            "total_paid":         total_paid,
            "total_revenue":      total_rev,
            "collection_rate":    team_collection_rate,
            "overdue_invoices":   overdue_count,
            "active_workflows":   active_wf,
            "ai_health_score":    team.ai_health_score if team else None,
            "efficiency_score":   efficiency_score,
        },
        "leaderboard": leaderboard,
        "insights": [
            f"Team collection rate is {team_collection_rate}%",
            f"{overdue_count} overdue invoice{'s' if overdue_count != 1 else ''} need{'s' if overdue_count == 1 else ''} attention",
            f"{active_wf} active automation workflow{'s' if active_wf != 1 else ''} running",
            f"Top performer: {leaderboard[0]['full_name']} — ${leaderboard[0]['revenue']:,.2f} collected"
            if leaderboard else "No invoice activity yet",
        ],
        "generated_at": now.isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  AI WORKSPACE PERSONALIZATION
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/me/ai-workspace",
    summary="AI workspace personalization context",
    description=(
        "Returns smart dashboard layout suggestions, frequently used actions, "
        "AI quick-action suggestions, recommended widget types, and "
        "personalized analytics cards based on the user's usage patterns."
    ),
)
def get_ai_workspace(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    now    = datetime.now(timezone.utc)
    thirty = now - timedelta(days=30)

    existing_widget_types = {
        w.widget_type for w in
        db.query(models.DashboardWidget.widget_type)
        .filter(models.DashboardWidget.user_id == current_user.id)
        .all()
    }

    all_types  = {t.value for t in DashboardWidgetType}
    tier       = current_user.team.subscription_tier if current_user.team else "free"
    locked     = (
        {DashboardWidgetType.HEATMAP, DashboardWidgetType.FORECAST, DashboardWidgetType.LEADERBOARD}
        if tier == "free" else set()
    )
    suggested_widgets = [
        t for t in all_types
        if t not in existing_widget_types and t not in locked
    ][:4]

    overdue = db.query(func.count(models.Invoice.id)).filter(
        models.Invoice.user_id == current_user.id,
        models.Invoice.status == "overdue",
    ).scalar() or 0

    quick_actions = ["Create invoice", "Add client", "View cash flow", "Ask AI assistant"]
    if overdue > 0:
        quick_actions.insert(0, f"Review {overdue} overdue invoice{'s' if overdue > 1 else ''}")

    return {
        "success": True,
        "workspace": {
            "theme":                 current_user.theme_preference,
            "language":              current_user.language,
            "ai_memory_enabled":     current_user.ai_memory_enabled,
            "command_palette_enabled": current_user.command_palette_enabled,
        },
        "quick_actions":    quick_actions,
        "suggested_widgets": suggested_widgets,
        "frequently_used":   ["invoices", "clients", "reminders", "analytics"],
        "preferred_analytics": [
            "Revenue overview",
            "Payment collection rate",
            "Client risk summary",
        ],
        "ai_tips": [
            "Use voice commands to create invoices hands-free",
            "Enable 2FA to secure your account",
            "Set up automated reminders to reduce late payments",
        ][:3 if not current_user.onboarding_completed else 2],
    }
