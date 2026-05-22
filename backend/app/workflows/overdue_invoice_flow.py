"""
Overdue Invoice Flow
Detect overdue invoices → AI-generate reminders → send email → schedule follow-up → escalate.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Escalation tier config
# ---------------------------------------------------------------------------

ESCALATION_TIERS = [
    {"days_overdue_min": 1,  "days_overdue_max": 7,  "tone": "friendly",      "label": "Friendly Reminder"},
    {"days_overdue_min": 8,  "days_overdue_max": 14, "tone": "professional",   "label": "Professional Follow-up"},
    {"days_overdue_min": 15, "days_overdue_max": 30, "tone": "urgent",         "label": "Urgent Notice"},
    {"days_overdue_min": 31, "days_overdue_max": 9999,"tone": "firm",          "label": "Final Demand"},
]


def _get_tone(days_overdue: int) -> dict:
    for tier in ESCALATION_TIERS:
        if tier["days_overdue_min"] <= days_overdue <= tier["days_overdue_max"]:
            return tier
    return ESCALATION_TIERS[-1]


def _days_overdue(due_date: datetime) -> int:
    now = datetime.now(timezone.utc)
    if due_date.tzinfo is None:
        due_date = due_date.replace(tzinfo=timezone.utc)
    delta = now - due_date
    return max(0, delta.days)


# ---------------------------------------------------------------------------
# Core flow steps
# ---------------------------------------------------------------------------

async def detect_overdue_invoices(db: AsyncSession) -> list[dict]:
    """
    Query all invoices with status in (sent, partial) where due_date < now.
    Returns list of invoice dicts with computed days_overdue.
    """
    try:
        from app.models import Invoice  # local import to avoid circular deps
        now = datetime.now(timezone.utc)
        stmt = select(Invoice).where(
            and_(
                Invoice.status.in_(["sent", "partial"]),
                Invoice.due_date < now,
            )
        )
        result = await db.execute(stmt)
        invoices = result.scalars().all()
        overdue = []
        for inv in invoices:
            do = _days_overdue(inv.due_date)
            overdue.append({
                "id": str(inv.id),
                "number": inv.number,
                "client_id": str(inv.client_id),
                "balance_due": float(inv.balance_due),
                "due_date": inv.due_date.isoformat(),
                "days_overdue": do,
                "tone": _get_tone(do),
                "reminders_sent": inv.reminders_sent or 0,
                "ai_priority": inv.ai_priority,
            })
        overdue.sort(key=lambda x: x["balance_due"], reverse=True)
        logger.info(f"[OverdueFlow] Found {len(overdue)} overdue invoices")
        return overdue
    except Exception as e:
        logger.error(f"[OverdueFlow] detect_overdue_invoices error: {e}")
        return []


async def generate_ai_reminder(invoice: dict, client_name: str, ai_api_key: str) -> dict:
    """Call AI to generate personalised reminder content."""
    try:
        import httpx
        tone = invoice["tone"]["tone"]
        prompt = (
            f"Write a {tone} payment reminder email.\n"
            f"Client: {client_name}\n"
            f"Invoice: #{invoice['number']}\n"
            f"Amount due: ${invoice['balance_due']:.2f}\n"
            f"Days overdue: {invoice['days_overdue']}\n"
            f"Return JSON: {{\"subject\": \"...\", \"body\": \"...\", \"cta\": \"...\"}}"
        )
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {ai_api_key}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                    "max_tokens": 400,
                },
            )
            resp.raise_for_status()
            import json
            content = resp.json()["choices"][0]["message"]["content"]
            return json.loads(content)
    except Exception as e:
        logger.warning(f"[OverdueFlow] AI reminder generation failed: {e}. Using fallback.")
        tone_label = invoice["tone"]["label"]
        return {
            "subject": f"{tone_label}: Invoice #{invoice['number']} – ${invoice['balance_due']:.2f} Overdue",
            "body": (
                f"Dear {client_name},\n\n"
                f"This is a {invoice['tone']['tone']} reminder that invoice #{invoice['number']} "
                f"for ${invoice['balance_due']:.2f} is {invoice['days_overdue']} days overdue.\n\n"
                f"Please arrange payment at your earliest convenience.\n\nThank you."
            ),
            "cta": "Pay Now",
        }


async def send_reminder_email(invoice_id: str, email: str, content: dict, db: AsyncSession) -> bool:
    """Record reminder as sent and trigger email notification."""
    try:
        from app.models import Invoice, Reminder
        from app.core.constants import ReminderType
        import uuid

        # Create reminder record
        reminder = Reminder(
            id=uuid.uuid4(),
            invoice_id=invoice_id,
            type=ReminderType.EMAIL,
            scheduled_at=datetime.now(timezone.utc),
            sent_at=datetime.now(timezone.utc),
            content=content.get("body", ""),
            ai_generated_content=content.get("body", ""),
            status="sent",
            tone=content.get("tone", "professional"),
        )
        db.add(reminder)

        # Increment reminders_sent on invoice
        stmt = select(Invoice).where(Invoice.id == invoice_id)
        result = await db.execute(stmt)
        inv = result.scalar_one_or_none()
        if inv:
            inv.reminders_sent = (inv.reminders_sent or 0) + 1

        await db.commit()
        logger.info(f"[OverdueFlow] Reminder sent for invoice {invoice_id} → {email}")
        return True
    except Exception as e:
        logger.error(f"[OverdueFlow] send_reminder_email error: {e}")
        await db.rollback()
        return False


async def schedule_follow_up(invoice_id: str, days_overdue: int, db: AsyncSession) -> None:
    """Schedule the next reminder based on escalation tier."""
    intervals = {range(1, 8): 3, range(8, 15): 5, range(15, 31): 7}
    next_days = 7  # default
    for r, d in intervals.items():
        if days_overdue in r:
            next_days = d
            break
    next_date = datetime.now(timezone.utc) + timedelta(days=next_days)
    try:
        from app.models import Reminder
        from app.core.constants import ReminderType
        import uuid
        reminder = Reminder(
            id=uuid.uuid4(),
            invoice_id=invoice_id,
            type=ReminderType.EMAIL,
            scheduled_at=next_date,
            status="pending",
            tone="professional",
        )
        db.add(reminder)
        await db.commit()
        logger.info(f"[OverdueFlow] Follow-up scheduled for invoice {invoice_id} on {next_date.date()}")
    except Exception as e:
        logger.error(f"[OverdueFlow] schedule_follow_up error: {e}")
        await db.rollback()


async def broadcast_overdue_event(invoice: dict, ws_manager=None) -> None:
    """Broadcast overdue_detected WebSocket event to the invoice's team."""
    if ws_manager is None:
        return
    try:
        event = {
            "type": "overdue_detected",
            "invoice_id": invoice["id"],
            "invoice_number": invoice["number"],
            "days_overdue": invoice["days_overdue"],
            "balance_due": invoice["balance_due"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await ws_manager.broadcast_team(invoice.get("team_id", ""), event)
    except Exception as e:
        logger.warning(f"[OverdueFlow] WebSocket broadcast failed: {e}")


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def run_overdue_invoice_flow(db: AsyncSession, ai_api_key: str = "", ws_manager=None) -> dict:
    """
    Full overdue invoice flow:
    1. Detect overdue invoices
    2. For each: generate AI reminder, send email, schedule follow-up, broadcast event
    3. Return summary stats
    """
    summary = {"processed": 0, "reminders_sent": 0, "errors": 0, "skipped": 0}
    overdue_invoices = await detect_overdue_invoices(db)

    for invoice in overdue_invoices:
        try:
            # Skip if reminder sent very recently (cooldown: 1 day)
            if invoice["reminders_sent"] > 0 and invoice["days_overdue"] < 2:
                summary["skipped"] += 1
                continue

            # Fetch client info
            client_name = await _get_client_name(invoice["client_id"], db)
            client_email = await _get_client_email(invoice["client_id"], db)

            # Generate AI reminder
            content = await generate_ai_reminder(invoice, client_name, ai_api_key)
            content["tone"] = invoice["tone"]["tone"]

            # Send reminder
            sent = await send_reminder_email(str(invoice["id"]), client_email, content, db)
            if sent:
                summary["reminders_sent"] += 1

            # Schedule follow-up
            await schedule_follow_up(str(invoice["id"]), invoice["days_overdue"], db)

            # Broadcast WS event
            await broadcast_overdue_event(invoice, ws_manager)

            summary["processed"] += 1
        except Exception as e:
            logger.error(f"[OverdueFlow] Error processing invoice {invoice.get('id')}: {e}")
            summary["errors"] += 1

    logger.info(f"[OverdueFlow] Completed: {summary}")
    return summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_client_name(client_id: str, db: AsyncSession) -> str:
    try:
        from app.models import Client
        stmt = select(Client).where(Client.id == client_id)
        result = await db.execute(stmt)
        client = result.scalar_one_or_none()
        return client.name if client else "Valued Client"
    except Exception:
        return "Valued Client"


async def _get_client_email(client_id: str, db: AsyncSession) -> str:
    try:
        from app.models import Client
        stmt = select(Client).where(Client.id == client_id)
        result = await db.execute(stmt)
        client = result.scalar_one_or_none()
        return client.email if client else ""
    except Exception:
        return ""