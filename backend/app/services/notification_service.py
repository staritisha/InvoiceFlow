"""
app/services/notification_service.py

Real-time, AI-driven notification engine for InvoiceFlow.
Powers in-app alerts, email delivery, WebSocket broadcasting, activity
timelines, AI summaries, business health warnings, and team collaboration.
"""

from __future__ import annotations

import json
import logging
import smtplib
import uuid
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class NotificationType(str, Enum):
    INVOICE_CREATED = "invoice_created"
    INVOICE_PAID = "invoice_paid"
    INVOICE_OVERDUE = "invoice_overdue"
    PAYMENT_RECEIVED = "payment_received"
    AI_INSIGHT = "ai_insight"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_FAILED = "workflow_failed"
    REPORT_GENERATED = "report_generated"
    REMINDER_SENT = "reminder_sent"
    CLIENT_RISK_ALERT = "client_risk_alert"
    WEEKLY_SUMMARY = "weekly_summary"
    BUSINESS_HEALTH_WARNING = "business_health_warning"
    REVENUE_MILESTONE = "revenue_milestone"
    TEAM_MENTION = "team_mention"
    WORKFLOW_ASSIGNED = "workflow_assigned"
    ADMIN_ANNOUNCEMENT = "admin_announcement"


class NotificationPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class NotificationChannel(str, Enum):
    IN_APP = "in_app"
    EMAIL = "email"
    WEBSOCKET = "websocket"
    PUSH = "push"
    WHATSAPP = "whatsapp"   # Future
    SLACK = "slack"         # Future
    DISCORD = "discord"     # Future


# ---------------------------------------------------------------------------
# Adapter helpers
# ---------------------------------------------------------------------------

def _get_db():
    try:
        from app import db
        return db
    except ImportError:
        raise RuntimeError("Could not import 'db' from 'app'. Ensure Flask app context is active.")


def _get_ai_client():
    try:
        import openai, os
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            logger.warning("OPENAI_API_KEY not set — AI features degraded")
            return None
        openai.api_key = key
        return openai
    except ImportError:
        logger.warning("openai package not installed — AI features disabled")
        return None


def _get_socketio():
    try:
        from app import socketio
        return socketio
    except ImportError:
        return None


def _smtp_config() -> dict:
    import os
    return {
        "host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": os.getenv("SMTP_USER", ""),
        "password": os.getenv("SMTP_PASSWORD", ""),
        "from_name": os.getenv("SMTP_FROM_NAME", "InvoiceFlow"),
        "from_email": os.getenv("SMTP_FROM_EMAIL", ""),
    }


def _now() -> datetime:
    return datetime.utcnow()


def _new_id() -> str:
    return str(uuid.uuid4())


def _notification_model():
    try:
        from app.models import Notification
        return Notification
    except ImportError:
        raise RuntimeError("Notification model not found — import it from app.models.")


def _preference_model():
    try:
        from app.models import NotificationPreference
        return NotificationPreference
    except ImportError:
        return None


# ===========================================================================
# 1 & 2. IN-APP NOTIFICATION ENGINE + TYPES
# ===========================================================================

def create_notification(
    user_id: int,
    notification_type: str,
    message: str,
    *,
    title: str = "",
    entity_type: str = "invoice",
    entity_id: str | None = None,
    metadata: dict | None = None,
    channels: list[str] | None = None,
    priority: str | None = None,
    team_id: int | None = None,
) -> dict:
    """
    Create and persist an in-app notification, then fan it out to all
    requested delivery channels (in-app, email, websocket, push).

    Parameters
    ----------
    user_id           : Recipient user ID.
    notification_type : One of NotificationType values.
    message           : Notification body text.
    title             : Short headline (auto-generated if blank).
    entity_type       : 'invoice', 'payment', 'workflow', etc.
    entity_id         : ID of the related entity.
    metadata          : Arbitrary extra JSON data.
    channels          : Delivery channels (defaults to ['in_app', 'websocket']).
    priority          : Override auto-calculated priority.
    team_id           : Optional team scope for collaboration notifications.

    Returns
    -------
    Serialised notification dict.
    """
    if notification_type not in [t.value for t in NotificationType]:
        raise ValueError(f"Unknown notification type: {notification_type!r}")

    if not priority:
        priority = calculate_priority(notification_type, metadata or {})

    if not title:
        title = _default_title(notification_type)

    if channels is None:
        channels = [NotificationChannel.IN_APP, NotificationChannel.WEBSOCKET]

    db = _get_db()
    Notification = _notification_model()

    note = Notification(
        id=_new_id(),
        user_id=user_id,
        team_id=team_id,
        notification_type=notification_type,
        title=title,
        message=message,
        entity_type=entity_type,
        entity_id=entity_id,
        priority=priority,
        metadata=json.dumps(metadata or {}),
        channels=json.dumps(channels),
        is_read=False,
        is_archived=False,
        created_at=_now(),
    )
    db.session.add(note)
    db.session.commit()

    serialised = _serialize_notification(note)

    # Fan out to all requested channels
    send_notification(serialised, channels=channels)

    logger.info(
        "Notification created: type=%s user=%s priority=%s",
        notification_type, user_id, priority,
    )
    return serialised


def send_notification(notification: dict, *, channels: list[str] | None = None) -> dict:
    """
    Deliver a notification to the specified channels.

    Returns a dict mapping channel → delivery result.
    """
    channels = channels or notification.get("channels") or [NotificationChannel.IN_APP]
    results: dict[str, Any] = {}

    for channel in channels:
        if channel == NotificationChannel.WEBSOCKET:
            results[channel] = broadcast_notification(notification)
        elif channel == NotificationChannel.EMAIL:
            recipient_email = notification.get("metadata", {}).get("email")
            if recipient_email:
                results[channel] = send_email_notification(
                    to_email=recipient_email,
                    subject=notification.get("title", "InvoiceFlow Notification"),
                    notification=notification,
                )
            else:
                results[channel] = {"ok": False, "error": "No email in metadata"}
        elif channel == NotificationChannel.PUSH:
            results[channel] = send_push_notification(notification)
        elif channel == NotificationChannel.IN_APP:
            results[channel] = {"ok": True, "delivered": "in_app"}
        else:
            results[channel] = {"ok": False, "error": f"Channel {channel!r} not yet supported"}

    return results


def _default_title(notification_type: str) -> str:
    titles = {
        NotificationType.INVOICE_CREATED: "New Invoice Created",
        NotificationType.INVOICE_PAID: "Invoice Paid",
        NotificationType.INVOICE_OVERDUE: "Invoice Overdue",
        NotificationType.PAYMENT_RECEIVED: "Payment Received",
        NotificationType.AI_INSIGHT: "AI Insight",
        NotificationType.WORKFLOW_COMPLETED: "Workflow Completed",
        NotificationType.WORKFLOW_FAILED: "Workflow Failed",
        NotificationType.REPORT_GENERATED: "Report Ready",
        NotificationType.REMINDER_SENT: "Reminder Sent",
        NotificationType.CLIENT_RISK_ALERT: "Client Risk Alert",
        NotificationType.WEEKLY_SUMMARY: "Weekly Summary",
        NotificationType.BUSINESS_HEALTH_WARNING: "Business Health Warning",
        NotificationType.REVENUE_MILESTONE: "Revenue Milestone",
        NotificationType.TEAM_MENTION: "You Were Mentioned",
        NotificationType.WORKFLOW_ASSIGNED: "Workflow Assigned to You",
        NotificationType.ADMIN_ANNOUNCEMENT: "Announcement",
    }
    return titles.get(notification_type, "Notification")


def _serialize_notification(note) -> dict:
    metadata = note.metadata
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}

    channels = note.channels
    if isinstance(channels, str):
        try:
            channels = json.loads(channels)
        except Exception:
            channels = []

    return {
        "id": note.id,
        "user_id": note.user_id,
        "team_id": getattr(note, "team_id", None),
        "notification_type": note.notification_type,
        "title": getattr(note, "title", ""),
        "message": note.message,
        "entity_type": getattr(note, "entity_type", ""),
        "entity_id": getattr(note, "entity_id", None),
        "priority": getattr(note, "priority", NotificationPriority.MEDIUM),
        "metadata": metadata,
        "channels": channels,
        "is_read": note.is_read,
        "is_archived": getattr(note, "is_archived", False),
        "created_at": note.created_at.isoformat() if note.created_at else None,
    }


# ===========================================================================
# 3. SMART NOTIFICATION CENTER
# ===========================================================================

def get_notification_center(
    user_id: int,
    *,
    include_ai_summary: bool = True,
    page: int = 1,
    per_page: int = 30,
    category: str | None = None,
    unread_only: bool = False,
) -> dict:
    """
    Return a rich notification center payload for the dashboard.

    Includes:
    - Grouped timeline (Today / Yesterday / This Week / Older)
    - Priority-sorted items
    - Unread count per category
    - Optional AI-generated summary
    - Category filter support

    Parameters
    ----------
    user_id          : Recipient user.
    include_ai_summary: Whether to generate an AI summary of recent alerts.
    page             : Pagination page number.
    per_page         : Items per page.
    category         : Filter to a specific NotificationType value.
    unread_only      : Return only unread notifications.
    """
    Notification = _notification_model()

    q = Notification.query.filter_by(user_id=user_id, is_archived=False)
    if unread_only:
        q = q.filter_by(is_read=False)
    if category:
        q = q.filter_by(notification_type=category)

    q = q.order_by(Notification.created_at.desc())
    paginated = q.paginate(page=page, per_page=per_page, error_out=False)

    items = [_serialize_notification(n) for n in paginated.items]
    grouped = group_notifications(items)

    unread_total = Notification.query.filter_by(user_id=user_id, is_read=False, is_archived=False).count()
    unread_by_type = _unread_counts_by_type(user_id)

    ai_summary = None
    if include_ai_summary and items:
        ai_summary = generate_ai_summary(items[:20])

    return {
        "groups": grouped,
        "total": paginated.total,
        "unread_total": unread_total,
        "unread_by_type": unread_by_type,
        "page": page,
        "per_page": per_page,
        "pages": paginated.pages,
        "ai_summary": ai_summary,
    }


def _unread_counts_by_type(user_id: int) -> dict:
    Notification = _notification_model()
    unread = Notification.query.filter_by(user_id=user_id, is_read=False, is_archived=False).all()
    counts: dict[str, int] = {}
    for n in unread:
        counts[n.notification_type] = counts.get(n.notification_type, 0) + 1
    return counts


# ===========================================================================
# 4. READ / UNREAD MANAGEMENT
# ===========================================================================

def mark_as_read(notification_id: str, *, user_id: int) -> dict:
    """Mark a single notification as read."""
    db = _get_db()
    Notification = _notification_model()

    note = Notification.query.filter_by(id=notification_id, user_id=user_id).first()
    if not note:
        raise ValueError(f"Notification {notification_id!r} not found for user {user_id}")

    note.is_read = True
    note.read_at = _now()
    db.session.commit()
    return _serialize_notification(note)


def mark_all_as_read(user_id: int, *, category: str | None = None) -> int:
    """
    Mark all (or category-filtered) notifications as read.

    Returns the number of notifications updated.
    """
    db = _get_db()
    Notification = _notification_model()

    q = Notification.query.filter_by(user_id=user_id, is_read=False)
    if category:
        q = q.filter_by(notification_type=category)

    count = q.count()
    q.update({"is_read": True, "read_at": _now()}, synchronize_session=False)
    db.session.commit()

    logger.info("Marked %d notifications as read for user %s", count, user_id)
    return count


def toggle_read_status(notification_id: str, *, user_id: int) -> dict:
    """Toggle the read/unread state of a notification."""
    db = _get_db()
    Notification = _notification_model()

    note = Notification.query.filter_by(id=notification_id, user_id=user_id).first()
    if not note:
        raise ValueError(f"Notification {notification_id!r} not found")

    note.is_read = not note.is_read
    if note.is_read:
        note.read_at = _now()
    db.session.commit()
    return _serialize_notification(note)


# ===========================================================================
# 5. BULK NOTIFICATION ACTIONS
# ===========================================================================

def bulk_update_notifications(
    user_id: int,
    action: str,
    *,
    notification_ids: list[str] | None = None,
    category: str | None = None,
) -> dict:
    """
    Perform a bulk operation on notifications.

    Actions
    -------
    mark_read    : Mark all (or selected) as read.
    archive      : Move to archive.
    delete       : Permanently delete.
    mute         : Mute a category (requires `category`).
    prioritize   : Promote priority to 'high' for selected.

    Parameters
    ----------
    user_id          : Recipient user.
    action           : Bulk action string.
    notification_ids : Explicit list of IDs, or None to target all.
    category         : Target a notification type category.
    """
    db = _get_db()
    Notification = _notification_model()

    q = Notification.query.filter_by(user_id=user_id)
    if notification_ids:
        q = q.filter(Notification.id.in_(notification_ids))
    if category:
        q = q.filter_by(notification_type=category)

    count = q.count()

    if action == "mark_read":
        q.update({"is_read": True, "read_at": _now()}, synchronize_session=False)
    elif action == "archive":
        q.update({"is_archived": True}, synchronize_session=False)
    elif action == "delete":
        q.delete(synchronize_session=False)
    elif action == "mute":
        if not category:
            raise ValueError("'mute' requires a category")
        update_notification_preferences(
            user_id,
            {f"mute_{category}": True},
        )
    elif action == "prioritize":
        q.update({"priority": NotificationPriority.HIGH}, synchronize_session=False)
    else:
        raise ValueError(f"Unknown bulk action: {action!r}")

    db.session.commit()
    logger.info("Bulk action '%s' applied to %d notifications for user %s", action, count, user_id)
    return {"action": action, "affected": count}


# ===========================================================================
# 6. REAL-TIME WEBSOCKET BROADCASTING
# ===========================================================================

def broadcast_notification(notification: dict) -> dict:
    """
    Push a notification instantly over WebSocket.

    Emits:
    - 'notification' event (global feed)
    - A type-specific event (e.g. 'invoice_paid', 'ai_insight')

    Falls back gracefully when SocketIO is not configured.
    """
    sio = _get_socketio()
    if sio is None:
        logger.debug("WebSocket broadcast skipped — SocketIO not configured")
        return {"ok": False, "reason": "SocketIO not configured"}

    payload = {
        "id": notification.get("id"),
        "type": notification.get("notification_type"),
        "title": notification.get("title"),
        "message": notification.get("message"),
        "priority": notification.get("priority"),
        "entity_id": notification.get("entity_id"),
        "entity_type": notification.get("entity_type"),
        "timestamp": notification.get("created_at") or _now().isoformat(),
    }

    try:
        # Broadcast to the specific user's room
        user_room = f"user_{notification.get('user_id')}"
        sio.emit("notification", payload, room=user_room)

        # Also emit a typed event for fine-grained client listeners
        event_type = notification.get("notification_type", "notification")
        sio.emit(event_type, payload, room=user_room)

        # Team broadcasts
        if notification.get("team_id"):
            team_room = f"team_{notification['team_id']}"
            sio.emit("team_notification", payload, room=team_room)

        logger.debug("Broadcast: %s → user=%s", event_type, notification.get("user_id"))
        return {"ok": True, "event": event_type}
    except Exception as exc:
        logger.warning("WebSocket broadcast failed: %s", exc)
        return {"ok": False, "error": str(exc)}


# ===========================================================================
# 7. EMAIL NOTIFICATION ENGINE
# ===========================================================================

def send_email_notification(
    to_email: str,
    subject: str,
    notification: dict,
    *,
    template: str = "default",
) -> dict:
    """
    Send a branded HTML email notification via SMTP.

    Supports:
    - Responsive HTML with gradient header
    - AI badge for AI insight notifications
    - CTA button with configurable label and URL
    - Plaintext fallback

    Environment variables required:
        SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
        SMTP_FROM_EMAIL, SMTP_FROM_NAME
    """
    cfg = _smtp_config()
    if not cfg["user"] or not cfg["from_email"]:
        logger.warning("SMTP not configured — email skipped for %s", to_email)
        return {"ok": False, "error": "SMTP not configured"}

    html_body = _render_email_html(notification, template=template)
    text_body = _render_email_text(notification)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{cfg['from_name']} <{cfg['from_email']}>"
    msg["To"] = to_email

    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(cfg["user"], cfg["password"])
            server.sendmail(cfg["from_email"], to_email, msg.as_string())

        logger.info("Email sent → %s | %s", to_email, subject)
        return {"ok": True, "to": to_email, "subject": subject}
    except Exception as exc:
        logger.error("Email send failed for %s: %s", to_email, exc)
        return {"ok": False, "error": str(exc)}


def _render_email_html(notification: dict, *, template: str = "default") -> str:
    """Render a branded, responsive HTML email body."""
    title = notification.get("title", "InvoiceFlow Notification")
    message = notification.get("message", "")
    priority = notification.get("priority", NotificationPriority.MEDIUM)
    ntype = notification.get("notification_type", "")
    entity_id = notification.get("entity_id", "")

    is_ai = ntype in (NotificationType.AI_INSIGHT, NotificationType.BUSINESS_HEALTH_WARNING)
    ai_badge = '<span style="background:#7C3AED;color:#fff;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:700;margin-left:8px;">AI</span>' if is_ai else ""

    priority_colors = {
        NotificationPriority.CRITICAL: "#DC2626",
        NotificationPriority.HIGH: "#D97706",
        NotificationPriority.MEDIUM: "#2563EB",
        NotificationPriority.LOW: "#059669",
    }
    accent = priority_colors.get(priority, "#2563EB")

    cta_url = notification.get("metadata", {}).get("action_url", "#")
    cta_label = notification.get("metadata", {}).get("action_label", "View Details")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#F3F4F6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#F3F4F6;padding:32px 0;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);">
          <!-- Header -->
          <tr>
            <td style="background:linear-gradient(135deg,#1E40AF 0%,#7C3AED 100%);padding:28px 32px;">
              <p style="margin:0;color:#fff;font-size:22px;font-weight:700;letter-spacing:-0.3px;">
                InvoiceFlow {ai_badge}
              </p>
              <p style="margin:4px 0 0;color:rgba(255,255,255,0.75);font-size:13px;">AI-Powered Invoice Management</p>
            </td>
          </tr>
          <!-- Priority bar -->
          <tr><td style="height:4px;background:{accent};"></td></tr>
          <!-- Body -->
          <tr>
            <td style="padding:32px;">
              <h2 style="margin:0 0 12px;font-size:20px;color:#111827;font-weight:700;">{title}</h2>
              <p style="margin:0 0 24px;font-size:15px;color:#374151;line-height:1.6;">{message}</p>
              <a href="{cta_url}"
                 style="display:inline-block;background:linear-gradient(135deg,#1E40AF,#7C3AED);color:#fff;
                        text-decoration:none;padding:12px 28px;border-radius:8px;font-weight:600;font-size:14px;">
                {cta_label}
              </a>
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="padding:20px 32px;background:#F9FAFB;border-top:1px solid #E5E7EB;">
              <p style="margin:0;font-size:12px;color:#9CA3AF;">
                You're receiving this because you have notifications enabled on InvoiceFlow.<br>
                <a href="#" style="color:#6B7280;">Manage preferences</a>
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _render_email_text(notification: dict) -> str:
    return (
        f"{notification.get('title', 'Notification')}\n\n"
        f"{notification.get('message', '')}\n\n"
        "InvoiceFlow — AI-Powered Invoice Management\n"
    )


# ===========================================================================
# 8. NOTIFICATION PRIORITY SYSTEM
# ===========================================================================

def calculate_priority(
    notification_type: str,
    metadata: dict | None = None,
) -> str:
    """
    Determine notification priority from its type and contextual metadata.

    Priority matrix
    ---------------
    CRITICAL : invoice_overdue (30+ days), business_health_warning, workflow_failed
    HIGH     : client_risk_alert, ai_insight, invoice_overdue (<30 days)
    MEDIUM   : invoice_paid, payment_received, workflow_completed, reminder_sent
    LOW      : revenue_milestone, weekly_summary, report_generated

    Metadata can override with explicit 'overdue_days' or 'health_score' keys.
    """
    metadata = metadata or {}

    critical_types = {
        NotificationType.BUSINESS_HEALTH_WARNING,
        NotificationType.WORKFLOW_FAILED,
    }
    high_types = {
        NotificationType.CLIENT_RISK_ALERT,
        NotificationType.AI_INSIGHT,
        NotificationType.INVOICE_OVERDUE,
    }
    medium_types = {
        NotificationType.INVOICE_PAID,
        NotificationType.PAYMENT_RECEIVED,
        NotificationType.WORKFLOW_COMPLETED,
        NotificationType.REMINDER_SENT,
        NotificationType.TEAM_MENTION,
        NotificationType.WORKFLOW_ASSIGNED,
    }

    # Escalate overdue based on age
    if notification_type == NotificationType.INVOICE_OVERDUE:
        overdue_days = int(metadata.get("overdue_days", 0))
        if overdue_days >= 30:
            return NotificationPriority.CRITICAL
        return NotificationPriority.HIGH

    # Business health — escalate if score dropped significantly
    if notification_type == NotificationType.BUSINESS_HEALTH_WARNING:
        drop = float(metadata.get("health_score_drop", 0))
        if drop >= 15:
            return NotificationPriority.CRITICAL
        return NotificationPriority.HIGH

    if notification_type in critical_types:
        return NotificationPriority.CRITICAL
    if notification_type in high_types:
        return NotificationPriority.HIGH
    if notification_type in medium_types:
        return NotificationPriority.MEDIUM

    return NotificationPriority.LOW


# ===========================================================================
# 9. AI NOTIFICATION SUMMARIES
# ===========================================================================

def generate_ai_summary(notifications: list[dict]) -> str:
    """
    Use AI to generate an executive summary of recent notifications.

    Falls back to a rule-based summary when AI is unavailable.

    Example output
    --------------
    "3 invoices became overdue today. Collection risk increased 14%.
     2 workflows completed successfully. Revenue is on track."
    """
    ai = _get_ai_client()

    overdue_count = sum(
        1 for n in notifications
        if n.get("notification_type") == NotificationType.INVOICE_OVERDUE
    )
    paid_count = sum(
        1 for n in notifications
        if n.get("notification_type") == NotificationType.INVOICE_PAID
    )
    workflow_count = sum(
        1 for n in notifications
        if n.get("notification_type") in (
            NotificationType.WORKFLOW_COMPLETED, NotificationType.WORKFLOW_FAILED
        )
    )

    if not ai:
        parts = []
        if overdue_count:
            parts.append(f"{overdue_count} invoice{'s' if overdue_count != 1 else ''} overdue.")
        if paid_count:
            parts.append(f"{paid_count} payment{'s' if paid_count != 1 else ''} received.")
        if workflow_count:
            parts.append(f"{workflow_count} workflow event{'s' if workflow_count != 1 else ''} recorded.")
        return " ".join(parts) if parts else "All systems normal."

    try:
        brief = [
            {"type": n.get("notification_type"), "message": n.get("message", "")[:100]}
            for n in notifications[:15]
        ]
        prompt = (
            f"Summarise these {len(notifications)} business notifications in 2-3 clear sentences "
            f"for an executive dashboard. Focus on risk, revenue, and action items.\n\n"
            f"Notifications: {json.dumps(brief)}"
        )
        resp = ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        logger.warning("AI summary generation failed: %s", exc)
        return "Recent activity requires your attention."


# ===========================================================================
# 10. NOTIFICATION GROUPING ENGINE
# ===========================================================================

def group_notifications(notifications: list[dict]) -> list[dict]:
    """
    Group notifications into a timeline-friendly structure.

    Groups (in order):
    - Today
    - Yesterday
    - This Week
    - AI Insights (cross-cutting AI notifications)
    - Payments
    - Reports
    - Older

    Each group:
    {
        "label": "Today",
        "items": [...],
        "unread_count": N
    }
    """
    now = _now()
    today = now.date()
    yesterday = (now - timedelta(days=1)).date()
    week_start = (now - timedelta(days=7)).date()

    groups: dict[str, list[dict]] = {
        "Today": [],
        "Yesterday": [],
        "This Week": [],
        "AI Insights": [],
        "Payments": [],
        "Reports": [],
        "Older": [],
    }

    ai_types = {NotificationType.AI_INSIGHT, NotificationType.BUSINESS_HEALTH_WARNING}
    payment_types = {NotificationType.INVOICE_PAID, NotificationType.PAYMENT_RECEIVED}
    report_types = {NotificationType.REPORT_GENERATED, NotificationType.WEEKLY_SUMMARY}

    for note in notifications:
        ntype = note.get("notification_type", "")
        created_raw = note.get("created_at")

        # Parse timestamp
        if created_raw:
            try:
                created_dt = datetime.fromisoformat(created_raw).date()
            except Exception:
                created_dt = today
        else:
            created_dt = today

        # Cross-cutting groups take precedence for AI / payments / reports
        if ntype in ai_types:
            groups["AI Insights"].append(note)
        elif ntype in payment_types:
            groups["Payments"].append(note)
        elif ntype in report_types:
            groups["Reports"].append(note)
        elif created_dt == today:
            groups["Today"].append(note)
        elif created_dt == yesterday:
            groups["Yesterday"].append(note)
        elif created_dt >= week_start:
            groups["This Week"].append(note)
        else:
            groups["Older"].append(note)

    # Return only non-empty groups, with unread counts
    result = []
    for label, items in groups.items():
        if not items:
            continue
        result.append({
            "label": label,
            "items": items,
            "unread_count": sum(1 for n in items if not n.get("is_read")),
        })

    return result


# ===========================================================================
# 11. NOTIFICATION PREFERENCES
# ===========================================================================

def update_notification_preferences(user_id: int, preferences: dict) -> dict:
    """
    Update a user's notification delivery preferences.

    Preference keys (all boolean unless noted):
    - email_alerts       : Receive email notifications.
    - push_alerts        : Receive push notifications.
    - ai_insights        : AI-generated insight notifications.
    - workflow_updates   : Workflow start/complete/fail events.
    - reminders          : Invoice reminder notifications.
    - weekly_reports     : Weekly summary emails.
    - mute_<type>        : Mute a specific NotificationType (bool).

    Returns
    -------
    Updated preferences dict.
    """
    db = _get_db()
    PreferenceModel = _preference_model()

    if PreferenceModel is None:
        logger.warning("NotificationPreference model not found — preferences not persisted")
        return preferences

    pref = PreferenceModel.query.filter_by(user_id=user_id).first()
    if not pref:
        pref = PreferenceModel(id=_new_id(), user_id=user_id, settings="{}", created_at=_now())
        db.session.add(pref)

    existing = {}
    if isinstance(pref.settings, str):
        try:
            existing = json.loads(pref.settings)
        except Exception:
            existing = {}

    existing.update(preferences)
    pref.settings = json.dumps(existing)
    pref.updated_at = _now()
    db.session.commit()

    logger.info("Notification preferences updated for user %s", user_id)
    return existing


def get_notification_preferences(user_id: int) -> dict:
    """Return the current notification preferences for a user."""
    PreferenceModel = _preference_model()
    if PreferenceModel is None:
        return _default_preferences()

    pref = PreferenceModel.query.filter_by(user_id=user_id).first()
    if not pref:
        return _default_preferences()

    try:
        return json.loads(pref.settings) if isinstance(pref.settings, str) else pref.settings or {}
    except Exception:
        return _default_preferences()


def _default_preferences() -> dict:
    return {
        "email_alerts": True,
        "push_alerts": False,
        "ai_insights": True,
        "workflow_updates": True,
        "reminders": True,
        "weekly_reports": True,
    }


# ===========================================================================
# 12. AI SMART RECOMMENDATION ALERTS
# ===========================================================================

def generate_recommendation_notifications(
    user_id: int,
    *,
    context: dict | None = None,
) -> list[dict]:
    """
    Generate and persist AI-powered proactive recommendation notifications.

    Examples
    --------
    - "AI suggests following up with Acme Inc. — overdue 18 days."
    - "Cashflow may dip next week due to 5 delayed invoices."
    - "High-risk client count increased — consider tightening credit terms."

    Returns
    -------
    List of created notification dicts.
    """
    ai = _get_ai_client()
    context = context or {}
    created = []

    if not ai:
        # Fallback rule-based recommendations
        fallback = [
            ("Follow-up recommended", "Consider following up on invoices overdue by 7+ days."),
            ("Cash-flow watch", "Several pending invoices may affect next week's cashflow."),
        ]
        for title, msg in fallback:
            note = create_notification(
                user_id=user_id,
                notification_type=NotificationType.AI_INSIGHT,
                title=title,
                message=msg,
                metadata={"source": "rule_based"},
                priority=NotificationPriority.MEDIUM,
            )
            created.append(note)
        return created

    try:
        prompt = (
            f"Based on this business context: {json.dumps(context)}, "
            "generate 3 concise, actionable business recommendations for an invoice management platform. "
            "Return JSON: {\"recommendations\": [{\"title\": \"...\", \"message\": \"...\"}]}"
        )
        resp = ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        recs = data.get("recommendations", [])
        for rec in recs[:3]:
            note = create_notification(
                user_id=user_id,
                notification_type=NotificationType.AI_INSIGHT,
                title=rec.get("title", "AI Recommendation"),
                message=rec.get("message", ""),
                metadata={"source": "ai", **context},
                priority=NotificationPriority.HIGH,
            )
            created.append(note)
    except Exception as exc:
        logger.warning("AI recommendation generation failed: %s", exc)

    return created


# ===========================================================================
# 13. PUSH NOTIFICATION PLACEHOLDER
# ===========================================================================

def send_push_notification(notification: dict) -> dict:
    """
    Send a push notification (future-ready architecture).

    Currently a structured placeholder. Wire up a provider such as:
    - Firebase Cloud Messaging (FCM) for Android / web
    - Apple Push Notification Service (APNs) for iOS
    - Expo Push Notifications for Expo/React Native

    To activate, set FCM_SERVER_KEY (or equivalent) in environment and
    replace the body below with the provider SDK call.
    """
    import os
    fcm_key = os.getenv("FCM_SERVER_KEY")

    if not fcm_key:
        logger.debug("Push notification skipped — FCM_SERVER_KEY not set")
        return {"ok": False, "reason": "FCM_SERVER_KEY not configured"}

    payload = {
        "to": notification.get("metadata", {}).get("push_token"),
        "notification": {
            "title": notification.get("title", "InvoiceFlow"),
            "body": notification.get("message", ""),
        },
        "data": {
            "notification_type": notification.get("notification_type"),
            "entity_id": notification.get("entity_id"),
        },
    }

    # TODO: Replace with your FCM / APNs SDK call
    logger.info("Push notification queued: %s", payload.get("notification", {}).get("title"))
    return {"ok": True, "queued": True, "provider": "fcm"}


# ===========================================================================
# 14. SCHEDULED NOTIFICATIONS
# ===========================================================================

def schedule_notification(
    user_id: int,
    notification_type: str,
    message: str,
    delay: timedelta,
    *,
    title: str = "",
    metadata: dict | None = None,
    recurrence: str | None = None,
) -> dict:
    """
    Schedule a notification to be delivered after a delay.

    Parameters
    ----------
    user_id           : Recipient user.
    notification_type : NotificationType value.
    message           : Notification body.
    delay             : timedelta until delivery (e.g. timedelta(days=3)).
    title             : Notification headline.
    metadata          : Extra data payload.
    recurrence        : 'daily' | 'weekly' | 'monthly' | None for one-shot.

    Returns
    -------
    Scheduling metadata dict. Wire the job_id into your task queue
    (Celery, APScheduler, RQ) to execute `create_notification()` at run_at.
    """
    run_at = _now() + delay
    job_id = _new_id()

    logger.info(
        "Notification scheduled: type=%s user=%s at=%s recurrence=%s job=%s",
        notification_type, user_id, run_at.isoformat(), recurrence, job_id,
    )

    # TODO: Enqueue to your task queue:
    # celery_app.send_task(
    #     "tasks.deliver_notification",
    #     args=[user_id, notification_type, message, title, metadata],
    #     eta=run_at,
    # )

    return {
        "job_id": job_id,
        "user_id": user_id,
        "notification_type": notification_type,
        "scheduled_at": run_at.isoformat(),
        "delay_seconds": int(delay.total_seconds()),
        "recurrence": recurrence,
    }


# ===========================================================================
# 15. AI BUSINESS HEALTH ALERTS
# ===========================================================================

def generate_health_alerts(
    user_id: int,
    *,
    current_health_score: float,
    previous_health_score: float,
    context: dict | None = None,
) -> list[dict]:
    """
    Generate business health notifications when score drops significantly.

    Thresholds
    ----------
    Drop ≥ 15 points  → CRITICAL alert
    Drop ≥ 8 points   → HIGH alert
    Drop ≥ 3 points   → MEDIUM alert
    Score < 60        → Always alert regardless of drop

    Parameters
    ----------
    current_health_score  : Latest computed business health score (0–100).
    previous_health_score : Score from the previous period.
    context               : Extra business context for AI message generation.
    """
    context = context or {}
    drop = previous_health_score - current_health_score
    alerts = []

    if drop < 3 and current_health_score >= 60:
        return []

    if drop >= 15 or current_health_score < 40:
        priority = NotificationPriority.CRITICAL
    elif drop >= 8 or current_health_score < 60:
        priority = NotificationPriority.HIGH
    else:
        priority = NotificationPriority.MEDIUM

    ai = _get_ai_client()
    if ai:
        try:
            prompt = (
                f"Business health dropped from {previous_health_score:.1f} to "
                f"{current_health_score:.1f} (drop of {drop:.1f} points). "
                f"Context: {json.dumps(context)}. "
                "Write a concise 2-sentence alert for the business owner."
            )
            resp = ai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100,
            )
            message = resp.choices[0].message.content.strip()
        except Exception as exc:
            logger.warning("AI health alert generation failed: %s", exc)
            message = (
                f"Business health score dropped from {previous_health_score:.0f} "
                f"to {current_health_score:.0f} this period. Immediate review recommended."
            )
    else:
        message = (
            f"Business health score dropped from {previous_health_score:.0f} "
            f"to {current_health_score:.0f} this period. Immediate review recommended."
        )

    note = create_notification(
        user_id=user_id,
        notification_type=NotificationType.BUSINESS_HEALTH_WARNING,
        title=f"Business Health Alert — Score {current_health_score:.0f}/100",
        message=message,
        priority=priority,
        metadata={
            "current_score": current_health_score,
            "previous_score": previous_health_score,
            "drop": round(drop, 1),
            **context,
        },
    )
    alerts.append(note)
    return alerts


# ===========================================================================
# 16. NOTIFICATION ACTIVITY TIMELINE
# ===========================================================================

def build_activity_timeline(
    user_id: int,
    *,
    limit: int = 50,
    include_team: bool = False,
) -> list[dict]:
    """
    Build a real-time activity feed for the dashboard timeline widget.

    Returns
    -------
    List of timeline entry dicts, newest first:
    {
        "id": ...,
        "icon": "💳",
        "label": "Invoice INV-104 paid",
        "type": "invoice_paid",
        "entity_id": ...,
        "timestamp": "...",
        "is_read": bool,
        "priority": "medium"
    }
    """
    Notification = _notification_model()

    q = Notification.query.filter_by(user_id=user_id, is_archived=False)
    if include_team:
        # also pull team notifications if team_id is available
        pass  # Extend with team_id filter in your app

    recent = q.order_by(Notification.created_at.desc()).limit(limit).all()

    return [_to_timeline_entry(n) for n in recent]


def _to_timeline_entry(note) -> dict:
    icons = {
        NotificationType.INVOICE_CREATED: "📄",
        NotificationType.INVOICE_PAID: "💳",
        NotificationType.INVOICE_OVERDUE: "⚠️",
        NotificationType.PAYMENT_RECEIVED: "✅",
        NotificationType.AI_INSIGHT: "🤖",
        NotificationType.WORKFLOW_COMPLETED: "⚡",
        NotificationType.WORKFLOW_FAILED: "❌",
        NotificationType.REPORT_GENERATED: "📊",
        NotificationType.REMINDER_SENT: "🔔",
        NotificationType.CLIENT_RISK_ALERT: "🚨",
        NotificationType.WEEKLY_SUMMARY: "📋",
        NotificationType.BUSINESS_HEALTH_WARNING: "📉",
        NotificationType.REVENUE_MILESTONE: "🏆",
        NotificationType.TEAM_MENTION: "💬",
        NotificationType.WORKFLOW_ASSIGNED: "📌",
        NotificationType.ADMIN_ANNOUNCEMENT: "📢",
    }
    ntype = getattr(note, "notification_type", "")
    return {
        "id": note.id,
        "icon": icons.get(ntype, "🔔"),
        "label": getattr(note, "title", note.message[:60] if note.message else ""),
        "body": note.message,
        "type": ntype,
        "entity_id": getattr(note, "entity_id", None),
        "entity_type": getattr(note, "entity_type", ""),
        "timestamp": note.created_at.isoformat() if note.created_at else None,
        "is_read": note.is_read,
        "priority": getattr(note, "priority", NotificationPriority.MEDIUM),
    }


# ===========================================================================
# 18. AI INSIGHT STREAMING
# ===========================================================================

def stream_ai_insights(
    user_id: int,
    *,
    context: dict | None = None,
) -> Any:
    """
    Stream AI-generated business insights token-by-token via OpenAI streaming.

    Designed to be consumed by a Server-Sent Events (SSE) or WebSocket route.
    Each yielded item is a text chunk to push to the client.

    Usage in an SSE route
    ---------------------
    @app.route("/api/notifications/ai-stream")
    def ai_stream():
        def generate():
            for chunk in stream_ai_insights(user_id=current_user.id, context=...):
                yield f"data: {chunk}\\n\\n"
        return Response(generate(), mimetype="text/event-stream")
    """
    ai = _get_ai_client()
    context = context or {}
    sio = _get_socketio()

    if not ai:
        fallback = "AI insights are not available. Please configure OPENAI_API_KEY."
        if sio:
            sio.emit("ai_insight_chunk", {"chunk": fallback}, room=f"user_{user_id}")
        return

    prompt = (
        f"You are an AI CFO assistant analysing a business's invoice data. "
        f"Business context: {json.dumps(context)}. "
        "Deliver 3 actionable insights about cashflow, overdue risk, and collection "
        "opportunities. Be concise and direct."
    )

    try:
        stream = ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            stream=True,
        )
        full_text = ""
        for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                full_text += delta
                if sio:
                    sio.emit(
                        "ai_insight_chunk",
                        {"chunk": delta, "user_id": user_id},
                        room=f"user_{user_id}",
                    )
                yield delta

        # After streaming completes, persist the full insight as a notification
        create_notification(
            user_id=user_id,
            notification_type=NotificationType.AI_INSIGHT,
            title="AI Business Insight",
            message=full_text.strip(),
            priority=NotificationPriority.HIGH,
            metadata={"source": "streamed", **context},
        )

        if sio:
            sio.emit("ai_insight_complete", {"user_id": user_id}, room=f"user_{user_id}")

    except Exception as exc:
        logger.error("AI insight streaming failed for user %s: %s", user_id, exc)
        if sio:
            sio.emit("ai_insight_error", {"error": str(exc)}, room=f"user_{user_id}")


# ===========================================================================
# 19. NOTIFICATION ANALYTICS
# ===========================================================================

def get_notification_analytics(
    user_id: int,
    *,
    days: int = 30,
) -> dict:
    """
    Return notification engagement and delivery analytics for the dashboard.

    Metrics
    -------
    total_sent          : All notifications created in the period.
    read_count          : Notifications marked as read.
    open_rate           : Percentage of notifications opened.
    by_type             : Breakdown by notification type.
    reminders_sent      : Count of REMINDER_SENT notifications.
    overdue_alerts      : Count of INVOICE_OVERDUE notifications.
    ai_insights_count   : Count of AI_INSIGHT notifications.
    workflow_events     : Count of WORKFLOW_COMPLETED + WORKFLOW_FAILED.
    critical_count      : Count of CRITICAL priority notifications.
    """
    Notification = _notification_model()
    since = _now() - timedelta(days=days)

    all_notifs = (
        Notification.query
        .filter(Notification.user_id == user_id, Notification.created_at >= since)
        .all()
    )

    total = len(all_notifs)
    read_count = sum(1 for n in all_notifs if n.is_read)
    open_rate = round((read_count / total * 100) if total else 0, 1)

    by_type: dict[str, int] = {}
    for n in all_notifs:
        by_type[n.notification_type] = by_type.get(n.notification_type, 0) + 1

    critical = sum(
        1 for n in all_notifs
        if getattr(n, "priority", "") == NotificationPriority.CRITICAL
    )

    return {
        "period_days": days,
        "total_sent": total,
        "read_count": read_count,
        "open_rate": open_rate,
        "unread_count": total - read_count,
        "by_type": by_type,
        "reminders_sent": by_type.get(NotificationType.REMINDER_SENT, 0),
        "overdue_alerts": by_type.get(NotificationType.INVOICE_OVERDUE, 0),
        "ai_insights_count": by_type.get(NotificationType.AI_INSIGHT, 0),
        "workflow_events": (
            by_type.get(NotificationType.WORKFLOW_COMPLETED, 0)
            + by_type.get(NotificationType.WORKFLOW_FAILED, 0)
        ),
        "critical_count": critical,
    }


# ===========================================================================
# 20. TEAM COLLABORATION NOTIFICATIONS
# ===========================================================================

def notify_team(
    team_id: int,
    notification_type: str,
    message: str,
    *,
    title: str = "",
    sent_by: int,
    entity_id: str | None = None,
    entity_type: str = "workflow",
    metadata: dict | None = None,
) -> list[dict]:
    """
    Broadcast a notification to all members of a team.

    Covers: mentions, shared analytics, workflow assignments,
    admin announcements, and team alert broadcasts.

    Parameters
    ----------
    team_id           : Target team ID.
    notification_type : NotificationType value.
    message           : Notification body.
    title             : Notification headline.
    sent_by           : User ID of the sender.
    entity_id         : Related entity ID (invoice, workflow, etc.).
    entity_type       : Entity type string.
    metadata          : Extra data payload.

    Returns
    -------
    List of created notification dicts (one per team member).
    """
    try:
        from app.models import TeamMember
        members = TeamMember.query.filter_by(team_id=team_id).all()
        member_ids = [m.user_id for m in members if m.user_id != sent_by]
    except ImportError:
        logger.warning("TeamMember model not found — team notification skipped")
        return []

    created = []
    for uid in member_ids:
        note = create_notification(
            user_id=uid,
            team_id=team_id,
            notification_type=notification_type,
            title=title or _default_title(notification_type),
            message=message,
            entity_id=entity_id,
            entity_type=entity_type,
            metadata={**(metadata or {}), "sent_by": sent_by},
        )
        created.append(note)

    logger.info(
        "Team notification sent to %d members (team=%s type=%s)",
        len(created), team_id, notification_type,
    )
    return created


def send_mention_notification(
    mentioned_user_id: int,
    mentioned_by: int,
    context_message: str,
    *,
    entity_id: str | None = None,
    entity_type: str = "invoice",
) -> dict:
    """Notify a user they were mentioned in a comment or workflow action."""
    return create_notification(
        user_id=mentioned_user_id,
        notification_type=NotificationType.TEAM_MENTION,
        title="You were mentioned",
        message=context_message,
        entity_id=entity_id,
        entity_type=entity_type,
        metadata={"mentioned_by": mentioned_by},
        priority=NotificationPriority.MEDIUM,
    )
