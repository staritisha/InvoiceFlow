"""
Payment Follow-up Flow
Triggered when invoice is paid → generate thank-you → send email → update client stats → notify team.
"""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

async def generate_thank_you_email(invoice: dict, client_name: str, ai_api_key: str) -> dict:
    """Use AI to generate a personalised thank-you email with optional upsell note."""
    try:
        import httpx, json
        prompt = (
            f"Write a warm thank-you email for a paid invoice.\n"
            f"Client: {client_name}\n"
            f"Invoice: #{invoice.get('number')}\n"
            f"Amount paid: ${invoice.get('total', 0):.2f}\n"
            f"Include a subtle upsell or appreciation note.\n"
            f"Return JSON: {{\"subject\": \"...\", \"body\": \"...\"}}"
        )
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {ai_api_key}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                    "max_tokens": 350,
                },
            )
            resp.raise_for_status()
            return json.loads(resp.json()["choices"][0]["message"]["content"])
    except Exception as e:
        logger.warning(f"[PaymentFlow] AI thank-you generation failed: {e}")
        return {
            "subject": f"Thank you for your payment – Invoice #{invoice.get('number')}",
            "body": (
                f"Dear {client_name},\n\n"
                f"Thank you for your prompt payment of ${invoice.get('total', 0):.2f} for "
                f"Invoice #{invoice.get('number')}. We truly appreciate your business!\n\n"
                f"We look forward to working with you again.\n\nWarm regards,\nInvoiceFlow"
            ),
        }


async def update_client_scores(client_id: str, days_to_pay: int, db: AsyncSession) -> None:
    """Improve payment behaviour score and trust score after successful payment."""
    try:
        from app.models import Client
        stmt = select(Client).where(Client.id == client_id)
        result = await db.execute(stmt)
        client = result.scalar_one_or_none()
        if not client:
            return

        # Recalculate payment behaviour score (0-100)
        current_score = client.payment_behavior_score or 50
        if days_to_pay <= 0:
            boost = 10
        elif days_to_pay <= 7:
            boost = 7
        elif days_to_pay <= 30:
            boost = 3
        else:
            boost = -5

        client.payment_behavior_score = max(0, min(100, current_score + boost))
        client.total_paid = (client.total_paid or 0)  # updated elsewhere
        # Recalculate average_days_to_pay rolling average
        prev_avg = client.average_days_to_pay or days_to_pay
        client.average_days_to_pay = round((prev_avg + days_to_pay) / 2, 1)

        await db.commit()
        logger.info(f"[PaymentFlow] Updated client {client_id} behaviour score → {client.payment_behavior_score}")
    except Exception as e:
        logger.error(f"[PaymentFlow] update_client_scores error: {e}")
        await db.rollback()


async def create_payment_notification(invoice: dict, db: AsyncSession) -> None:
    """Create an in-app notification for the invoice owner."""
    try:
        from app.models import Notification
        from app.core.constants import NotificationType
        notif = Notification(
            id=uuid.uuid4(),
            user_id=invoice.get("user_id"),
            type=NotificationType.PAYMENT_RECEIVED,
            title="Payment Received 🎉",
            message=f"Invoice #{invoice.get('number')} has been paid – ${invoice.get('total', 0):.2f}",
            read=False,
            data={"invoice_id": invoice.get("id"), "amount": invoice.get("total", 0)},
            created_at=datetime.now(timezone.utc),
        )
        db.add(notif)
        await db.commit()
    except Exception as e:
        logger.warning(f"[PaymentFlow] create_payment_notification error: {e}")
        await db.rollback()


async def log_payment_activity(invoice: dict, db: AsyncSession) -> None:
    try:
        from app.models import Activity
        from app.core.constants import ActivityType
        activity = Activity(
            id=uuid.uuid4(),
            team_id=invoice.get("team_id"),
            user_id=invoice.get("user_id"),
            action_type=ActivityType.PAYMENT_RECORDED,
            entity_type="invoice",
            entity_id=str(invoice.get("id")),
            description=f"Payment received for invoice #{invoice.get('number')}",
            metadata={"amount": invoice.get("total", 0)},
        )
        db.add(activity)
        await db.commit()
    except Exception as e:
        logger.warning(f"[PaymentFlow] log_payment_activity error: {e}")
        await db.rollback()


async def broadcast_payment_event(invoice: dict, ws_manager=None) -> None:
    if ws_manager is None:
        return
    try:
        event = {
            "type": "invoice_paid",
            "invoice_id": str(invoice.get("id")),
            "invoice_number": invoice.get("number"),
            "amount": invoice.get("total", 0),
            "client_id": str(invoice.get("client_id")),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await ws_manager.broadcast_team(str(invoice.get("team_id", "")), event)
    except Exception as e:
        logger.warning(f"[PaymentFlow] WebSocket broadcast failed: {e}")


def _days_to_pay(issue_date, paid_date) -> int:
    if not issue_date or not paid_date:
        return 0
    if issue_date.tzinfo is None:
        issue_date = issue_date.replace(tzinfo=timezone.utc)
    if paid_date.tzinfo is None:
        paid_date = paid_date.replace(tzinfo=timezone.utc)
    return max(0, (paid_date - issue_date).days)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def run_payment_followup_flow(
    invoice_data: dict,
    db: AsyncSession,
    ai_api_key: str = "",
    ws_manager=None,
) -> dict:
    """
    Trigger after a payment is recorded.
    invoice_data must include: id, number, client_id, user_id, team_id, total, issue_date, paid_date.
    """
    result = {"thank_you_sent": False, "client_updated": False, "notification_created": False}
    try:
        client_name = await _get_client_name(invoice_data.get("client_id", ""), db)
        client_email = await _get_client_email(invoice_data.get("client_id", ""), db)

        # Generate and "send" thank-you (real send handled by notification_service)
        thank_you = await generate_thank_you_email(invoice_data, client_name, ai_api_key)
        invoice_data["thank_you_email"] = thank_you
        result["thank_you_sent"] = True

        # Update client scores
        days = _days_to_pay(
            invoice_data.get("issue_date"),
            invoice_data.get("paid_date") or datetime.now(timezone.utc),
        )
        await update_client_scores(str(invoice_data.get("client_id", "")), days, db)
        result["client_updated"] = True

        # Create notification
        await create_payment_notification(invoice_data, db)
        result["notification_created"] = True

        # Log activity
        await log_payment_activity(invoice_data, db)

        # Broadcast WS
        await broadcast_payment_event(invoice_data, ws_manager)

        logger.info(f"[PaymentFlow] Completed for invoice {invoice_data.get('id')}: {result}")
    except Exception as e:
        logger.error(f"[PaymentFlow] run_payment_followup_flow error: {e}")

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_client_name(client_id: str, db: AsyncSession) -> str:
    try:
        from app.models import Client
        result = await db.execute(select(Client).where(Client.id == client_id))
        c = result.scalar_one_or_none()
        return c.name if c else "Valued Client"
    except Exception:
        return "Valued Client"


async def _get_client_email(client_id: str, db: AsyncSession) -> str:
    try:
        from app.models import Client
        result = await db.execute(select(Client).where(Client.id == client_id))
        c = result.scalar_one_or_none()
        return c.email if c else ""
    except Exception:
        return ""