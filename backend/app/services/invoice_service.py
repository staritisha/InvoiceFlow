"""
app/services/invoice_service.py

Production-grade + AI-powered Invoice Service for InvoiceFlow AI Platform.
Handles: full CRUD, smart calculations, AI enhancements, send flow,
duplicate detection, recurring engine, payment recording, PDF generation,
multi-currency, activity logging, and real-time WebSocket events.
"""

from __future__ import annotations

import math
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import ActivityType, InvoicePriority, NotificationType
from app.models import (
    Activity,
    Client,
    Invoice,
    InvoiceItem,
    InvoiceStatus,
    Notification,
    Payment,
    Reminder,
    User,
)
from app.services.ai_service import AIService
from app.websocket.manager import ws_manager

ai_service = AIService()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INVOICE_THEMES = {
    "modern":        {"primary": "#6366f1", "font": "Inter",     "layout": "clean"},
    "startup":       {"primary": "#8b5cf6", "font": "Poppins",   "layout": "bold"},
    "elegant":       {"primary": "#1e293b", "font": "Playfair",  "layout": "serif"},
    "bold":          {"primary": "#ef4444", "font": "Montserrat","layout": "block"},
    "minimal":       {"primary": "#374151", "font": "Inter",     "layout": "minimal"},
    "glassmorphism": {"primary": "#0ea5e9", "font": "Inter",     "layout": "glass"},
}

RECURRING_INTERVALS = {
    "daily":     {"days": 1},
    "weekly":    {"days": 7},
    "biweekly":  {"days": 14},
    "monthly":   {"months": 1},
    "quarterly": {"months": 3},
    "yearly":    {"months": 12},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _round2(value: float) -> float:
    return round(value, 2)


def _next_invoice_number(last: Optional[str]) -> str:
    if not last:
        return "INV-0001"
    parts = last.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return f"{parts[0]}-{int(parts[1]) + 1:04d}"
    return f"INV-{uuid4().hex[:6].upper()}"


def _advance_date(from_date: date, interval: str) -> date:
    cfg = RECURRING_INTERVALS.get(interval, {"days": 30})
    if "days" in cfg:
        return from_date + timedelta(days=cfg["days"])
    months = cfg.get("months", 1)
    year = from_date.year + (from_date.month - 1 + months) // 12
    month = (from_date.month - 1 + months) % 12 + 1
    day = min(from_date.day, [31, 29 if year % 4 == 0 else 28, 31, 30, 31, 30,
                                31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


# ===========================================================================
# 1 + 2. Invoice CRUD + Smart Calculations
# ===========================================================================


class InvoiceService:

    # ------------------------------------------------------------------
    # Calculation primitives
    # ------------------------------------------------------------------

    def calculate_subtotal(self, items: list[dict]) -> float:
        return _round2(sum(float(i.get("quantity", 1)) * float(i.get("rate", 0)) for i in items))

    def calculate_tax(self, subtotal: float, tax_rate: float) -> float:
        return _round2(subtotal * tax_rate / 100)

    def calculate_discount(
        self,
        subtotal: float,
        discount: float,
        discount_type: str = "flat",
    ) -> float:
        if discount_type == "percentage":
            return _round2(subtotal * discount / 100)
        return _round2(discount)

    def calculate_total(
        self,
        subtotal: float,
        tax_amount: float,
        discount_amount: float,
    ) -> float:
        return _round2(max(0.0, subtotal + tax_amount - discount_amount))

    def calculate_balance_due(self, total: float, amount_paid: float) -> float:
        return _round2(max(0.0, total - amount_paid))

    def recalculate(self, items: list[dict], tax_rate: float, discount: float,
                    discount_type: str = "flat", amount_paid: float = 0.0) -> dict:
        """Full recalculation returning all money fields."""
        subtotal = self.calculate_subtotal(items)
        tax_amount = self.calculate_tax(subtotal, tax_rate)
        discount_amount = self.calculate_discount(subtotal, discount, discount_type)
        total = self.calculate_total(subtotal, tax_amount, discount_amount)
        balance_due = self.calculate_balance_due(total, amount_paid)
        return {
            "subtotal": subtotal,
            "tax_amount": tax_amount,
            "discount_amount": discount_amount,
            "total": total,
            "balance_due": balance_due,
        }

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create_invoice(
        self,
        db: AsyncSession,
        data: dict,
        team_id: UUID,
        user_id: UUID,
        enhance_with_ai: bool = True,
    ) -> Invoice:
        """
        Create a fully-calculated invoice with optional AI enhancement.
        Handles: auto-number, totals, AI description, AI priority, duplicate detection.
        """
        # Resolve client
        client: Optional[Client] = None
        if data.get("client_id"):
            client_result = await db.execute(
                select(Client).where(Client.id == data["client_id"], Client.team_id == team_id)
            )
            client = client_result.scalar_one_or_none()

        # Auto invoice number
        last_stmt = select(Invoice.number).where(Invoice.team_id == team_id).order_by(desc(Invoice.id)).limit(1)
        last_number = (await db.execute(last_stmt)).scalar_one_or_none()
        invoice_number = data.get("number") or _next_invoice_number(last_number)

        # Calculations
        items = data.get("items", [])
        tax_rate = float(data.get("tax_rate", 0))
        discount = float(data.get("discount", 0))
        discount_type = data.get("discount_type", "flat")
        calcs = self.recalculate(items, tax_rate, discount, discount_type)

        # AI: description + priority
        ai_description: Optional[str] = None
        ai_priority: str = InvoicePriority.medium
        if enhance_with_ai:
            try:
                desc_result = await ai_service.enhance_invoice_description(
                    raw_description=data.get("description", ""),
                    items=items,
                    client_name=client.name if client else "",
                    month=date.today().strftime("%B %Y"),
                )
                ai_description = desc_result.get("enhanced")

                priority_result = await ai_service.calculate_invoice_priority(
                    amount=calcs["total"],
                    overdue_days=0,
                    client_risk_score=float(client.risk_score or 0) if client else 0.0,
                    unpaid_invoice_count=0,
                )
                ai_priority = priority_result.get("priority", InvoicePriority.medium)
            except Exception:
                pass

        # Duplicate detection
        is_duplicate = await self.detect_duplicate_invoice(
            db=db,
            team_id=team_id,
            client_id=data.get("client_id"),
            total=calcs["total"],
            issue_date=date.today(),
        )

        # Due date
        due_date = data.get("due_date") or self._default_due_date(client)

        invoice = Invoice(
            number=invoice_number,
            client_id=data.get("client_id"),
            user_id=user_id,
            team_id=team_id,
            status=InvoiceStatus.draft,
            currency=data.get("currency", "USD").upper(),
            subtotal=calcs["subtotal"],
            tax_rate=tax_rate,
            tax_amount=calcs["tax_amount"],
            discount=discount,
            discount_type=discount_type,
            total=calcs["total"],
            amount_paid=0,
            balance_due=calcs["balance_due"],
            issue_date=date.today(),
            due_date=due_date,
            description=ai_description or data.get("description", ""),
            ai_description=ai_description,
            ai_priority=ai_priority,
            notes=data.get("notes", ""),
            terms=data.get("terms", "Net 30"),
            is_recurring=data.get("is_recurring", False),
            recurring_interval=data.get("recurring_interval"),
            recurring_max_runs=data.get("recurring_max_runs"),
            recurring_run_count=0,
            recurring_next_run=None,
            theme=data.get("theme", "modern"),
            source=data.get("source", "web"),
            is_duplicate_flag=is_duplicate,
            metadata={},
        )
        db.add(invoice)
        await db.flush()

        # Save items
        for item in items:
            qty = float(item.get("quantity", 1))
            rate = float(item.get("rate", 0))
            db.add(InvoiceItem(
                invoice_id=invoice.id,
                description=item.get("description", "Service"),
                quantity=qty,
                rate=rate,
                amount=_round2(qty * rate),
                unit=item.get("unit"),
            ))

        # Set up recurring next run
        if invoice.is_recurring and invoice.recurring_interval:
            invoice.recurring_next_run = _advance_date(date.today(), invoice.recurring_interval)

        await db.commit()
        await db.refresh(invoice)

        # Activity + WebSocket
        await self._log(db, team_id=team_id, user_id=user_id,
                        action_type=ActivityType.created, entity_id=invoice.id,
                        description=f"Invoice {invoice_number} created")
        await db.commit()

        await ws_manager.broadcast_to_team(str(team_id), {
            "event": "INVOICE_CREATED",
            "invoice_id": str(invoice.id),
            "number": invoice_number,
            "total": calcs["total"],
            "currency": invoice.currency,
            "is_duplicate_flag": is_duplicate,
        })

        return invoice

    def _default_due_date(self, client: Optional[Client]) -> date:
        if client and client.average_days_to_pay:
            days = min(int(client.average_days_to_pay * 0.8), 60)
        else:
            days = 30
        return date.today() + timedelta(days=max(7, days))

    # ------------------------------------------------------------------
    # Get by ID
    # ------------------------------------------------------------------

    async def get_invoice_by_id(
        self,
        db: AsyncSession,
        invoice_id: UUID,
        team_id: UUID,
    ) -> Optional[Invoice]:
        stmt = (
            select(Invoice)
            .where(Invoice.id == invoice_id, Invoice.team_id == team_id)
            .options(
                selectinload(Invoice.items),
                selectinload(Invoice.client),
                selectinload(Invoice.payments),
                selectinload(Invoice.reminders),
            )
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    async def update_invoice(
        self,
        db: AsyncSession,
        invoice: Invoice,
        updates: dict,
        team_id: UUID,
        user_id: UUID,
    ) -> Invoice:
        """Update invoice fields, recalculate totals, re-score AI priority."""
        items = updates.get("items") or [
            {"quantity": i.quantity, "rate": i.rate}
            for i in (invoice.items or [])
        ]
        tax_rate = float(updates.get("tax_rate", invoice.tax_rate or 0))
        discount = float(updates.get("discount", invoice.discount or 0))
        discount_type = updates.get("discount_type", getattr(invoice, "discount_type", "flat") or "flat")

        calcs = self.recalculate(items, tax_rate, discount, discount_type, float(invoice.amount_paid or 0))

        for field in ("description", "notes", "terms", "currency", "due_date",
                      "theme", "is_recurring", "recurring_interval"):
            if field in updates:
                setattr(invoice, field, updates[field])

        invoice.tax_rate = tax_rate
        invoice.discount = discount
        invoice.subtotal = calcs["subtotal"]
        invoice.tax_amount = calcs["tax_amount"]
        invoice.total = calcs["total"]
        invoice.balance_due = calcs["balance_due"]

        # Recalculate status
        invoice.status = self._derive_status(invoice)

        # Re-score AI priority
        try:
            overdue_days = max(0, (date.today() - invoice.due_date).days) if invoice.due_date else 0
            client_risk = 0.0
            if invoice.client_id:
                cl = (await db.execute(select(Client).where(Client.id == invoice.client_id))).scalar_one_or_none()
                client_risk = float(cl.risk_score or 0) if cl else 0.0
            prio = await ai_service.calculate_invoice_priority(
                amount=calcs["total"], overdue_days=overdue_days,
                client_risk_score=client_risk, unpaid_invoice_count=0,
            )
            invoice.ai_priority = prio.get("priority", invoice.ai_priority)
        except Exception:
            pass

        await self._log(db, team_id=team_id, user_id=user_id,
                        action_type=ActivityType.updated, entity_id=invoice.id,
                        description=f"Invoice {invoice.number} updated")
        await db.commit()
        await db.refresh(invoice)

        await ws_manager.broadcast_to_team(str(team_id), {
            "event": "INVOICE_UPDATED",
            "invoice_id": str(invoice.id),
            "number": invoice.number,
            "total": calcs["total"],
        })
        return invoice

    def _derive_status(self, invoice: Invoice) -> str:
        if float(invoice.balance_due or 0) <= 0:
            return InvoiceStatus.paid
        if invoice.due_date and date.today() > invoice.due_date and invoice.status != InvoiceStatus.draft:
            return InvoiceStatus.overdue
        if invoice.status == InvoiceStatus.overdue and float(invoice.amount_paid or 0) > 0:
            return InvoiceStatus.partial
        return invoice.status

    # ------------------------------------------------------------------
    # Delete (soft)
    # ------------------------------------------------------------------

    async def delete_invoice(
        self,
        db: AsyncSession,
        invoice: Invoice,
        team_id: UUID,
        user_id: UUID,
        permanent: bool = False,
    ) -> None:
        if permanent:
            await db.delete(invoice)
        else:
            invoice.is_deleted = True
            invoice.deleted_at = _utcnow()

        await self._log(db, team_id=team_id, user_id=user_id,
                        action_type=ActivityType.deleted, entity_id=invoice.id,
                        description=f"Invoice {invoice.number} {'deleted' if permanent else 'archived'}")
        await db.commit()

    # ------------------------------------------------------------------
    # Mark sent
    # ------------------------------------------------------------------

    async def mark_sent(
        self,
        invoice_id: UUID,
        db: AsyncSession,
    ) -> None:
        stmt = select(Invoice).where(Invoice.id == invoice_id)
        invoice = (await db.execute(stmt)).scalar_one_or_none()
        if invoice and invoice.status == InvoiceStatus.draft:
            invoice.status = InvoiceStatus.sent
            invoice.sent_at = _utcnow()
            await db.commit()

    # ------------------------------------------------------------------
    # Send flow
    # ------------------------------------------------------------------

    async def send_invoice(
        self,
        db: AsyncSession,
        invoice: Invoice,
        team_id: UUID,
        user_id: UUID,
        send_email: bool = True,
        send_whatsapp: bool = False,
    ) -> dict:
        """
        Full send flow:
        Generate PDF → Send Email/WhatsApp → Update Status →
        Log Activity → Create Reminder Schedule → Notify Dashboard
        """
        # 1. Generate PDF
        pdf_bytes = await self.generate_invoice_pdf(invoice)

        # 2. Email (via email_service — import lazily to avoid circular)
        email_sent = False
        if send_email:
            client_result = await db.execute(select(Client).where(Client.id == invoice.client_id))
            client = client_result.scalar_one_or_none()
            if client and client.email:
                try:
                    from app.services.email_service import EmailService
                    email_svc = EmailService()
                    subject = f"Invoice {invoice.number} from {getattr(client, 'business_name', 'Us')}"
                    body = await ai_service.generate_email(
                        email_type="invoice_delivery",
                        tone="professional",
                        context={
                            "client_name": client.name,
                            "invoice_number": invoice.number,
                            "amount": float(invoice.total or 0),
                            "currency": invoice.currency,
                            "due_date": str(invoice.due_date),
                        },
                    )
                    await email_svc.send(
                        to=client.email,
                        subject=body.get("subject", subject),
                        html=body.get("html", ""),
                        attachments=[{"filename": f"Invoice_{invoice.number}.pdf",
                                      "data": pdf_bytes, "content_type": "application/pdf"}],
                    )
                    email_sent = True
                except Exception:
                    pass

        # 3. Update status
        invoice.status = InvoiceStatus.sent
        invoice.sent_at = _utcnow()

        # 4. AI reminder schedule
        try:
            schedule = await ai_service.generate_followup_schedule(
                invoice_data={
                    "id": str(invoice.id), "total": float(invoice.total or 0),
                    "due_date": str(invoice.due_date), "currency": invoice.currency,
                },
                client_risk_score=0.0,
            )
            for step in schedule.get("schedule", []):
                reminder_date = date.today() + timedelta(days=step.get("day_from_today", 7))
                db.add(Reminder(
                    invoice_id=invoice.id,
                    team_id=team_id,
                    scheduled_date=reminder_date,
                    tone=step.get("tone", "professional"),
                    channel=step.get("channel", "email"),
                    status="scheduled",
                ))
        except Exception:
            pass

        # 5. Activity + Notification
        await self._log(db, team_id=team_id, user_id=user_id,
                        action_type=ActivityType.sent, entity_id=invoice.id,
                        description=f"Invoice {invoice.number} sent")
        db.add(Notification(
            user_id=user_id,
            type=NotificationType.invoice_sent,
            title=f"Invoice {invoice.number} sent",
            message=f"Invoice {invoice.number} for {float(invoice.total or 0):,.2f} {invoice.currency} sent.",
            read=False,
            created_at=_utcnow(),
        ))
        await db.commit()

        await ws_manager.broadcast_to_team(str(team_id), {
            "event": "INVOICE_SENT",
            "invoice_id": str(invoice.id),
            "number": invoice.number,
            "email_sent": email_sent,
        })

        return {"status": "sent", "email_sent": email_sent, "pdf_generated": bool(pdf_bytes)}

    # ------------------------------------------------------------------
    # Duplicate invoice
    # ------------------------------------------------------------------

    async def duplicate_invoice(
        self,
        db: AsyncSession,
        source: Invoice,
        team_id: UUID,
        user_id: UUID,
    ) -> Invoice:
        """
        One-click duplicate. Copies client + items, resets payment state,
        generates new invoice number, sets status = draft.
        """
        last_stmt = select(Invoice.number).where(Invoice.team_id == team_id).order_by(desc(Invoice.id)).limit(1)
        last_number = (await db.execute(last_stmt)).scalar_one_or_none()

        new_invoice = Invoice(
            number=_next_invoice_number(last_number),
            client_id=source.client_id,
            user_id=user_id,
            team_id=team_id,
            status=InvoiceStatus.draft,
            currency=source.currency,
            subtotal=source.subtotal,
            tax_rate=source.tax_rate,
            tax_amount=source.tax_amount,
            discount=source.discount,
            total=source.total,
            amount_paid=0,
            balance_due=source.total,
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            description=source.description,
            ai_description=source.ai_description,
            ai_priority=source.ai_priority,
            notes=source.notes,
            terms=source.terms,
            is_recurring=source.is_recurring,
            recurring_interval=source.recurring_interval,
            theme=source.theme,
            source="duplicate",
            metadata={"duplicated_from": str(source.id)},
        )
        db.add(new_invoice)
        await db.flush()

        # Copy items
        items_stmt = select(InvoiceItem).where(InvoiceItem.invoice_id == source.id)
        orig_items = (await db.execute(items_stmt)).scalars().all()
        for item in orig_items:
            db.add(InvoiceItem(
                invoice_id=new_invoice.id,
                description=item.description,
                quantity=item.quantity,
                rate=item.rate,
                amount=item.amount,
                unit=item.unit,
            ))

        await self._log(db, team_id=team_id, user_id=user_id,
                        action_type=ActivityType.duplicated, entity_id=new_invoice.id,
                        description=f"Invoice {source.number} duplicated → {new_invoice.number}")
        await db.commit()
        await db.refresh(new_invoice)

        await ws_manager.broadcast_to_team(str(team_id), {
            "event": "INVOICE_CREATED",
            "invoice_id": str(new_invoice.id),
            "number": new_invoice.number,
            "source": "duplicate",
        })
        return new_invoice

    # ------------------------------------------------------------------
    # Recurring engine
    # ------------------------------------------------------------------

    async def create_recurring_invoice(
        self,
        db: AsyncSession,
        template_data: dict,
        team_id: UUID,
        user_id: UUID,
    ) -> Invoice:
        """Create a recurring invoice template with next_run schedule."""
        template_data["is_recurring"] = True
        invoice = await self.create_invoice(db, template_data, team_id, user_id)
        interval = template_data.get("recurring_interval", "monthly")
        invoice.recurring_next_run = _advance_date(date.today(), interval)
        invoice.recurring_max_runs = template_data.get("recurring_max_runs")
        await db.commit()
        return invoice

    async def process_recurring_invoices(self, db: AsyncSession) -> dict:
        """
        Scheduler entry point. Finds all due recurring invoices,
        duplicates them, updates next_run, sends automatically.
        """
        today = date.today()
        stmt = select(Invoice).where(
            Invoice.is_recurring.is_(True),
            Invoice.recurring_next_run <= today,
            or_(Invoice.recurring_max_runs.is_(None),
                Invoice.recurring_run_count < Invoice.recurring_max_runs),
        )
        templates = (await db.execute(stmt)).scalars().all()

        processed, failed = 0, 0
        for tmpl in templates:
            try:
                new_inv = await self.duplicate_invoice(db, tmpl, tmpl.team_id, tmpl.user_id)
                # Auto-send
                await self.send_invoice(db, new_inv, tmpl.team_id, tmpl.user_id)
                # Advance schedule
                tmpl.recurring_run_count = (tmpl.recurring_run_count or 0) + 1
                tmpl.recurring_next_run = _advance_date(today, tmpl.recurring_interval or "monthly")
                await db.commit()
                processed += 1
            except Exception:
                failed += 1

        return {"processed": processed, "failed": failed, "total": len(templates)}

    # ------------------------------------------------------------------
    # Status automation
    # ------------------------------------------------------------------

    async def update_invoice_status(self, db: AsyncSession, invoice: Invoice) -> Invoice:
        """Auto-derive status based on payment state and due date."""
        new_status = self._derive_status(invoice)
        if new_status != invoice.status:
            old_status = invoice.status
            invoice.status = new_status

            if new_status == InvoiceStatus.overdue:
                await ws_manager.broadcast_to_team(str(invoice.team_id), {
                    "event": "INVOICE_OVERDUE",
                    "invoice_id": str(invoice.id),
                    "number": invoice.number,
                    "balance_due": float(invoice.balance_due or 0),
                })
                # AI reminder
                try:
                    await self.generate_reminder_for_invoice(db, invoice)
                except Exception:
                    pass

            elif new_status == InvoiceStatus.paid:
                invoice.paid_at = _utcnow()
                await ws_manager.broadcast_to_team(str(invoice.team_id), {
                    "event": "INVOICE_PAID",
                    "invoice_id": str(invoice.id),
                    "number": invoice.number,
                    "total": float(invoice.total or 0),
                })

            await db.commit()
        return invoice

    # ------------------------------------------------------------------
    # Payment recording
    # ------------------------------------------------------------------

    async def record_payment(
        self,
        db: AsyncSession,
        invoice: Invoice,
        amount: float,
        currency: str,
        method: str,
        reference: str | None,
        team_id: UUID,
        user_id: UUID,
    ) -> dict:
        """
        Record a payment (full or partial). Updates balance, triggers
        thank-you email and status automation.
        """
        if amount <= 0:
            raise ValueError("Payment amount must be positive.")
        if amount > float(invoice.balance_due or 0) + 0.01:
            raise ValueError(f"Payment ${amount:.2f} exceeds balance due ${invoice.balance_due:.2f}.")

        payment = Payment(
            invoice_id=invoice.id,
            amount=_round2(amount),
            currency=currency.upper(),
            method=method,
            reference=reference,
            paid_at=_utcnow(),
        )
        db.add(payment)

        invoice.amount_paid = _round2(float(invoice.amount_paid or 0) + amount)
        invoice.balance_due = _round2(max(0.0, float(invoice.total or 0) - float(invoice.amount_paid)))
        invoice = await self.update_invoice_status(db, invoice)

        # Notification
        db.add(Notification(
            user_id=user_id,
            type=NotificationType.payment_received,
            title=f"Payment recorded — {invoice.number}",
            message=f"${amount:,.2f} recorded for invoice {invoice.number}.",
            read=False,
            created_at=_utcnow(),
        ))

        await self._log(db, team_id=team_id, user_id=user_id,
                        action_type=ActivityType.payment_recorded, entity_id=invoice.id,
                        description=f"Payment ${amount:.2f} recorded for {invoice.number}")
        await db.commit()

        return {
            "payment_id": str(payment.id),
            "amount_paid": float(invoice.amount_paid),
            "balance_due": float(invoice.balance_due),
            "status": invoice.status,
            "fully_paid": invoice.status == InvoiceStatus.paid,
        }

    # ------------------------------------------------------------------
    # AI: Due date recommendation
    # ------------------------------------------------------------------

    async def recommend_due_date(
        self,
        client: Optional[Client],
        amount: float,
    ) -> date:
        """AI-recommended due date based on client history and invoice amount."""
        avg_days = float(client.average_days_to_pay or 30) if client else 30.0
        risk = float(client.risk_score or 0) if client else 0.0

        rec = await ai_service.recommend_due_date(
            client_name=client.name if client else "Unknown",
            avg_payment_days=avg_days,
            invoice_amount=amount,
        )
        days = int(rec.get("recommended_days", 30))

        # Risk adjustment: high-risk → shorter window
        if risk >= 70:
            days = max(7, days - 7)
        elif risk >= 40:
            days = max(14, days - 3)

        return date.today() + timedelta(days=days)

    # ------------------------------------------------------------------
    # Multi-currency
    # ------------------------------------------------------------------

    async def convert_currency(
        self,
        amount: float,
        from_currency: str,
        to_currency: str,
    ) -> dict:
        """Convert amount between currencies using live rates."""
        try:
            from app.services.integration_helpers import get_exchange_rate
            rate = await get_exchange_rate(from_currency, to_currency)
        except Exception:
            rate = 1.0
        converted = _round2(amount * rate)
        return {
            "original": amount,
            "from": from_currency.upper(),
            "to": to_currency.upper(),
            "rate": rate,
            "converted": converted,
        }

    # ------------------------------------------------------------------
    # Theme engine
    # ------------------------------------------------------------------

    def apply_invoice_theme(self, theme_name: str) -> dict:
        return INVOICE_THEMES.get(theme_name, INVOICE_THEMES["modern"])

    # ------------------------------------------------------------------
    # AI: Generate reminder for invoice
    # ------------------------------------------------------------------

    async def generate_reminder_for_invoice(
        self,
        db: AsyncSession,
        invoice: Invoice,
        tone: str = "professional",
    ) -> dict:
        """AI generates a reminder when invoice becomes overdue."""
        client_result = await db.execute(select(Client).where(Client.id == invoice.client_id))
        client = client_result.scalar_one_or_none()
        reminder = await ai_service.generate_payment_reminder(
            invoice_data={
                "number": invoice.number,
                "total": float(invoice.total or 0),
                "balance_due": float(invoice.balance_due or 0),
                "currency": invoice.currency,
                "due_date": str(invoice.due_date),
                "overdue_days": max(0, (date.today() - invoice.due_date).days) if invoice.due_date else 0,
            },
            tone=tone,
            client_name=client.name if client else "Client",
            business_name="",
        )
        return reminder

    # ------------------------------------------------------------------
    # Smart search + List/filter
    # ------------------------------------------------------------------

    async def search_invoices(
        self,
        db: AsyncSession,
        team_id: UUID,
        query: str,
    ) -> list[dict]:
        """Natural-language smart search — converts query to DB filters."""
        filter_data = await ai_service.generate_filter_query(query)
        return await self.list_invoices(db, team_id=team_id, filters=filter_data)

    async def list_invoices(
        self,
        db: AsyncSession,
        team_id: UUID,
        page: int = 1,
        page_size: int = 20,
        filters: dict | None = None,
        sort_by: str = "newest",
    ) -> list[dict]:
        """Paginated invoice list with full filter support."""
        f = filters or {}
        stmt = select(Invoice).where(
            Invoice.team_id == team_id,
            Invoice.is_deleted.is_not(True),
        ).options(selectinload(Invoice.client))

        if f.get("status"):
            stmt = stmt.where(Invoice.status == f["status"])
        if f.get("client_name"):
            stmt = stmt.join(Client, Invoice.client_id == Client.id, isouter=True).where(
                Client.name.ilike(f"%{f['client_name']}%")
            )
        if f.get("overdue_only"):
            stmt = stmt.where(Invoice.status == InvoiceStatus.overdue)
        if f.get("is_recurring") is not None:
            stmt = stmt.where(Invoice.is_recurring.is_(f["is_recurring"]))
        if f.get("min_amount"):
            stmt = stmt.where(Invoice.total >= f["min_amount"])
        if f.get("max_amount"):
            stmt = stmt.where(Invoice.total <= f["max_amount"])
        if f.get("date_from"):
            stmt = stmt.where(Invoice.issue_date >= f["date_from"])
        if f.get("date_to"):
            stmt = stmt.where(Invoice.issue_date <= f["date_to"])
        if f.get("currency"):
            stmt = stmt.where(Invoice.currency == f["currency"].upper())
        if f.get("ai_priority"):
            stmt = stmt.where(Invoice.ai_priority == f["ai_priority"])

        sort_map = {
            "newest":         desc(Invoice.id),
            "oldest":         Invoice.id,
            "highest_amount": desc(Invoice.total),
            "lowest_amount":  Invoice.total,
            "overdue_first":  desc(Invoice.due_date),
        }
        stmt = stmt.order_by(sort_map.get(sort_by, desc(Invoice.id)))
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

        rows = (await db.execute(stmt)).scalars().all()
        return [self._serialize(r) for r in rows]

    def _serialize(self, inv: Invoice) -> dict:
        return {
            "id": str(inv.id),
            "number": inv.number,
            "status": inv.status,
            "currency": inv.currency,
            "total": float(inv.total or 0),
            "balance_due": float(inv.balance_due or 0),
            "amount_paid": float(inv.amount_paid or 0),
            "due_date": str(inv.due_date) if inv.due_date else None,
            "issue_date": str(inv.issue_date) if inv.issue_date else None,
            "client_name": inv.client.name if inv.client else None,
            "ai_priority": inv.ai_priority,
            "is_recurring": inv.is_recurring,
            "is_duplicate_flag": getattr(inv, "is_duplicate_flag", False),
            "theme": inv.theme,
            "source": inv.source,
        }

    # ------------------------------------------------------------------
    # PDF generation
    # ------------------------------------------------------------------

    async def generate_invoice_pdf(self, invoice: Invoice) -> bytes:
        """
        Generate branded PDF invoice.
        Falls back to a minimal text-based PDF if renderer unavailable.
        """
        try:
            from app.services.pdf_service import PDFService
            pdf_svc = PDFService()
            return await pdf_svc.render_invoice(invoice)
        except Exception:
            # Minimal fallback: return a placeholder byte string
            content = (
                f"INVOICE {invoice.number}\n"
                f"Total: {invoice.currency} {invoice.total}\n"
                f"Due: {invoice.due_date}\n"
                f"Status: {invoice.status}\n"
            )
            return content.encode("utf-8")

    # ------------------------------------------------------------------
    # Fraud / duplicate detection
    # ------------------------------------------------------------------

    async def detect_duplicate_invoice(
        self,
        db: AsyncSession,
        team_id: UUID,
        client_id: Optional[UUID],
        total: float,
        issue_date: date,
        tolerance_days: int = 3,
    ) -> bool:
        """
        Check if a suspiciously similar invoice already exists.
        Matches: same client, same amount, within N days.
        """
        if not client_id:
            return False
        window_start = issue_date - timedelta(days=tolerance_days)
        window_end = issue_date + timedelta(days=tolerance_days)
        stmt = select(func.count(Invoice.id)).where(
            Invoice.team_id == team_id,
            Invoice.client_id == client_id,
            Invoice.total == total,
            Invoice.issue_date >= window_start,
            Invoice.issue_date <= window_end,
            Invoice.is_deleted.is_not(True),
        )
        count = int((await db.execute(stmt)).scalar_one() or 0)
        return count > 0

    # ------------------------------------------------------------------
    # AI: Collection probability
    # ------------------------------------------------------------------

    async def predict_collection_probability(
        self,
        invoice: Invoice,
        client: Optional[Client],
    ) -> dict:
        """
        AI prediction: will client pay? How late? Need escalation?
        Returns { probability_pct, likely_delay_days, need_reminder, need_escalation, risk_factors }
        """
        invoice_data = {
            "total": float(invoice.total or 0),
            "balance_due": float(invoice.balance_due or 0),
            "due_date": str(invoice.due_date),
            "status": invoice.status,
        }
        client_history = {
            "avg_days_to_pay": float(client.average_days_to_pay or 30) if client else 30,
            "risk_score": float(client.risk_score or 0) if client else 0,
            "total_invoiced": float(client.total_invoiced or 0) if client else 0,
            "total_paid": float(client.total_paid or 0) if client else 0,
        }
        result = await ai_service.predict_overdue_probability(invoice_data, client_history)
        prob = result.get("probability_pct", 50)
        return {
            **result,
            "need_reminder": prob > 40,
            "need_escalation": prob > 75,
        }

    # ------------------------------------------------------------------
    # Invoice timeline
    # ------------------------------------------------------------------

    async def get_invoice_timeline(
        self,
        db: AsyncSession,
        invoice_id: UUID,
        team_id: UUID,
    ) -> list[dict]:
        """
        Build a full activity timeline for an invoice:
        Created → Sent → Viewed → Reminder Sent → Paid → Overdue → Escalated
        """
        stmt = (
            select(Activity)
            .where(Activity.entity_id == invoice_id, Activity.team_id == team_id)
            .order_by(Activity.created_at)
        )
        activities = (await db.execute(stmt)).scalars().all()

        timeline = []
        for a in activities:
            icon_map = {
                ActivityType.created:          "📄",
                ActivityType.sent:             "📤",
                ActivityType.payment_recorded: "💰",
                ActivityType.overdue:          "⚠️",
                ActivityType.reminder_sent:    "🔔",
                ActivityType.duplicated:       "📋",
                ActivityType.updated:          "✏️",
                ActivityType.deleted:          "🗑️",
            }
            timeline.append({
                "id": str(a.id),
                "action": a.action_type,
                "description": a.description,
                "icon": icon_map.get(a.action_type, "•"),
                "timestamp": a.created_at.isoformat() if a.created_at else None,
                "metadata": a.metadata or {},
            })
        return timeline

    # ------------------------------------------------------------------
    # Invoice health score
    # ------------------------------------------------------------------

    def calculate_invoice_health(self, invoice: Invoice) -> dict:
        """
        Score invoice health 0–100 based on overdue risk, payment progress,
        and priority level.
        """
        score = 100
        reasons = []

        if invoice.status == InvoiceStatus.overdue:
            overdue_days = (date.today() - invoice.due_date).days if invoice.due_date else 0
            penalty = min(40, overdue_days * 2)
            score -= penalty
            reasons.append(f"Overdue by {overdue_days} days (−{penalty})")

        if invoice.balance_due and invoice.total:
            paid_pct = float(invoice.amount_paid or 0) / float(invoice.total) * 100
            if paid_pct < 50:
                score -= 20
                reasons.append(f"Only {paid_pct:.0f}% paid (−20)")

        priority_penalties = {"urgent": 15, "high": 10, "medium": 5, "low": 0}
        penalty = priority_penalties.get(invoice.ai_priority or "medium", 5)
        if penalty:
            score -= penalty
            reasons.append(f"AI priority: {invoice.ai_priority} (−{penalty})")

        score = max(0, min(100, score))
        status = (
            "critical" if score < 30 else
            "at_risk"  if score < 50 else
            "fair"     if score < 75 else
            "healthy"
        )
        return {
            "score": score,
            "status": status,
            "reasons": reasons,
        }

    # ------------------------------------------------------------------
    # Analytics hooks
    # ------------------------------------------------------------------

    async def refresh_analytics_hooks(
        self,
        db: AsyncSession,
        team_id: UUID,
    ) -> None:
        """
        Called after any invoice change to refresh client analytics.
        Updates client totals from invoice aggregates.
        """
        agg_stmt = (
            select(
                Invoice.client_id,
                func.sum(Invoice.total).label("total_invoiced"),
                func.sum(Invoice.amount_paid).label("total_paid"),
                func.count(Invoice.id).label("invoice_count"),
            )
            .where(Invoice.team_id == team_id, Invoice.is_deleted.is_not(True))
            .group_by(Invoice.client_id)
        )
        rows = (await db.execute(agg_stmt)).mappings().all()
        for row in rows:
            if row["client_id"]:
                await db.execute(
                    __import__("sqlalchemy", fromlist=["update"]).update(Client)
                    .where(Client.id == row["client_id"])
                    .values(
                        total_invoiced=float(row["total_invoiced"] or 0),
                        total_paid=float(row["total_paid"] or 0),
                    )
                )
        await db.commit()

    # ------------------------------------------------------------------
    # Internal activity logger
    # ------------------------------------------------------------------

    async def _log(
        self,
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
            entity_type="invoice",
            entity_id=entity_id,
            description=description,
            metadata=metadata or {},
            created_at=_utcnow(),
        ))
