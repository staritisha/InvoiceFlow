"""
app/routers/notifications.py

Smart Notification Center for InvoiceFlow AI Platform.
Covers real-time notification CRUD, AI prioritization & importance scoring,
smart grouping, bulk operations, WebSocket info endpoint, autonomous AI alerts,
activity timeline feed, and rich payloads for the live dashboard.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import ActivityType, NotificationType
from app.core.permissions import require_permission
from app.database import get_db
from app.models import (
    Activity,
    BusinessInsight,
    Client,
    Invoice,
    InvoiceStatus,
    Notification,
    User,
)
from app.schemas import (
    NotificationBulkUpdate,
    NotificationCreate,
    NotificationOut,
)
from app.services.ai_service import AIService
from app.services.analytics_service import AnalyticsService
from app.services.notification_service import NotificationService
from app.websocket.manager import ws_manager
from auth import get_current_user

router = APIRouter(prefix="/notifications", tags=["Notifications"])

ai_service = AIService()
notification_service = NotificationService()
analytics_service = AnalyticsService()

# ---------------------------------------------------------------------------
# Supported WebSocket channels and events (returned by /ws/info)
# ---------------------------------------------------------------------------

WS_CHANNELS = [
    "notifications", "invoices", "analytics", "reminders",
    "workflows", "ai_insights", "kpi_updates", "dashboard_activity", "payments",
]

WS_EVENTS = [
    "NEW_NOTIFICATION", "PAYMENT_RECEIVED", "INVOICE_OVERDUE",
    "AI_INSIGHT_GENERATED", "WORKFLOW_COMPLETED", "HIGH_RISK_CLIENT",
    "REVENUE_DROP_ALERT", "REMINDER_SENT", "DASHBOARD_REFRESH",
    "WORKFLOW_STARTED", "WORKFLOW_FAILED", "REMINDER_FAILED",
    "OVERDUE_ESCALATED", "CLIENT_RISK_CHANGED", "KPI_REFRESHED",
    "AI_RECOMMENDATION_GENERATED",
]

# Notification categories for filtering
CATEGORIES = [
    "payment", "invoice", "reminder", "workflow",
    "analytics", "risk", "ai_insight", "system", "team",
]

# Priority ranks for sorting
PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _importance_score(n: Notification) -> int:
    """Heuristic importance score from priority + ai_generated flags."""
    base = {
        "critical": 95, "high": 80, "medium": 60, "low": 40, "info": 20,
    }.get(getattr(n, "priority", "info"), 20)
    if getattr(n, "ai_generated", False):
        base = min(base + 10, 100)
    return base


def _smart_category(n: Notification) -> str:
    """Infer category from notification type string."""
    t = str(n.type or "").lower()
    for cat in CATEGORIES:
        if cat in t:
            return cat
    return "system"


def _next_best_action(n: Notification) -> str:
    t = str(n.type or "").lower()
    if "overdue" in t or "reminder" in t:
        return "Send reminder to client"
    if "risk" in t:
        return "Review client risk profile"
    if "payment" in t:
        return "Verify payment details"
    if "workflow" in t and "fail" in t:
        return "Check workflow logs and retry"
    if "revenue" in t or "cashflow" in t:
        return "Review cash flow forecast"
    return "Review notification details"


def _notification_dict(n: Notification, *, ai_summary: str = "") -> dict:
    return {
        "id": str(n.id),
        "user_id": str(n.user_id),
        "type": n.type,
        "title": n.title,
        "message": n.message,
        "read": n.read,
        "data": n.data or {},
        "created_at": n.created_at.isoformat() if n.created_at else None,
        # Extended fields (present when model supports them)
        "priority": getattr(n, "priority", "info"),
        "category": getattr(n, "category", None) or _smart_category(n),
        "icon": getattr(n, "icon", None),
        "action_url": getattr(n, "action_url", None),
        "ai_generated": getattr(n, "ai_generated", False),
        "importance_score": getattr(n, "importance_score", None) or _importance_score(n),
        "related_entity_type": getattr(n, "related_entity_type", None),
        "related_entity_id": str(getattr(n, "related_entity_id", "") or ""),
        "archived": getattr(n, "archived", False),
        "read_at": getattr(n, "read_at", None),
        "smart_group_id": getattr(n, "smart_group_id", None),
        # AI enhancements
        "ai_summary": ai_summary or n.message[:120] if n.message else "",
        "next_best_action": _next_best_action(n),
        "smart_category": _smart_category(n),
        "related_entities": [],  # populated per-request in detail endpoints
    }


# ---------------------------------------------------------------------------
# GET /  — Smart Notification Center
# ---------------------------------------------------------------------------


@router.get("/")
async def list_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    # Filters
    unread_only: bool = Query(False),
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None, max_length=200),
    ai_only: bool = Query(False),
    priority: Optional[str] = Query(None, regex="^(critical|high|medium|low|info)$"),
    archived: bool = Query(False),
    # Sort
    sort_by: str = Query("newest", regex="^(newest|priority|ai_relevance)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    offset = (page - 1) * page_size

    stmt = select(Notification).where(Notification.user_id == current_user.id)

    if unread_only:
        stmt = stmt.where(Notification.read.is_(False))
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            or_(Notification.title.ilike(pattern), Notification.message.ilike(pattern))
        )
    if ai_only and hasattr(Notification, "ai_generated"):
        stmt = stmt.where(Notification.ai_generated.is_(True))
    if priority and hasattr(Notification, "priority"):
        stmt = stmt.where(Notification.priority == priority)
    if category and hasattr(Notification, "category"):
        stmt = stmt.where(Notification.category == category)
    if hasattr(Notification, "archived"):
        stmt = stmt.where(Notification.archived.is_(archived))

    # Count before pagination
    count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = int(count_result.scalar_one() or 0)

    # Sort
    if sort_by == "newest":
        stmt = stmt.order_by(desc(Notification.created_at))
    elif sort_by == "priority" and hasattr(Notification, "priority"):
        stmt = stmt.order_by(Notification.priority)
    else:
        stmt = stmt.order_by(desc(Notification.created_at))

    stmt = stmt.offset(offset).limit(page_size)
    result = await db.execute(stmt)
    notifications = result.scalars().all()

    items = [_notification_dict(n) for n in notifications]

    # AI relevance re-sort (post-query)
    if sort_by == "ai_relevance":
        items.sort(key=lambda x: x["importance_score"], reverse=True)

    # Smart grouping — aggregate similar notifications
    groups: dict[str, list[dict]] = {}
    for item in items:
        key = item["smart_category"]
        groups.setdefault(key, []).append(item)

    smart_groups = [
        {
            "category": cat,
            "count": len(group_items),
            "label": _group_label(cat, len(group_items)),
            "items": group_items[:3],  # preview first 3 in each group
            "has_more": len(group_items) > 3,
        }
        for cat, group_items in sorted(groups.items(), key=lambda x: len(x[1]), reverse=True)
    ]

    # Unread counts by category
    unread_by_cat: dict[str, int] = {}
    for item in items:
        if not item["read"]:
            cat = item["smart_category"]
            unread_by_cat[cat] = unread_by_cat.get(cat, 0) + 1

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": -(-total // page_size),
        "unread_count": sum(1 for n in notifications if not n.read),
        "critical_count": sum(1 for i in items if i["priority"] == "critical"),
        "smart_groups": smart_groups,
        "unread_by_category": unread_by_cat,
    }


def _group_label(category: str, count: int) -> str:
    labels = {
        "invoice": f"{count} invoice update{'s' if count != 1 else ''}",
        "payment": f"{count} payment event{'s' if count != 1 else ''}",
        "reminder": f"{count} reminder{'s' if count != 1 else ''}",
        "workflow": f"{count} workflow event{'s' if count != 1 else ''}",
        "risk": f"{count} risk alert{'s' if count != 1 else ''}",
        "ai_insight": f"{count} AI insight{'s' if count != 1 else ''}",
        "analytics": f"{count} analytics update{'s' if count != 1 else ''}",
        "system": f"{count} system notification{'s' if count != 1 else ''}",
        "team": f"{count} team update{'s' if count != 1 else ''}",
    }
    return labels.get(category, f"{count} notification{'s' if count != 1 else ''}")


# ---------------------------------------------------------------------------
# GET /unread-count  — Real-time unread badge
# ---------------------------------------------------------------------------


@router.get("/unread-count")
async def unread_count(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    base = select(Notification).where(
        Notification.user_id == current_user.id,
        Notification.read.is_(False),
    )
    if hasattr(Notification, "archived"):
        base = base.where(Notification.archived.is_(False))

    total_stmt = select(func.count()).select_from(base.subquery())
    total_unread = int((await db.execute(total_stmt)).scalar_one() or 0)

    # Critical count
    critical_count = 0
    ai_urgency_count = 0
    if hasattr(Notification, "priority"):
        crit_stmt = select(func.count()).select_from(
            base.where(Notification.priority == "critical").subquery()
        )
        critical_count = int((await db.execute(crit_stmt)).scalar_one() or 0)
    if hasattr(Notification, "ai_generated"):
        ai_stmt = select(func.count()).select_from(
            base.where(Notification.ai_generated.is_(True)).subquery()
        )
        ai_urgency_count = int((await db.execute(ai_stmt)).scalar_one() or 0)

    # Per-category breakdown
    all_unread_stmt = (
        select(Notification)
        .where(Notification.user_id == current_user.id, Notification.read.is_(False))
        .limit(200)
    )
    unread_rows = (await db.execute(all_unread_stmt)).scalars().all()
    by_category: dict[str, int] = {}
    for n in unread_rows:
        cat = _smart_category(n)
        by_category[cat] = by_category.get(cat, 0) + 1

    return {
        "total_unread": total_unread,
        "critical_count": critical_count,
        "ai_urgency_count": ai_urgency_count,
        "by_category": by_category,
        "refreshed_at": _utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# POST /  — Create notification with AI enrichment
# ---------------------------------------------------------------------------


@router.post("/", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_notification(
    payload: NotificationCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    notif = Notification(
        user_id=payload.user_id or current_user.id,
        type=payload.type,
        title=payload.title,
        message=payload.message,
        read=False,
        data=payload.data or {},
        created_at=_utcnow(),
    )

    # Extended fields
    for field, value in {
        "priority": getattr(payload, "priority", "info"),
        "category": getattr(payload, "category", None) or _smart_category(notif),
        "icon": getattr(payload, "icon", None),
        "action_url": getattr(payload, "action_url", None),
        "ai_generated": getattr(payload, "ai_generated", False),
        "related_entity_type": getattr(payload, "related_entity_type", None),
        "related_entity_id": getattr(payload, "related_entity_id", None),
    }.items():
        if hasattr(notif, field):
            setattr(notif, field, value)

    if hasattr(notif, "importance_score"):
        notif.importance_score = _importance_score(notif)

    db.add(notif)
    await db.flush()
    await db.commit()
    await db.refresh(notif)

    result = _notification_dict(notif)

    # Real-time broadcast
    await ws_manager.broadcast_to_team(
        str(current_user.team_id),
        {
            "event": "NEW_NOTIFICATION",
            "notification": result,
        },
    )

    # AI enrichment in background for AI-generated notifications
    if getattr(payload, "ai_generated", False):
        background_tasks.add_task(
            _ai_enrich_notification_bg,
            notification_id=notif.id,
            team_id=current_user.team_id,
        )

    return result


async def _ai_enrich_notification_bg(notification_id: UUID, team_id: UUID) -> None:
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Notification).where(Notification.id == notification_id))
        notif = result.scalar_one_or_none()
        if not notif:
            return

        enriched = await ai_service.enrich_notification(
            title=notif.title,
            message=notif.message,
            notification_type=str(notif.type),
        )

        if hasattr(notif, "importance_score") and enriched.get("importance_score"):
            notif.importance_score = enriched["importance_score"]
        if hasattr(notif, "smart_group_id") and enriched.get("group_id"):
            notif.smart_group_id = enriched["group_id"]

        await db.commit()


# ---------------------------------------------------------------------------
# PUT /{id}/read  — Mark single notification as read
# ---------------------------------------------------------------------------


@router.put("/{notification_id}/read", response_model=dict)
async def mark_read(
    notification_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = select(Notification).where(
        Notification.id == notification_id,
        Notification.user_id == current_user.id,
    )
    result = await db.execute(stmt)
    notif = result.scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found.")

    notif.read = True
    if hasattr(notif, "read_at"):
        notif.read_at = _utcnow()

    await db.commit()
    await db.refresh(notif)

    # Sync badge count via WebSocket
    unread_stmt = select(func.count(Notification.id)).where(
        Notification.user_id == current_user.id,
        Notification.read.is_(False),
    )
    new_count = int((await db.execute(unread_stmt)).scalar_one() or 0)
    await ws_manager.send_to_user(
        str(current_user.id),
        {"event": "BADGE_SYNC", "unread_count": new_count},
    )

    return _notification_dict(notif)


# ---------------------------------------------------------------------------
# PUT /read-all  — Mark all as read (optionally per category)
# ---------------------------------------------------------------------------


@router.put("/read-all", response_model=dict)
async def mark_all_read(
    category: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = (
        update(Notification)
        .where(
            Notification.user_id == current_user.id,
            Notification.read.is_(False),
        )
        .values(read=True)
    )
    if category and hasattr(Notification, "category"):
        stmt = (
            update(Notification)
            .where(
                Notification.user_id == current_user.id,
                Notification.read.is_(False),
                Notification.category == category,
            )
            .values(read=True)
        )
    if hasattr(Notification, "read_at"):
        stmt = stmt.values(read=True, read_at=_utcnow())

    await db.execute(stmt)
    await db.commit()

    await ws_manager.send_to_user(
        str(current_user.id),
        {"event": "BADGE_SYNC", "unread_count": 0},
    )

    return {
        "status": "success",
        "message": f"All {'[' + category + '] ' if category else ''}notifications marked as read.",
        "category": category,
    }


# ---------------------------------------------------------------------------
# PUT /bulk-update  — Bulk operations
# ---------------------------------------------------------------------------


@router.put("/bulk-update", response_model=dict)
async def bulk_update_notifications(
    payload: NotificationBulkUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    if not payload.notification_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="notification_ids cannot be empty.",
        )

    ids = payload.notification_ids
    action = payload.action  # "read" | "archive" | "delete" | "priority_change" | "categorize"

    base_where = [
        Notification.id.in_(ids),
        Notification.user_id == current_user.id,
    ]

    if action == "read":
        values: dict = {"read": True}
        if hasattr(Notification, "read_at"):
            values["read_at"] = _utcnow()
        await db.execute(update(Notification).where(*base_where).values(**values))

    elif action == "archive" and hasattr(Notification, "archived"):
        await db.execute(update(Notification).where(*base_where).values(archived=True))

    elif action == "delete":
        # Soft delete via archived flag if supported, else hard delete
        if hasattr(Notification, "archived"):
            await db.execute(update(Notification).where(*base_where).values(archived=True))
        else:
            from sqlalchemy import delete as sql_delete
            await db.execute(sql_delete(Notification).where(*base_where))

    elif action == "priority_change" and hasattr(Notification, "priority"):
        new_priority = getattr(payload, "priority", "medium")
        await db.execute(update(Notification).where(*base_where).values(priority=new_priority))

    elif action == "categorize" and hasattr(Notification, "category"):
        new_category = getattr(payload, "category", "system")
        await db.execute(update(Notification).where(*base_where).values(category=new_category))

    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported bulk action '{action}'.",
        )

    await db.commit()

    # Sync badge
    unread_stmt = select(func.count(Notification.id)).where(
        Notification.user_id == current_user.id, Notification.read.is_(False)
    )
    new_count = int((await db.execute(unread_stmt)).scalar_one() or 0)
    await ws_manager.send_to_user(
        str(current_user.id),
        {"event": "BADGE_SYNC", "unread_count": new_count},
    )

    return {
        "status": "success",
        "action": action,
        "affected_count": len(ids),
        "unread_count": new_count,
    }


# ---------------------------------------------------------------------------
# DELETE /{id}  — Soft delete notification
# ---------------------------------------------------------------------------


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification(
    notification_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    stmt = select(Notification).where(
        Notification.id == notification_id,
        Notification.user_id == current_user.id,
    )
    result = await db.execute(stmt)
    notif = result.scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found.")

    if hasattr(notif, "archived"):
        notif.archived = True
        await db.commit()
    else:
        await db.delete(notif)
        await db.commit()


# ---------------------------------------------------------------------------
# GET /ws/info  — WebSocket connection info
# ---------------------------------------------------------------------------


@router.get("/ws/info")
async def websocket_info(
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Returns WebSocket endpoint URL, available channels, and all supported
    real-time event types. Use this to initialise the live dashboard client.
    """
    # Build the public WS URL from Replit domain env var
    domain = os.getenv("REPLIT_DEV_DOMAIN", "localhost")
    ws_url = f"wss://{domain}/api/ws/{current_user.team_id}"

    return {
        "ws_url": ws_url,
        "team_id": str(current_user.team_id),
        "user_id": str(current_user.id),
        "channels": WS_CHANNELS,
        "supported_events": WS_EVENTS,
        "connection_guide": {
            "connect": f"Connect to {ws_url}",
            "subscribe": "Send JSON: {\"action\": \"subscribe\", \"channels\": [\"notifications\", \"kpi_updates\"]}",
            "heartbeat": "Send ping every 30 seconds to keep alive",
            "auth": "Pass Bearer token in Sec-WebSocket-Protocol header",
        },
        "event_descriptions": {
            "NEW_NOTIFICATION": "A new notification was created for this team",
            "PAYMENT_RECEIVED": "A payment was recorded on an invoice",
            "INVOICE_OVERDUE": "An invoice has passed its due date",
            "AI_INSIGHT_GENERATED": "AI generated a new business insight card",
            "WORKFLOW_COMPLETED": "A workflow finished executing successfully",
            "WORKFLOW_FAILED": "A workflow failed after retries",
            "HIGH_RISK_CLIENT": "A client's risk score crossed the high-risk threshold",
            "REVENUE_DROP_ALERT": "Revenue dropped significantly vs previous period",
            "REMINDER_SENT": "A payment reminder was delivered to a client",
            "DASHBOARD_REFRESH": "KPIs and analytics data have been refreshed",
            "OVERDUE_ESCALATED": "An overdue invoice was escalated to a higher severity tone",
            "CLIENT_RISK_CHANGED": "AI recalculated a client's risk score",
            "KPI_REFRESHED": "Real-time KPIs have been recalculated",
            "AI_RECOMMENDATION_GENERATED": "AI produced a new action recommendation",
        },
    }


# ---------------------------------------------------------------------------
# GET /activity-timeline  — Live AI activity feed for the dashboard
# ---------------------------------------------------------------------------


@router.get("/activity-timeline")
async def activity_timeline(
    limit: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Returns a chronological activity stream combining notifications, workflow
    runs, and AI insights — powers the animated live dashboard timeline.
    """
    # Recent notifications
    notif_stmt = (
        select(Notification)
        .where(Notification.user_id == current_user.id)
        .order_by(desc(Notification.created_at))
        .limit(limit // 3)
    )
    notifs = (await db.execute(notif_stmt)).scalars().all()

    # Recent activities
    act_stmt = (
        select(Activity)
        .where(Activity.team_id == current_user.team_id)
        .order_by(desc(Activity.created_at))
        .limit(limit // 3)
    )
    acts = (await db.execute(act_stmt)).scalars().all()

    # Recent AI insights
    insight_stmt = (
        select(BusinessInsight)
        .where(
            BusinessInsight.team_id == current_user.team_id,
            BusinessInsight.ai_generated.is_(True),
        )
        .order_by(desc(BusinessInsight.id))
        .limit(limit // 3)
    )
    insights = (await db.execute(insight_stmt)).scalars().all()

    # Merge and sort by timestamp
    events: list[dict] = []

    for n in notifs:
        events.append({
            "id": str(n.id),
            "source": "notification",
            "icon": getattr(n, "icon", "bell"),
            "title": n.title,
            "description": n.message[:120] if n.message else "",
            "category": _smart_category(n),
            "priority": getattr(n, "priority", "info"),
            "importance_score": _importance_score(n),
            "action_url": getattr(n, "action_url", None),
            "timestamp": n.created_at.isoformat() if n.created_at else "",
            "ai_generated": getattr(n, "ai_generated", False),
        })

    for a in acts:
        events.append({
            "id": str(a.id),
            "source": "activity",
            "icon": "activity",
            "title": a.action_type.replace("_", " ").title() if a.action_type else "Activity",
            "description": a.description or "",
            "category": a.entity_type or "system",
            "priority": "info",
            "importance_score": 30,
            "action_url": None,
            "timestamp": a.created_at.isoformat() if a.created_at else "",
            "ai_generated": False,
        })

    for i in insights:
        events.append({
            "id": str(i.id),
            "source": "ai_insight",
            "icon": "sparkles",
            "title": i.title,
            "description": i.content[:120] if i.content else "",
            "category": i.category or "ai_insight",
            "priority": i.severity or "info",
            "importance_score": 80,
            "action_url": None,
            "timestamp": "",  # BusinessInsight may not have created_at, sort last
            "ai_generated": True,
        })

    events.sort(key=lambda e: e["timestamp"], reverse=True)
    events = events[:limit]

    return {
        "events": events,
        "total": len(events),
        "refreshed_at": _utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# POST /ai/generate-alerts  — Autonomous AI alert generation
# ---------------------------------------------------------------------------


@router.post("/ai/generate-alerts", status_code=status.HTTP_202_ACCEPTED)
async def generate_ai_alerts(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Triggers background AI analysis of revenue, clients, invoices, and cash flow.
    Generates AI-priority notifications for anything that warrants attention.
    """
    background_tasks.add_task(
        _run_ai_alert_engine,
        team_id=current_user.team_id,
        user_id=current_user.id,
    )
    return {
        "status": "running",
        "message": "AI is scanning your business data for alerts. Notifications will appear shortly.",
    }


async def _run_ai_alert_engine(team_id: UUID, user_id: UUID) -> None:
    """
    Autonomous alert engine — checks all major risk signals and creates
    prioritised AI notifications for anything that crosses thresholds.
    """
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        from datetime import date

        today = date.today()

        # --- Revenue drop ---
        from sqlalchemy import extract

        month_start = today.replace(day=1)
        prev_month_start = (month_start.replace(day=1) - __import__("datetime").timedelta(days=1)).replace(day=1)

        curr_rev_stmt = select(func.coalesce(func.sum(Invoice.total), 0)).where(
            Invoice.team_id == team_id,
            Invoice.issue_date >= month_start,
        )
        prev_rev_stmt = select(func.coalesce(func.sum(Invoice.total), 0)).where(
            Invoice.team_id == team_id,
            Invoice.issue_date >= prev_month_start,
            Invoice.issue_date < month_start,
        )
        curr_rev = float((await db.execute(curr_rev_stmt)).scalar_one() or 0)
        prev_rev = float((await db.execute(prev_rev_stmt)).scalar_one() or 0)

        if prev_rev > 0 and curr_rev < prev_rev * 0.8:
            drop_pct = round((prev_rev - curr_rev) / prev_rev * 100, 1)
            await _create_ai_notification(
                db,
                user_id=user_id,
                team_id=team_id,
                notification_type="revenue_drop_alert",
                title=f"Revenue dropped {drop_pct}% this month",
                message=f"Monthly revenue is {drop_pct}% below last month (${curr_rev:,.0f} vs ${prev_rev:,.0f}). "
                        f"AI recommends reviewing overdue invoices and following up with top clients.",
                priority="high",
                category="analytics",
                event="REVENUE_DROP_ALERT",
            )

        # --- High-risk clients ---
        high_risk_stmt = (
            select(Client.id, Client.name, Client.risk_score)
            .where(Client.team_id == team_id, Client.risk_score >= 75, Client.is_active.is_(True))
            .order_by(desc(Client.risk_score))
            .limit(5)
        )
        high_risk_rows = (await db.execute(high_risk_stmt)).all()
        if high_risk_rows:
            names = ", ".join(r[1] for r in high_risk_rows[:3])
            await _create_ai_notification(
                db,
                user_id=user_id,
                team_id=team_id,
                notification_type="high_risk_client",
                title=f"{len(high_risk_rows)} high-risk client{'s' if len(high_risk_rows) > 1 else ''} detected",
                message=f"Clients {names} have risk scores above 75. "
                        f"AI recommends sending reminders and reviewing payment terms.",
                priority="critical" if len(high_risk_rows) >= 3 else "high",
                category="risk",
                event="HIGH_RISK_CLIENT",
            )

        # --- Overdue invoice spike ---
        overdue_stmt = select(
            func.count(Invoice.id).label("count"),
            func.coalesce(func.sum(Invoice.balance_due), 0).label("amount"),
        ).where(Invoice.team_id == team_id, Invoice.status == InvoiceStatus.overdue)
        ov = (await db.execute(overdue_stmt)).mappings().one()
        overdue_count = int(ov["count"] or 0)
        overdue_amount = float(ov["amount"] or 0)

        if overdue_count >= 5:
            await _create_ai_notification(
                db,
                user_id=user_id,
                team_id=team_id,
                notification_type="invoice_overdue",
                title=f"{overdue_count} invoices overdue — ${overdue_amount:,.0f} at risk",
                message=f"You have {overdue_count} overdue invoices totalling ${overdue_amount:,.2f}. "
                        f"AI recommends triggering the overdue recovery workflow.",
                priority="high" if overdue_amount > 10000 else "medium",
                category="invoice",
                event="INVOICE_OVERDUE",
            )

        # --- Cash flow warning (outstanding > 2x this month revenue) ---
        outstanding_stmt = select(func.coalesce(func.sum(Invoice.balance_due), 0)).where(
            Invoice.team_id == team_id,
            Invoice.status.in_([InvoiceStatus.sent, InvoiceStatus.overdue]),
        )
        outstanding = float((await db.execute(outstanding_stmt)).scalar_one() or 0)
        if curr_rev > 0 and outstanding > curr_rev * 2:
            await _create_ai_notification(
                db,
                user_id=user_id,
                team_id=team_id,
                notification_type="cashflow_warning",
                title=f"Cash flow risk: ${outstanding:,.0f} outstanding",
                message=f"Outstanding balance (${outstanding:,.2f}) is more than 2× this month's revenue "
                        f"(${curr_rev:,.2f}). Predicted cash flow risk if not collected soon.",
                priority="critical",
                category="analytics",
                event="REVENUE_DROP_ALERT",
            )

        await db.commit()


async def _create_ai_notification(
    db: AsyncSession,
    *,
    user_id: UUID,
    team_id: UUID,
    notification_type: str,
    title: str,
    message: str,
    priority: str,
    category: str,
    event: str,
) -> None:
    notif = Notification(
        user_id=user_id,
        type=notification_type,
        title=title,
        message=message,
        read=False,
        data={"ai_generated": True},
        created_at=_utcnow(),
    )
    for field, value in {
        "priority": priority,
        "category": category,
        "ai_generated": True,
        "importance_score": {"critical": 95, "high": 80, "medium": 60}.get(priority, 40),
    }.items():
        if hasattr(notif, field):
            setattr(notif, field, value)

    db.add(notif)
    await db.flush()

    await ws_manager.broadcast_to_team(
        str(team_id),
        {
            "event": event,
            "notification": {
                "id": str(notif.id),
                "title": title,
                "message": message[:120],
                "priority": priority,
                "category": category,
                "ai_generated": True,
            },
        },
    )
