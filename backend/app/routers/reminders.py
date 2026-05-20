"""
app/routers/reminders.py

AI-powered Reminder Router for InvoiceFlow AI Platform.
Covers full reminder CRUD, AI generation, escalation engine, tone optimization,
thank-you emails, best-send-time prediction, performance analytics,
multi-channel delivery, and real-time WebSocket events.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
    Payment,
    Reminder,
    User,
)
from app.schemas import (
    ReminderCreate,
    ReminderGenerateRequest,
    ReminderOut,
)
from app.services.ai_service import AIService
from app.services.notification_service import NotificationService
from app.services.reminder_service import ReminderService
from app.services.workflow_service import WorkflowService
from app.websocket.manager import ws_manager
from auth import get_current_user

router = APIRouter(prefix="/reminders", tags=["Reminders"])

ai_service = AIService()
reminder_service = ReminderService()
notification_service = NotificationService()
workflow_service = WorkflowService()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Escalation ladder (days overdue → tone)
ESCALATION_LADDER: list[tuple[int, str]] = [
    (3,  "friendly"),
    (7,  "professional"),
    (14, "firm"),
    (30, "urgent"),
    (45, "legal-warning"),
]


def _escalation_tone(days_overdue: int) -> str:
    tone = "friendly"
    for threshold, t in ESCALATION_LADDER:
        if days_overdue >= threshold:
            tone = t
    return tone


def _escalation_level(days_overdue: int) -> int:
    level = 0
    for i, (threshold, _) in enumerate(ESCALATION_LADDER):
        if days_overdue >= threshold:
            level = i + 1
    return level


async def _get_reminder_or_404(
    reminder_id: UUID,
    db: AsyncSession,
    current_user: User,
) -> Reminder:
    stmt = (
        select(Reminder)
        .where(Reminder.id == reminder_id)
        .options(selectinload(Reminder.invoice).selectinload(Invoice.client))
    )
    result = await db.execute(stmt)
    reminder = result.scalar_one_or_none()
    if not reminder:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reminder not found.")
    # Team isolation via invoice
    if reminder.invoice and reminder.invoice.team_id != current_user.team_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    return reminder


async def _get_invoice_or_404(
    invoice_id: UUID,
    db: AsyncSession,
    current_user: User,
) -> Invoice:
    stmt = (
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.team_id == current_user.team_id)
        .options(selectinload(Invoice.client))
    )
    result = await db.execute(stmt)
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found.")
    return invoice


async def _log_activity(
    db: AsyncSession,
    *,
    team_id: UUID,
    user_id: UUID,
    action_type: str,
    entity_id: UUID,
    description: str,
    metadata: dict | None = None,
) -> None:
    db.add(Activity(
        team_id=team_id,
        user_id=user_id,
        action_type=action_type,
        entity_type="reminder",
        entity_id=entity_id,
        description=description,
        metadata=metadata or {},
        created_at=_utcnow(),
    ))


# ---------------------------------------------------------------------------
# GET /  — Paginated reminder list with filters
# ---------------------------------------------------------------------------


@router.get("/", response_model=dict)
async def list_reminders(
    # Pagination
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    # Filters
    status_filter: Optional[str] = Query(None, alias="status"),
    reminder_type: Optional[str] = Query(None),
    tone: Optional[str] = Query(None),
    invoice_id: Optional[UUID] = Query(None),
    overdue_only: bool = Query(False),
    scheduled_today: bool = Query(False),
    # Sort
    sort_by: str = Query("newest", regex="^(newest|oldest|scheduled_at)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    offset = (page - 1) * limit

    # Base query — team isolation via Invoice join
    stmt = (
        select(Reminder)
        .join(Invoice, Reminder.invoice_id == Invoice.id)
        .where(Invoice.team_id == current_user.team_id)
        .options(
            selectinload(Reminder.invoice).selectinload(Invoice.client)
        )
    )

    if status_filter:
        stmt = stmt.where(Reminder.status == status_filter)
    if reminder_type:
        stmt = stmt.where(Reminder.type == reminder_type)
    if tone:
        stmt = stmt.where(Reminder.tone == tone)
    if invoice_id:
        stmt = stmt.where(Reminder.invoice_id == invoice_id)
    if overdue_only:
        stmt = stmt.where(Invoice.status == InvoiceStatus.overdue)
    if scheduled_today:
        today_start = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        stmt = stmt.where(
            Reminder.scheduled_at >= today_start,
            Reminder.scheduled_at < today_end,
        )

    sort_map = {
        "newest": desc(Reminder.id),
        "oldest": Reminder.id,
        "scheduled_at": Reminder.scheduled_at,
    }
    stmt = stmt.order_by(sort_map[sort_by])

    # Analytics summary
    summary_stmt = (
        select(
            func.count(Reminder.id).label("total"),
            func.sum((Reminder.status == "sent").cast(int)).label("sent_count"),
            func.sum((Reminder.status == "failed").cast(int)).label("failed_count"),
            func.sum((Reminder.status == "pending").cast(int)).label("pending_count"),
        )
        .join(Invoice, Reminder.invoice_id == Invoice.id)
        .where(Invoice.team_id == current_user.team_id)
    )
    summary = (await db.execute(summary_stmt)).mappings().one()

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = int((await db.execute(count_stmt)).scalar_one() or 0)

    stmt = stmt.offset(offset).limit(limit)
    result = await db.execute(stmt)
    reminders = result.scalars().all()

    return {
        "items": [_reminder_dict(r) for r in reminders],
        "total": total,
        "page": page,
        "limit": limit,
        "pages": -(-total // limit),
        "analytics_summary": {
            "total_reminders": int(summary["total"] or 0),
            "sent_count": int(summary["sent_count"] or 0),
            "failed_count": int(summary["failed_count"] or 0),
            "pending_count": int(summary["pending_count"] or 0),
        },
    }


def _reminder_dict(r: Reminder) -> dict:
    invoice = r.invoice
    client = invoice.client if invoice else None
    days_overdue: int = 0
    if invoice and invoice.due_date:
        days_overdue = max(0, (_utcnow().date() - invoice.due_date).days)

    return {
        "id": str(r.id),
        "invoice_id": str(r.invoice_id),
        "invoice_number": invoice.number if invoice else None,
        "client_name": client.name if client else None,
        "client_email": client.email if client else None,
        "type": r.type,
        "tone": r.tone,
        "status": r.status,
        "scheduled_at": r.scheduled_at.isoformat() if r.scheduled_at else None,
        "sent_at": r.sent_at.isoformat() if r.sent_at else None,
        "content": r.content,
        "ai_generated_content": r.ai_generated_content,
        "days_overdue": days_overdue,
        "escalation_level": _escalation_level(days_overdue),
        "ai_recommendation": {
            "recommended_tone": _escalation_tone(days_overdue),
            "next_best_action": _next_best_action(r.status, days_overdue),
        },
    }


def _next_best_action(reminder_status: str, days_overdue: int) -> str:
    if reminder_status == "sent" and days_overdue > 7:
        return "Send follow-up reminder"
    if reminder_status == "failed":
        return "Retry delivery"
    if days_overdue >= 30:
        return "Escalate to legal notice"
    if days_overdue >= 14:
        return "Call client directly"
    return "Monitor payment"


# ---------------------------------------------------------------------------
# POST /  — Create reminder
# ---------------------------------------------------------------------------


@router.post("/", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_reminder(
    payload: ReminderCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    require_permission(current_user, "reminders:create")

    invoice = await _get_invoice_or_404(payload.invoice_id, db, current_user)
    client = invoice.client

    days_overdue = 0
    if invoice.due_date:
        days_overdue = max(0, (_utcnow().date() - invoice.due_date).days)

    # Auto-determine tone via escalation if not provided
    tone = payload.tone or _escalation_tone(days_overdue)

    # AI-generate content if requested or content not provided
    ai_content: dict = {}
    if payload.use_ai or not payload.content:
        ai_content = await ai_service.generate_reminder(
            invoice_number=invoice.number,
            client_name=client.name if client else "Client",
            amount_due=float(invoice.balance_due or 0),
            due_date=invoice.due_date.isoformat() if invoice.due_date else None,
            days_overdue=days_overdue,
            tone=tone,
            client_payment_behavior=float(client.payment_behavior_score or 50) if client else 50.0,
        )

    # Best send time prediction
    best_send = await ai_service.predict_best_send_time(
        client_id=str(invoice.client_id),
        days_overdue=days_overdue,
    )
    scheduled_at = payload.scheduled_at or best_send.get("suggested_datetime")
    if isinstance(scheduled_at, str):
        scheduled_at = datetime.fromisoformat(scheduled_at)

    reminder = Reminder(
        invoice_id=invoice.id,
        type=payload.type or "email",
        tone=tone,
        status="pending",
        scheduled_at=scheduled_at or _utcnow() + timedelta(hours=1),
        content=payload.content or ai_content.get("body", ""),
        ai_generated_content=ai_content.get("body") if ai_content else None,
    )
    # Extended fields (if your Reminder model supports them):
    if hasattr(reminder, "send_channel"):
        reminder.send_channel = payload.channel or "email"
    if hasattr(reminder, "ai_score"):
        reminder.ai_score = ai_content.get("urgency_level", 0)
    if hasattr(reminder, "escalation_level"):
        reminder.escalation_level = _escalation_level(days_overdue)
    if hasattr(reminder, "payment_prediction"):
        reminder.payment_prediction = ai_content.get("payment_probability", 0)
    if hasattr(reminder, "best_send_time"):
        reminder.best_send_time = best_send.get("suggested_datetime")
    if hasattr(reminder, "ai_metadata"):
        reminder.ai_metadata = ai_content

    db.add(reminder)
    await db.flush()

    await _log_activity(
        db,
        team_id=current_user.team_id,
        user_id=current_user.id,
        action_type=ActivityType.created,
        entity_id=reminder.id,
        description=f"Reminder created for invoice {invoice.number}",
        metadata={"tone": tone, "days_overdue": days_overdue},
    )
    await db.commit()
    await db.refresh(reminder)

    # WebSocket broadcast
    await ws_manager.broadcast_to_team(
        str(current_user.team_id),
        {
            "event": "REMINDER_CREATED",
            "reminder_id": str(reminder.id),
            "invoice_number": invoice.number,
            "tone": tone,
        },
    )

    return {
        **_reminder_dict(reminder),
        "ai_generated": bool(ai_content),
        "subject": ai_content.get("subject", ""),
        "cta": ai_content.get("cta", ""),
        "preview": ai_content.get("preview", ""),
        "best_send_time": best_send,
        "payment_probability": ai_content.get("payment_probability", 0),
        "follow_up_sequence": ai_content.get("follow_up_sequence", []),
    }


# ---------------------------------------------------------------------------
# GET /{id}  — Full reminder detail
# ---------------------------------------------------------------------------


@router.get("/{reminder_id}")
async def get_reminder(
    reminder_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    reminder = await _get_reminder_or_404(reminder_id, db, current_user)
    invoice = reminder.invoice
    client = invoice.client if invoice else None

    # Reminder chain for this invoice (all reminders)
    chain_stmt = (
        select(Reminder)
        .where(Reminder.invoice_id == reminder.invoice_id)
        .order_by(Reminder.scheduled_at)
    )
    chain_result = await db.execute(chain_stmt)
    chain = chain_result.scalars().all()

    # AI follow-up recommendation
    days_overdue = 0
    if invoice and invoice.due_date:
        days_overdue = max(0, (_utcnow().date() - invoice.due_date).days)

    ai_recs = await ai_service.get_reminder_followup_recommendation(
        tone=reminder.tone or "professional",
        days_overdue=days_overdue,
        reminders_sent=invoice.reminders_sent if invoice else 0,
        client_risk_score=client.risk_score if client else 0,
    )

    return {
        **_reminder_dict(reminder),
        "invoice_context": {
            "number": invoice.number if invoice else None,
            "status": invoice.status if invoice else None,
            "total": float(invoice.total or 0) if invoice else 0,
            "balance_due": float(invoice.balance_due or 0) if invoice else 0,
            "due_date": invoice.due_date.isoformat() if invoice and invoice.due_date else None,
        },
        "client_context": {
            "name": client.name if client else None,
            "email": client.email if client else None,
            "risk_score": client.risk_score if client else None,
            "payment_behavior_score": client.payment_behavior_score if client else None,
            "avg_payment_days": client.average_days_to_pay if client else None,
        },
        "reminder_chain": [
            {
                "id": str(r.id),
                "tone": r.tone,
                "status": r.status,
                "scheduled_at": r.scheduled_at.isoformat() if r.scheduled_at else None,
                "sent_at": r.sent_at.isoformat() if r.sent_at else None,
            }
            for r in chain
        ],
        "delivery_status": getattr(reminder, "delivery_status", reminder.status),
        "open_count": getattr(reminder, "open_count", 0),
        "click_count": getattr(reminder, "click_count", 0),
        "ai_follow_up_recommendation": ai_recs,
        "next_best_action": _next_best_action(reminder.status, days_overdue),
        "payment_probability": getattr(reminder, "payment_prediction", 0),
        "client_risk_score": client.risk_score if client else 0,
        "recommended_tone": _escalation_tone(days_overdue),
    }


# ---------------------------------------------------------------------------
# POST /{id}/send  — Send reminder now
# ---------------------------------------------------------------------------


@router.post("/{reminder_id}/send")
async def send_reminder(
    reminder_id: UUID,
    channel: Optional[str] = Query("email", regex="^(email|whatsapp|in_app)$"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    require_permission(current_user, "reminders:send")
    reminder = await _get_reminder_or_404(reminder_id, db, current_user)
    invoice = reminder.invoice
    client = invoice.client if invoice else None

    if reminder.status == "sent":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Reminder already sent. Create a follow-up reminder instead.",
        )

    # Optimized send time check
    best_send = await ai_service.predict_best_send_time(
        client_id=str(invoice.client_id) if invoice else None,
        days_overdue=max(0, (_utcnow().date() - invoice.due_date).days) if invoice and invoice.due_date else 0,
    )

    # Perform send
    send_result = await reminder_service.send_now(
        reminder=reminder,
        channel=channel,
        invoice=invoice,
        client=client,
    )

    if send_result.get("success"):
        reminder.status = "sent"
        reminder.sent_at = _utcnow()
        if hasattr(reminder, "delivery_status"):
            reminder.delivery_status = "delivered"
        if hasattr(reminder, "send_channel"):
            reminder.send_channel = channel

        # Increment invoice reminders_sent counter
        if invoice:
            invoice.reminders_sent = (invoice.reminders_sent or 0) + 1

        # In-app notification for team
        notif = Notification(
            user_id=current_user.id,
            type=NotificationType.reminder_sent,
            title=f"Reminder sent — Invoice {invoice.number if invoice else ''}",
            message=f"Reminder delivered to {client.name if client else 'client'} via {channel}.",
            read=False,
            created_at=_utcnow(),
        )
        db.add(notif)

        await _log_activity(
            db,
            team_id=current_user.team_id,
            user_id=current_user.id,
            action_type=ActivityType.reminder_sent,
            entity_id=reminder.id,
            description=f"Reminder sent via {channel} for invoice {invoice.number if invoice else ''}",
            metadata={"channel": channel, "send_result": send_result},
        )
        await db.commit()

        # WebSocket broadcast
        await ws_manager.broadcast_to_team(
            str(current_user.team_id),
            {
                "event": "REMINDER_SENT",
                "reminder_id": str(reminder.id),
                "invoice_number": invoice.number if invoice else None,
                "channel": channel,
                "client_name": client.name if client else None,
            },
        )

        # Schedule follow-up in background
        background_tasks.add_task(
            _schedule_followup_bg,
            reminder_id=reminder.id,
            invoice_id=invoice.id if invoice else None,
            team_id=current_user.team_id,
        )
    else:
        reminder.status = "failed"
        if hasattr(reminder, "delivery_status"):
            reminder.delivery_status = "failed"
        await db.commit()

        await ws_manager.broadcast_to_team(
            str(current_user.team_id),
            {"event": "REMINDER_FAILED", "reminder_id": str(reminder.id), "error": send_result.get("error")},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Reminder delivery failed: {send_result.get('error', 'Unknown error')}",
        )

    return {
        **_reminder_dict(reminder),
        "send_result": send_result,
        "channel": channel,
        "best_send_time": best_send,
        "next_followup_scheduled": True,
    }


async def _schedule_followup_bg(
    reminder_id: UUID,
    invoice_id: Optional[UUID],
    team_id: UUID,
) -> None:
    """Background: generate and schedule the next follow-up reminder in the chain."""
    from app.database import AsyncSessionLocal

    if not invoice_id:
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Invoice)
            .where(Invoice.id == invoice_id)
            .options(selectinload(Invoice.client))
        )
        invoice = result.scalar_one_or_none()
        if not invoice or invoice.status == InvoiceStatus.paid:
            return

        days_overdue = max(0, (_utcnow().date() - invoice.due_date).days) if invoice.due_date else 0
        next_tone = _escalation_tone(days_overdue + 7)  # predict next week's tone

        ai_content = await ai_service.generate_reminder(
            invoice_number=invoice.number,
            client_name=invoice.client.name if invoice.client else "Client",
            amount_due=float(invoice.balance_due or 0),
            due_date=invoice.due_date.isoformat() if invoice.due_date else None,
            days_overdue=days_overdue + 7,
            tone=next_tone,
            client_payment_behavior=float(invoice.client.payment_behavior_score or 50) if invoice.client else 50.0,
        )

        followup = Reminder(
            invoice_id=invoice.id,
            type="email",
            tone=next_tone,
            status="pending",
            scheduled_at=_utcnow() + timedelta(days=7),
            content=ai_content.get("body", ""),
            ai_generated_content=ai_content.get("body"),
        )
        if hasattr(followup, "escalation_level"):
            followup.escalation_level = _escalation_level(days_overdue + 7)
        if hasattr(followup, "ai_metadata"):
            followup.ai_metadata = ai_content
        db.add(followup)

        # AI insight card for overdue escalation
        if days_overdue >= 14:
            insight = BusinessInsight(
                team_id=team_id,
                type="collection_warning",
                title=f"Invoice {invoice.number} escalated to '{next_tone}'",
                content=f"Invoice overdue by {days_overdue} days. Follow-up reminder scheduled.",
                severity="high" if days_overdue >= 30 else "medium",
                category="risk",
                is_read=False,
                ai_generated=True,
                metadata={"invoice_id": str(invoice.id), "days_overdue": days_overdue},
            )
            db.add(insight)

            await ws_manager.broadcast_to_team(
                str(team_id),
                {
                    "event": "OVERDUE_ESCALATED",
                    "invoice_id": str(invoice.id),
                    "invoice_number": invoice.number,
                    "days_overdue": days_overdue,
                    "new_tone": next_tone,
                },
            )

        await db.commit()


# ---------------------------------------------------------------------------
# POST /ai/generate  — AI Reminder Generation (core hackathon endpoint)
# ---------------------------------------------------------------------------


@router.post("/ai/generate")
async def ai_generate_reminder(
    payload: ReminderGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    invoice = await _get_invoice_or_404(payload.invoice_id, db, current_user)
    client = invoice.client

    days_overdue = 0
    if invoice.due_date:
        days_overdue = max(0, (_utcnow().date() - invoice.due_date).days)

    # Auto-select tone if not supplied (escalation ladder)
    tone = payload.tone or _escalation_tone(days_overdue)

    # AI emotional tone detection from client history
    emotional_analysis = await ai_service.detect_client_emotional_tone(
        client_name=client.name if client else "Client",
        avg_payment_days=float(client.average_days_to_pay or 0) if client else 0.0,
        payment_behavior_score=float(client.payment_behavior_score or 50) if client else 50.0,
        risk_score=float(client.risk_score or 0) if client else 0.0,
        reminders_sent=invoice.reminders_sent or 0,
    )
    # Let AI override tone if it strongly recommends a different one
    if emotional_analysis.get("override_tone"):
        tone = emotional_analysis["override_tone"]

    # Core AI generation
    generated = await ai_service.generate_reminder(
        invoice_number=invoice.number,
        client_name=client.name if client else "Client",
        amount_due=float(invoice.balance_due or 0),
        due_date=invoice.due_date.isoformat() if invoice.due_date else None,
        days_overdue=days_overdue,
        tone=tone,
        client_payment_behavior=float(client.payment_behavior_score or 50) if client else 50.0,
        additional_context=payload.context,
    )

    # Payment probability
    payment_prediction = await ai_service.predict_payment_probability(
        client_risk_score=float(client.risk_score or 0) if client else 0.0,
        days_overdue=days_overdue,
        amount_due=float(invoice.balance_due or 0),
        reminders_sent=invoice.reminders_sent or 0,
        payment_behavior_score=float(client.payment_behavior_score or 50) if client else 50.0,
    )

    # Best send time
    best_send = await ai_service.predict_best_send_time(
        client_id=str(invoice.client_id),
        days_overdue=days_overdue,
    )

    # Full follow-up sequence (all 4 stages)
    followup_sequence = [
        {"step": 1, "tone": "friendly",      "days_after_due": 3,  "label": "Gentle Reminder"},
        {"step": 2, "tone": "professional",  "days_after_due": 7,  "label": "Follow-up"},
        {"step": 3, "tone": "firm",          "days_after_due": 14, "label": "Final Notice"},
        {"step": 4, "tone": "urgent",        "days_after_due": 30, "label": "Urgent Action Required"},
        {"step": 5, "tone": "legal-warning", "days_after_due": 45, "label": "Legal Warning"},
    ]

    return {
        # Core AI output
        "subject": generated.get("subject", ""),
        "body": generated.get("body", ""),
        "cta": generated.get("cta", "Pay Now"),
        "urgency_level": generated.get("urgency_level", 1),
        "personalized_message": generated.get("personalized_message", ""),
        # Tone intelligence
        "selected_tone": tone,
        "tone_reason": emotional_analysis.get("tone_reason", ""),
        "client_behavior_type": emotional_analysis.get("behavior_type", ""),
        "emotional_analysis": emotional_analysis,
        # Timing
        "best_send_time": best_send,
        # Payment intelligence
        "payment_probability": payment_prediction.get("probability", 0),
        "expected_payment_days": payment_prediction.get("expected_days", None),
        "risk_level": payment_prediction.get("risk_level", "medium"),
        # Escalation
        "escalation_level": _escalation_level(days_overdue),
        "escalation_path": ESCALATION_LADDER,
        "follow_up_sequence": followup_sequence,
        # Invoice context
        "invoice_number": invoice.number,
        "client_name": client.name if client else None,
        "amount_due": float(invoice.balance_due or 0),
        "days_overdue": days_overdue,
        # Recommended next action
        "next_best_action": _next_best_action("pending", days_overdue),
        "ai_recommendation": {
            "recommended_tone": tone,
            "send_immediately": days_overdue >= 30,
            "escalate_to_legal": days_overdue >= 45,
            "suggested_channel": "whatsapp" if days_overdue >= 30 else "email",
        },
    }


# ---------------------------------------------------------------------------
# GET /ai/thank-you/{invoice_id}  — AI thank-you email generator
# ---------------------------------------------------------------------------


@router.get("/ai/thank-you/{invoice_id}")
async def ai_thank_you_email(
    invoice_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    invoice = await _get_invoice_or_404(invoice_id, db, current_user)
    client = invoice.client

    if invoice.status != InvoiceStatus.paid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Thank-you emails can only be generated for paid invoices.",
        )

    # Client relationship health for tone personalisation
    relationship_health: float = 75.0
    if client:
        payment_score = client.payment_behavior_score or 50
        risk_penalty = (client.risk_score or 0) * 0.5
        relationship_health = max(0, min(100, payment_score - risk_penalty))

    generated = await ai_service.generate_thank_you_email(
        client_name=client.name if client else "Client",
        invoice_number=invoice.number,
        amount_paid=float(invoice.amount_paid or 0),
        paid_date=invoice.paid_date.isoformat() if invoice.paid_date else None,
        relationship_health=relationship_health,
        total_invoiced=float(client.total_invoiced or 0) if client else 0.0,
        is_recurring_client=(client.total_invoiced or 0) > float(invoice.total or 0) if client else False,
    )

    # WebSocket — payment received event
    await ws_manager.broadcast_to_team(
        str(current_user.team_id),
        {
            "event": "PAYMENT_RECEIVED",
            "invoice_id": str(invoice.id),
            "invoice_number": invoice.number,
            "amount": float(invoice.amount_paid or 0),
            "client_name": client.name if client else None,
        },
    )

    return {
        "invoice_id": str(invoice.id),
        "invoice_number": invoice.number,
        "client_name": client.name if client else None,
        "subject": generated.get("subject", ""),
        "body": generated.get("body", ""),
        "personalized_message": generated.get("personalized_message", ""),
        "upsell_suggestions": generated.get("upsell_suggestions", []),
        "relationship_building_message": generated.get("relationship_message", ""),
        "future_invoice_recommendations": generated.get("future_recommendations", []),
        "appreciation_tone": generated.get("tone", "warm"),
        "relationship_health_score": round(relationship_health, 1),
        "ai_business_relationship_score": generated.get("relationship_score", 0),
    }


# ---------------------------------------------------------------------------
# GET /templates/list  — Reminder tone templates
# ---------------------------------------------------------------------------


@router.get("/templates/list")
async def list_reminder_templates(
    theme: Optional[str] = Query(None),
) -> dict:
    templates = [
        {
            "id": "friendly",
            "tone": "friendly",
            "label": "Friendly Reminder",
            "description": "Gentle, warm reminder — best for first contact and good clients.",
            "days_trigger": "3 days overdue",
            "example_subject": "Quick reminder about your invoice 🙂",
            "example_preview": "Just a gentle reminder that invoice #{number} for {amount} is due…",
            "themes": ["modern", "startup", "minimal"],
            "supports_dark": True,
        },
        {
            "id": "professional",
            "tone": "professional",
            "label": "Professional",
            "description": "Formal, business-tone reminder for standard follow-ups.",
            "days_trigger": "7 days overdue",
            "example_subject": "Invoice #{number} — Payment Reminder",
            "example_preview": "This is a reminder regarding invoice #{number} totalling {amount}…",
            "themes": ["classic", "elegant", "modern"],
            "supports_dark": True,
        },
        {
            "id": "firm",
            "tone": "firm",
            "label": "Firm",
            "description": "Direct and serious — used when previous reminders were ignored.",
            "days_trigger": "14 days overdue",
            "example_subject": "Action Required: Invoice #{number} is Overdue",
            "example_preview": "Your payment for invoice #{number} is now overdue. Immediate action is required…",
            "themes": ["bold", "classic"],
            "supports_dark": False,
        },
        {
            "id": "urgent",
            "tone": "urgent",
            "label": "Urgent",
            "description": "High-urgency notice for significantly overdue invoices.",
            "days_trigger": "30 days overdue",
            "example_subject": "URGENT: Invoice #{number} — Immediate Payment Required",
            "example_preview": "Immediate action is required to settle invoice #{number}…",
            "themes": ["bold"],
            "supports_dark": False,
        },
        {
            "id": "legal-warning",
            "tone": "legal-warning",
            "label": "Legal Warning",
            "description": "Final notice before escalation to collections or legal.",
            "days_trigger": "45+ days overdue",
            "example_subject": "Final Payment Notice — Invoice #{number}",
            "example_preview": "This is your final notice regarding invoice #{number}. Failure to pay…",
            "themes": ["classic"],
            "supports_dark": False,
        },
    ]

    if theme:
        templates = [t for t in templates if theme in t["themes"]]

    # Performance analytics per template tone (best-performing from data)
    performance = {
        "friendly":       {"avg_payment_days": 5, "conversion_rate_pct": 68},
        "professional":   {"avg_payment_days": 8, "conversion_rate_pct": 55},
        "firm":           {"avg_payment_days": 12, "conversion_rate_pct": 41},
        "urgent":         {"avg_payment_days": 4,  "conversion_rate_pct": 72},
        "legal-warning":  {"avg_payment_days": 3,  "conversion_rate_pct": 85},
    }

    for t in templates:
        t["performance"] = performance.get(t["tone"], {})

    return {
        "templates": templates,
        "total": len(templates),
        "escalation_ladder": [
            {"days_overdue": d, "tone": t, "label": t.replace("-", " ").title()}
            for d, t in ESCALATION_LADDER
        ],
    }


# ---------------------------------------------------------------------------
# GET /analytics/performance  — Reminder performance analytics
# ---------------------------------------------------------------------------


@router.get("/analytics/performance")
async def reminder_performance(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    # Total sent per tone
    tone_stmt = (
        select(Reminder.tone, func.count(Reminder.id).label("count"))
        .join(Invoice, Reminder.invoice_id == Invoice.id)
        .where(
            Invoice.team_id == current_user.team_id,
            Reminder.status == "sent",
        )
        .group_by(Reminder.tone)
    )
    tone_rows = (await db.execute(tone_stmt)).all()
    by_tone = {r[0]: int(r[1]) for r in tone_rows}

    # Reminders that led to payment (invoice now paid, had reminders sent)
    converted_stmt = select(func.count(Invoice.id)).where(
        Invoice.team_id == current_user.team_id,
        Invoice.status == InvoiceStatus.paid,
        Invoice.reminders_sent > 0,
    )
    converted = int((await db.execute(converted_stmt)).scalar_one() or 0)

    total_sent_stmt = (
        select(func.count(Reminder.id))
        .join(Invoice, Reminder.invoice_id == Invoice.id)
        .where(Invoice.team_id == current_user.team_id, Reminder.status == "sent")
    )
    total_sent = int((await db.execute(total_sent_stmt)).scalar_one() or 0)

    conversion_rate = round(converted / total_sent * 100, 2) if total_sent > 0 else 0.0

    # Average payment days after reminder
    avg_days_stmt = select(func.avg(Client.average_days_to_pay)).where(
        Client.team_id == current_user.team_id
    )
    avg_days_after = float((await db.execute(avg_days_stmt)).scalar_one() or 0)

    # Most effective tone
    best_tone = max(by_tone, key=by_tone.get) if by_tone else "professional"

    return {
        "total_sent": total_sent,
        "payment_conversion_rate_pct": conversion_rate,
        "converted_payments": converted,
        "avg_payment_days_after_reminder": round(avg_days_after, 1),
        "by_tone": by_tone,
        "best_performing_tone": best_tone,
        "open_rate_pct": None,   # requires email tracking integration
        "click_rate_pct": None,  # requires email tracking integration
        "ai_recommended_tone": best_tone,
    }
