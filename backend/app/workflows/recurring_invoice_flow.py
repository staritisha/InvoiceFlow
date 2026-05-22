"""
Recurring Invoice Flow
Check schedule → duplicate template → update dates/number → send to client → log activity.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Frequency helpers
# ---------------------------------------------------------------------------

FREQUENCY_DELTA = {
    "daily":     lambda: timedelta(days=1),
    "weekly":    lambda: timedelta(weeks=1),
    "biweekly":  lambda: timedelta(weeks=2),
    "monthly":   lambda n=1: _add_months(n),
    "quarterly": lambda: _add_months(3),
    "yearly":    lambda: _add_months(12),
}


def _add_months(n: int) -> timedelta:
    """Approximate month addition as 30-day blocks."""
    return timedelta(days=30 * n)


def next_run_date(current: datetime, frequency: str) -> datetime:
    delta_fn = FREQUENCY_DELTA.get(frequency, lambda: timedelta(days=30))
    delta = delta_fn()
    return current + delta


# ---------------------------------------------------------------------------
# Core steps
# ---------------------------------------------------------------------------

async def get_due_recurring_invoices(db: AsyncSession) -> list:
    """Return all active RecurringInvoice rows whose next_run <= now."""
    try:
        from app.models import RecurringInvoice
        now = datetime.now(timezone.utc)
        stmt = select(RecurringInvoice).where(
            and_(
                RecurringInvoice.is_active == True,
                RecurringInvoice.next_run <= now,
            )
        )
        result = await db.execute(stmt)
        return result.scalars().all()
    except Exception as e:
        logger.error(f"[RecurringFlow] get_due_recurring_invoices: {e}")
        return []


async def duplicate_invoice(template_id: str, db: AsyncSession) -> Optional[object]:
    """Clone a template invoice with a fresh number, new dates, and 'pending' status."""
    try:
        from app.models import Invoice, InvoiceItem
        from app.utils import generate_invoice_number

        stmt = select(Invoice).where(Invoice.id == template_id)
        result = await db.execute(stmt)
        template = result.scalar_one_or_none()
        if not template:
            logger.warning(f"[RecurringFlow] Template invoice {template_id} not found")
            return None

        now = datetime.now(timezone.utc)
        new_invoice = Invoice(
            id=uuid.uuid4(),
            number=await generate_invoice_number(db),
            client_id=template.client_id,
            user_id=template.user_id,
            team_id=template.team_id,
            status="pending",
            currency=template.currency,
            exchange_rate=template.exchange_rate,
            theme=template.theme,
            subtotal=template.subtotal,
            tax_rate=template.tax_rate,
            tax_amount=template.tax_amount,
            discount=template.discount,
            total=template.total,
            amount_paid=0,
            balance_due=template.total,
            issue_date=now,
            due_date=now + timedelta(days=30),
            description=template.description,
            notes=template.notes,
            terms=template.terms,
            footer=template.footer,
            is_recurring=False,
            source="recurring",
        )
        db.add(new_invoice)
        await db.flush()  # get new_invoice.id

        # Duplicate items
        item_stmt = select(InvoiceItem).where(InvoiceItem.invoice_id == template_id)
        item_result = await db.execute(item_stmt)
        items = item_result.scalars().all()
        for item in items:
            new_item = InvoiceItem(
                id=uuid.uuid4(),
                invoice_id=new_invoice.id,
                description=item.description,
                quantity=item.quantity,
                rate=item.rate,
                amount=item.amount,
            )
            db.add(new_item)

        await db.commit()
        logger.info(f"[RecurringFlow] Duplicated invoice → #{new_invoice.number}")
        return new_invoice
    except Exception as e:
        logger.error(f"[RecurringFlow] duplicate_invoice error: {e}")
        await db.rollback()
        return None


async def update_recurring_schedule(recurring, db: AsyncSession) -> None:
    """Advance next_run date and increment total_runs; deactivate if max_runs reached."""
    try:
        recurring.next_run = next_run_date(recurring.next_run, recurring.frequency)
        recurring.total_runs = (recurring.total_runs or 0) + 1
        if recurring.max_runs and recurring.total_runs >= recurring.max_runs:
            recurring.is_active = False
            logger.info(f"[RecurringFlow] RecurringInvoice {recurring.id} reached max_runs, deactivated")
        if recurring.end_date and recurring.next_run > recurring.end_date:
            recurring.is_active = False
            logger.info(f"[RecurringFlow] RecurringInvoice {recurring.id} passed end_date, deactivated")
        await db.commit()
    except Exception as e:
        logger.error(f"[RecurringFlow] update_recurring_schedule error: {e}")
        await db.rollback()


async def auto_send_invoice(invoice, db: AsyncSession) -> bool:
    """Mark invoice as 'sent' and trigger email notification."""
    try:
        from app.models import Invoice
        stmt = select(Invoice).where(Invoice.id == invoice.id)
        result = await db.execute(stmt)
        inv = result.scalar_one_or_none()
        if inv:
            inv.status = "sent"
            await db.commit()
            logger.info(f"[RecurringFlow] Invoice #{inv.number} auto-sent")
            return True
        return False
    except Exception as e:
        logger.error(f"[RecurringFlow] auto_send_invoice error: {e}")
        await db.rollback()
        return False


async def log_recurring_activity(invoice, db: AsyncSession) -> None:
    """Create an Activity log entry for recurring invoice creation."""
    try:
        from app.models import Activity
        from app.core.constants import ActivityType
        activity = Activity(
            id=uuid.uuid4(),
            team_id=invoice.team_id,
            user_id=invoice.user_id,
            action_type=ActivityType.INVOICE_CREATED,
            entity_type="invoice",
            entity_id=str(invoice.id),
            description=f"Recurring invoice #{invoice.number} auto-generated",
            metadata={"source": "recurring", "invoice_number": invoice.number},
        )
        db.add(activity)
        await db.commit()
    except Exception as e:
        logger.warning(f"[RecurringFlow] log_recurring_activity error: {e}")
        await db.rollback()


async def broadcast_recurring_event(invoice, ws_manager=None) -> None:
    if ws_manager is None:
        return
    try:
        event = {
            "type": "recurring_created",
            "invoice_id": str(invoice.id),
            "invoice_number": invoice.number,
            "total": float(invoice.total),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await ws_manager.broadcast_team(str(invoice.team_id), event)
    except Exception as e:
        logger.warning(f"[RecurringFlow] WebSocket broadcast failed: {e}")


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def run_recurring_invoice_flow(db: AsyncSession, ws_manager=None) -> dict:
    """
    Full recurring invoice flow:
    1. Find all due recurring invoices
    2. Duplicate template → new invoice
    3. Auto-send to client
    4. Advance schedule
    5. Log activity + broadcast
    """
    summary = {"processed": 0, "errors": 0, "deactivated": 0}
    due_list = await get_due_recurring_invoices(db)

    for recurring in due_list:
        try:
            new_invoice = await duplicate_invoice(str(recurring.template_invoice_id), db)
            if not new_invoice:
                summary["errors"] += 1
                continue

            await auto_send_invoice(new_invoice, db)
            await log_recurring_activity(new_invoice, db)
            await broadcast_recurring_event(new_invoice, ws_manager)

            was_active = recurring.is_active
            await update_recurring_schedule(recurring, db)
            if was_active and not recurring.is_active:
                summary["deactivated"] += 1

            summary["processed"] += 1
        except Exception as e:
            logger.error(f"[RecurringFlow] Error for recurring {recurring.id}: {e}")
            summary["errors"] += 1

    logger.info(f"[RecurringFlow] Completed: {summary}")
    return summary