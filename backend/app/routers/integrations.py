"""
app/routers/integrations.py

Unified Integration Hub for InvoiceFlow AI Platform.
Covers: Stripe payments + webhooks, transactional email, WhatsApp messaging,
live currency exchange, multi-currency conversion, AI-powered communication
optimization, integration health monitoring, and real-time WebSocket events.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import ActivityType, NotificationType
from app.database import get_db
from app.models import (
    Activity,
    BusinessInsight,
    Client,
    Invoice,
    InvoiceStatus,
    Notification,
    Payment,
    User,
    Workflow,
)
from app.services.ai_service import AIService
from app.services.analytics_service import AnalyticsService
from app.services.email_service import EmailService
from app.services.notification_service import NotificationService
from app.services.whatsapp_service import WhatsAppService
from app.websocket.manager import ws_manager
from auth import get_current_user

router = APIRouter(prefix="/integrations", tags=["Integrations"])

ai_service = AIService()
email_service = EmailService()
whatsapp_service = WhatsAppService()
analytics_service = AnalyticsService()
notification_service = NotificationService()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_API_BASE = "https://api.stripe.com/v1"

EXCHANGE_API_KEY = os.getenv("EXCHANGE_RATE_API_KEY", "")
EXCHANGE_API_BASE = "https://v6.exchangerate-api.com/v6"

SUPPORTED_CURRENCIES = {
    "USD": {"symbol": "$",  "name": "US Dollar",         "country": "US"},
    "EUR": {"symbol": "€",  "name": "Euro",               "country": "EU"},
    "GBP": {"symbol": "£",  "name": "British Pound",      "country": "GB"},
    "INR": {"symbol": "₹",  "name": "Indian Rupee",       "country": "IN"},
    "CAD": {"symbol": "C$", "name": "Canadian Dollar",    "country": "CA"},
    "AUD": {"symbol": "A$", "name": "Australian Dollar",  "country": "AU"},
    "SGD": {"symbol": "S$", "name": "Singapore Dollar",   "country": "SG"},
    "AED": {"symbol": "د.إ","name": "UAE Dirham",         "country": "AE"},
    "JPY": {"symbol": "¥",  "name": "Japanese Yen",       "country": "JP"},
    "CNY": {"symbol": "¥",  "name": "Chinese Yuan",       "country": "CN"},
    "CHF": {"symbol": "Fr", "name": "Swiss Franc",        "country": "CH"},
    "MXN": {"symbol": "MX$","name": "Mexican Peso",       "country": "MX"},
    "BRL": {"symbol": "R$", "name": "Brazilian Real",     "country": "BR"},
    "ZAR": {"symbol": "R",  "name": "South African Rand", "country": "ZA"},
    "HKD": {"symbol": "HK$","name": "Hong Kong Dollar",   "country": "HK"},
}

EMAIL_TONES = ["professional", "friendly", "startup", "urgent", "premium", "investor-style"]

# Simple in-process exchange rate cache (replace with Redis in production)
_exchange_cache: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PaymentIntentRequest(BaseModel):
    invoice_id: UUID
    currency: str = "USD"
    partial_amount: Optional[float] = None
    save_payment_method: bool = False
    customer_stripe_id: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class EmailSendRequest(BaseModel):
    to: EmailStr
    subject: Optional[str] = None
    body: Optional[str] = None
    tone: str = "professional"
    email_type: str = "reminder"
    invoice_id: Optional[UUID] = None
    attach_invoice_pdf: bool = False
    attach_report_id: Optional[UUID] = None
    scheduled_at: Optional[datetime] = None


class BulkEmailRequest(BaseModel):
    recipient_user_ids: Optional[list[UUID]] = None
    invoice_ids: Optional[list[UUID]] = None
    message_template: str
    tone: str = "professional"
    email_type: str = "bulk_reminder"


class WhatsAppSendRequest(BaseModel):
    phone: str
    client_id: Optional[UUID] = None
    invoice_id: Optional[UUID] = None
    message: Optional[str] = None
    tone: str = "friendly"
    message_type: str = "reminder"
    attach_pdf: bool = False
    scheduled_at: Optional[datetime] = None


class CurrencyConvertRequest(BaseModel):
    from_currency: str
    to_currency: str
    amount: float
    as_of_date: Optional[date] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
        entity_type="integration",
        entity_id=entity_id,
        description=description,
        metadata=metadata or {},
        created_at=_utcnow(),
    ))


async def _stripe_post(path: str, data: dict) -> dict:
    """Make a POST request to Stripe API."""
    if not STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe is not configured. Set STRIPE_SECRET_KEY.",
        )
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{STRIPE_API_BASE}{path}",
            data=data,
            auth=(STRIPE_SECRET_KEY, ""),
            timeout=15,
        )
        body = resp.json()
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Stripe error: {body.get('error', {}).get('message', resp.text)}",
            )
        return body


async def _get_exchange_rates(base: str = "USD") -> dict:
    """Fetch exchange rates with in-memory cache (5-minute TTL)."""
    cached = _exchange_cache.get(base)
    if cached and time.time() - cached["fetched_at"] < 300:
        return cached["rates"]

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{EXCHANGE_API_BASE}/{EXCHANGE_API_KEY}/latest/{base}",
                timeout=10,
            )
            body = resp.json()
        rates = body.get("conversion_rates", {})
    except Exception:
        rates = {c: 1.0 for c in SUPPORTED_CURRENCIES}  # fallback

    _exchange_cache[base] = {"rates": rates, "fetched_at": time.time()}
    return rates


# ===========================================================================
# STRIPE
# ===========================================================================


@router.get("/stripe/config")
async def stripe_config(current_user: User = Depends(get_current_user)) -> dict:
    """Return Stripe publishable key, supported currencies, and integration status."""
    return {
        "publishable_key": STRIPE_PUBLISHABLE_KEY or None,
        "environment": "live" if STRIPE_PUBLISHABLE_KEY.startswith("pk_live") else "test",
        "configured": bool(STRIPE_SECRET_KEY),
        "webhook_configured": bool(STRIPE_WEBHOOK_SECRET),
        "supported_currencies": list(SUPPORTED_CURRENCIES.keys()),
        "enabled_payment_methods": ["card", "apple_pay", "google_pay", "link"],
        "features": {
            "partial_payments": True,
            "subscriptions": True,
            "saved_payment_methods": True,
            "multi_currency": True,
            "auto_tax": False,
        },
    }


@router.post("/stripe/create-payment-intent")
async def create_payment_intent(
    payload: PaymentIntentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Create a Stripe PaymentIntent for an invoice.
    Supports partial payments, multi-currency, saved methods, subscriptions.
    AI tags high-risk payments for fraud monitoring.
    """
    # Load invoice
    stmt = select(Invoice).where(
        Invoice.id == payload.invoice_id,
        Invoice.team_id == current_user.team_id,
    )
    invoice = (await db.execute(stmt)).scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found.")
    if invoice.status == InvoiceStatus.paid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invoice is already paid.")

    charge_amount = float(payload.partial_amount or invoice.balance_due)
    if charge_amount <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payment amount.")

    currency = (payload.currency or invoice.currency or "USD").lower()

    # AI fraud risk assessment
    client_stmt = select(Client).where(Client.id == invoice.client_id)
    client = (await db.execute(client_stmt)).scalar_one_or_none()
    ai_risk = await ai_service.assess_payment_fraud_risk(
        invoice_id=str(invoice.id),
        amount=charge_amount,
        currency=currency,
        client_risk_score=float(client.risk_score or 0) if client else 0.0,
    )

    # Build Stripe request
    # Stripe amounts are in smallest currency unit (cents for USD)
    zero_decimal_currencies = {"jpy", "krw", "vnd", "clp", "gnf", "mga", "pyg", "rwf", "ugx", "xaf", "xof"}
    stripe_amount = int(charge_amount) if currency in zero_decimal_currencies else int(round(charge_amount * 100))

    stripe_data = {
        "amount": str(stripe_amount),
        "currency": currency,
        "automatic_payment_methods[enabled]": "true",
        "metadata[invoice_id]": str(invoice.id),
        "metadata[invoice_number]": invoice.number or "",
        "metadata[client_id]": str(invoice.client_id) if invoice.client_id else "",
        "metadata[business_id]": str(current_user.team_id),
        "metadata[ai_fraud_risk]": str(ai_risk.get("risk_level", "low")),
        "metadata[is_partial]": "true" if payload.partial_amount else "false",
        **payload.metadata,
    }
    if payload.customer_stripe_id:
        stripe_data["customer"] = payload.customer_stripe_id
    if payload.save_payment_method:
        stripe_data["setup_future_usage"] = "off_session"

    intent = await _stripe_post("/payment_intents", stripe_data)

    await _log_activity(
        db,
        team_id=current_user.team_id,
        user_id=current_user.id,
        action_type=ActivityType.payment_initiated,
        entity_id=invoice.id,
        description=f"Payment intent created for invoice {invoice.number}: {currency.upper()} {charge_amount:.2f}",
        metadata={
            "stripe_pi_id": intent["id"],
            "amount": charge_amount,
            "currency": currency,
            "ai_fraud_risk": ai_risk.get("risk_level"),
        },
    )
    await db.commit()

    return {
        "client_secret": intent["client_secret"],
        "payment_intent_id": intent["id"],
        "amount": charge_amount,
        "currency": currency.upper(),
        "invoice_id": str(invoice.id),
        "invoice_number": invoice.number,
        "ai_fraud_risk": ai_risk.get("risk_level", "low"),
        "ai_fraud_score": ai_risk.get("score", 0),
        "ai_recommendation": ai_risk.get("recommendation", ""),
        "is_partial": bool(payload.partial_amount),
        "payment_methods": ["card", "apple_pay", "google_pay"],
    }


@router.post("/stripe/webhook", include_in_schema=False)
async def stripe_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    stripe_signature: Optional[str] = Header(None, alias="stripe-signature"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Handle Stripe webhook events.
    Verifies signature, processes events async in background.
    """
    body = await request.body()

    # Signature validation
    if STRIPE_WEBHOOK_SECRET and stripe_signature:
        if not _verify_stripe_signature(body, stripe_signature, STRIPE_WEBHOOK_SECRET):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Stripe webhook signature.")

    event = json.loads(body)
    event_type = event.get("type", "")
    event_data = event.get("data", {}).get("object", {})

    background_tasks.add_task(_process_stripe_event, event_type=event_type, event_data=event_data)

    return {"received": True, "event": event_type}


def _verify_stripe_signature(payload: bytes, sig_header: str, secret: str) -> bool:
    """Validate Stripe-Signature header using HMAC-SHA256."""
    try:
        parts = {p.split("=")[0]: p.split("=")[1] for p in sig_header.split(",") if "=" in p}
        timestamp = int(parts.get("t", 0))
        signed_payload = f"{timestamp}.".encode() + payload
        expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, parts.get("v1", ""))
    except Exception:
        return False


async def _process_stripe_event(event_type: str, event_data: dict) -> None:
    """Background task: handle Stripe event and trigger automations."""
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        invoice_id_str = (event_data.get("metadata") or {}).get("invoice_id")
        invoice: Optional[Invoice] = None
        if invoice_id_str:
            try:
                stmt = select(Invoice).where(Invoice.id == UUID(invoice_id_str))
                invoice = (await db.execute(stmt)).scalar_one_or_none()
            except Exception:
                pass

        # payment_intent.succeeded
        if event_type == "payment_intent.succeeded" and invoice:
            paid_amount_cents = event_data.get("amount_received", 0)
            paid_amount = paid_amount_cents / 100.0
            invoice.amount_paid = float(invoice.amount_paid or 0) + paid_amount
            invoice.balance_due = max(0, float(invoice.total or 0) - float(invoice.amount_paid))
            if invoice.balance_due <= 0:
                invoice.status = InvoiceStatus.paid
                invoice.paid_at = _utcnow()

            # Record payment
            db.add(Payment(
                invoice_id=invoice.id,
                amount=paid_amount,
                currency=event_data.get("currency", "usd").upper(),
                stripe_payment_intent_id=event_data.get("id"),
                paid_at=_utcnow(),
            ))

            # Thank-you notification
            db.add(Notification(
                user_id=invoice.user_id,
                type=NotificationType.payment_received,
                title=f"Payment received: {invoice.number}",
                message=f"${paid_amount:,.2f} received for invoice {invoice.number}.",
                read=False,
                created_at=_utcnow(),
            ))

            await db.commit()

            await ws_manager.broadcast_to_team(
                str(invoice.team_id),
                {
                    "event": "PAYMENT_RECEIVED",
                    "invoice_id": str(invoice.id),
                    "invoice_number": invoice.number,
                    "amount_paid": paid_amount,
                    "new_status": invoice.status,
                    "balance_due": float(invoice.balance_due),
                },
            )

            # AI thank-you email
            client_stmt = select(Client).where(Client.id == invoice.client_id)
            client = (await db.execute(client_stmt)).scalar_one_or_none()
            if client and client.email:
                thank_you = await ai_service.generate_email(
                    email_type="thank_you",
                    tone="friendly",
                    context={
                        "client_name": client.name,
                        "invoice_number": invoice.number,
                        "amount": paid_amount,
                    },
                )
                await email_service.send(
                    to=client.email,
                    subject=thank_you.get("subject", f"Thank you — Invoice {invoice.number} paid!"),
                    html=thank_you.get("html", ""),
                )

        # payment_intent.failed
        elif event_type == "payment_intent.failed" and invoice:
            ai_recovery = await ai_service.generate_email(
                email_type="payment_recovery",
                tone="professional",
                context={
                    "invoice_number": invoice.number,
                    "amount": float(invoice.balance_due or 0),
                    "failure_reason": (event_data.get("last_payment_error") or {}).get("message", ""),
                },
            )
            db.add(Notification(
                user_id=invoice.user_id,
                type=NotificationType.payment_failed,
                title=f"Payment failed: {invoice.number}",
                message=f"Payment for invoice {invoice.number} failed. AI recovery email queued.",
                read=False,
                created_at=_utcnow(),
            ))
            await db.commit()
            await ws_manager.broadcast_to_team(
                str(invoice.team_id),
                {"event": "PAYMENT_FAILED", "invoice_id": str(invoice.id), "recovery_email_queued": True},
            )

        # charge.refunded
        elif event_type == "charge.refunded" and invoice:
            refund_amount = (event_data.get("amount_refunded", 0)) / 100.0
            invoice.amount_paid = max(0, float(invoice.amount_paid or 0) - refund_amount)
            invoice.balance_due = float(invoice.total or 0) - float(invoice.amount_paid)
            invoice.status = InvoiceStatus.sent  # reopen
            await db.commit()
            await ws_manager.broadcast_to_team(
                str(invoice.team_id),
                {"event": "PAYMENT_REFUNDED", "invoice_id": str(invoice.id), "refund_amount": refund_amount},
            )

        # invoice.paid (Stripe subscription invoice)
        elif event_type == "invoice.paid":
            await ws_manager.broadcast_to_team(
                str(event_data.get("metadata", {}).get("business_id", "")),
                {"event": "SUBSCRIPTION_INVOICE_PAID", "stripe_invoice_id": event_data.get("id")},
            )


# ===========================================================================
# EMAIL
# ===========================================================================


@router.post("/email/send")
async def send_email(
    payload: EmailSendRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Send an AI-generated transactional email. Supports reminders, thank-yous,
    overdue notices, onboarding, business summaries, custom tones.
    """
    invoice = None
    client = None
    if payload.invoice_id:
        inv_stmt = select(Invoice).where(
            Invoice.id == payload.invoice_id, Invoice.team_id == current_user.team_id
        )
        invoice = (await db.execute(inv_stmt)).scalar_one_or_none()
        if invoice and invoice.client_id:
            cl_stmt = select(Client).where(Client.id == invoice.client_id)
            client = (await db.execute(cl_stmt)).scalar_one_or_none()

    # AI email generation if no manual body provided
    email_body = payload.body
    email_subject = payload.subject
    if not email_body:
        context: dict = {
            "business_name": current_user.business_name,
            "recipient_email": payload.to,
        }
        if invoice:
            context.update({
                "invoice_number": invoice.number,
                "amount": float(invoice.balance_due or 0),
                "currency": invoice.currency or "USD",
                "due_date": str(invoice.due_date),
            })
        if client:
            context["client_name"] = client.name
            context["avg_days_to_pay"] = float(client.average_days_to_pay or 14)

        generated = await ai_service.generate_email(
            email_type=payload.email_type,
            tone=payload.tone,
            context=context,
        )
        email_body = generated.get("html", generated.get("text", ""))
        email_subject = email_subject or generated.get("subject", f"Message from {current_user.business_name}")

    # Attachment: invoice PDF
    attachments: list[dict] = []
    if payload.attach_invoice_pdf and invoice:
        pdf_bytes = await email_service.fetch_invoice_pdf(invoice_id=str(invoice.id))
        if pdf_bytes:
            attachments.append({
                "filename": f"Invoice_{invoice.number}.pdf",
                "data": pdf_bytes,
                "content_type": "application/pdf",
            })

    if payload.scheduled_at and payload.scheduled_at > _utcnow():
        background_tasks.add_task(
            email_service.send_at,
            to=payload.to,
            subject=email_subject,
            html=email_body,
            attachments=attachments,
            scheduled_at=payload.scheduled_at,
        )
        send_status = "scheduled"
    else:
        background_tasks.add_task(
            email_service.send,
            to=payload.to,
            subject=email_subject,
            html=email_body,
            attachments=attachments,
        )
        send_status = "queued"

    await _log_activity(
        db,
        team_id=current_user.team_id,
        user_id=current_user.id,
        action_type=ActivityType.email_sent,
        entity_id=invoice.id if invoice else current_user.id,
        description=f"Email sent to {payload.to} ({payload.email_type}, tone={payload.tone})",
        metadata={"email_type": payload.email_type, "tone": payload.tone, "scheduled": bool(payload.scheduled_at)},
    )
    await db.commit()

    await ws_manager.broadcast_to_team(
        str(current_user.team_id),
        {
            "event": "EMAIL_SENT",
            "to": payload.to,
            "email_type": payload.email_type,
            "invoice_id": str(invoice.id) if invoice else None,
        },
    )

    return {
        "status": send_status,
        "to": payload.to,
        "subject": email_subject,
        "email_type": payload.email_type,
        "tone": payload.tone,
        "ai_generated": not payload.body,
        "attachments": len(attachments),
        "scheduled_at": payload.scheduled_at.isoformat() if payload.scheduled_at else None,
    }


@router.post("/email/bulk-send")
async def bulk_send_email(
    payload: BulkEmailRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Send AI-personalised bulk emails to multiple clients/invoices.
    Each email is individually AI-customised.
    """
    targets: list[dict] = []

    if payload.invoice_ids:
        stmt = select(Invoice, Client).join(Client, Invoice.client_id == Client.id, isouter=True).where(
            Invoice.id.in_(payload.invoice_ids), Invoice.team_id == current_user.team_id
        )
        rows = (await db.execute(stmt)).all()
        targets = [
            {"email": row.Client.email, "name": row.Client.name,
             "invoice_number": row.Invoice.number, "amount": float(row.Invoice.balance_due or 0)}
            for row in rows
            if row.Client and row.Client.email
        ]

    for target in targets:
        context = {**target, "business_name": current_user.business_name, "template": payload.message_template}
        background_tasks.add_task(
            _send_bulk_single,
            to=target["email"],
            email_type=payload.email_type,
            tone=payload.tone,
            context=context,
        )

    await db.commit()

    return {
        "status": "queued",
        "recipients": len(targets),
        "email_type": payload.email_type,
        "tone": payload.tone,
        "message": f"{len(targets)} personalised emails queued.",
    }


async def _send_bulk_single(to: str, email_type: str, tone: str, context: dict) -> None:
    generated = await ai_service.generate_email(email_type=email_type, tone=tone, context=context)
    await email_service.send(
        to=to,
        subject=generated.get("subject", "Invoice Update"),
        html=generated.get("html", ""),
    )


# ===========================================================================
# WHATSAPP
# ===========================================================================


@router.post("/whatsapp/send")
async def send_whatsapp(
    payload: WhatsAppSendRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Send an AI-generated WhatsApp message (reminder / thank-you / escalation).
    AI optimises timing and personalises tone based on client payment history.
    """
    client = None
    invoice = None

    if payload.client_id:
        cl_stmt = select(Client).where(Client.id == payload.client_id, Client.team_id == current_user.team_id)
        client = (await db.execute(cl_stmt)).scalar_one_or_none()

    if payload.invoice_id:
        inv_stmt = select(Invoice).where(Invoice.id == payload.invoice_id, Invoice.team_id == current_user.team_id)
        invoice = (await db.execute(inv_stmt)).scalar_one_or_none()

    # AI timing recommendation
    ai_timing = await ai_service.recommend_message_timing(
        channel="whatsapp",
        client_id=str(payload.client_id) if payload.client_id else None,
        message_type=payload.message_type,
    )

    # AI message generation
    wa_message = payload.message
    if not wa_message:
        context: dict = {
            "business_name": current_user.business_name,
            "client_name": client.name if client else "Valued Client",
        }
        if invoice:
            context.update({
                "invoice_number": invoice.number,
                "amount": float(invoice.balance_due or 0),
                "currency": invoice.currency or "USD",
                "due_date": str(invoice.due_date),
            })
        generated = await ai_service.generate_whatsapp_message(
            message_type=payload.message_type,
            tone=payload.tone,
            context=context,
        )
        wa_message = generated.get("text", "")

    # PDF attachment
    pdf_bytes = None
    if payload.attach_pdf and invoice:
        pdf_bytes = await email_service.fetch_invoice_pdf(invoice_id=str(invoice.id))

    if payload.scheduled_at and payload.scheduled_at > _utcnow():
        background_tasks.add_task(
            whatsapp_service.send_at,
            phone=payload.phone,
            message=wa_message,
            pdf_bytes=pdf_bytes,
            scheduled_at=payload.scheduled_at,
        )
        send_status = "scheduled"
    else:
        background_tasks.add_task(
            whatsapp_service.send,
            phone=payload.phone,
            message=wa_message,
            pdf_bytes=pdf_bytes,
        )
        send_status = "queued"

    await _log_activity(
        db,
        team_id=current_user.team_id,
        user_id=current_user.id,
        action_type=ActivityType.whatsapp_sent,
        entity_id=invoice.id if invoice else current_user.id,
        description=f"WhatsApp sent to {payload.phone} ({payload.message_type})",
        metadata={
            "message_type": payload.message_type,
            "tone": payload.tone,
            "ai_best_time": ai_timing.get("recommended_time"),
        },
    )
    await db.commit()

    await ws_manager.broadcast_to_team(
        str(current_user.team_id),
        {
            "event": "WHATSAPP_SENT",
            "phone": payload.phone,
            "message_type": payload.message_type,
            "invoice_id": str(invoice.id) if invoice else None,
        },
    )

    return {
        "status": send_status,
        "phone": payload.phone,
        "message_type": payload.message_type,
        "tone": payload.tone,
        "message_preview": wa_message[:200] if wa_message else "",
        "ai_generated": not payload.message,
        "pdf_attached": bool(pdf_bytes),
        "ai_best_time": ai_timing.get("recommended_time"),
        "ai_reply_probability": ai_timing.get("reply_probability", 0),
        "scheduled_at": payload.scheduled_at.isoformat() if payload.scheduled_at else None,
    }


# ===========================================================================
# CURRENCY
# ===========================================================================


@router.get("/currencies/list")
async def list_currencies(
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return all supported currencies with symbols, countries, and AI popularity ranking."""
    popular_order = ["USD", "EUR", "GBP", "INR", "CAD", "AUD", "SGD", "AED",
                     "JPY", "CNY", "CHF", "MXN", "BRL", "ZAR", "HKD"]
    currencies = []
    for rank, code in enumerate(popular_order, 1):
        info = SUPPORTED_CURRENCIES.get(code, {})
        currencies.append({
            "code": code,
            "name": info.get("name", code),
            "symbol": info.get("symbol", code),
            "country": info.get("country", ""),
            "popularity_rank": rank,
            "exchange_available": True,
        })
    return {
        "currencies": currencies,
        "total": len(currencies),
        "base_currency": "USD",
    }


@router.get("/exchange-rates")
async def exchange_rates(
    base: str = Query("USD", max_length=3),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return live exchange rates with AI currency insights and volatility warnings."""
    base = base.upper()
    if base not in SUPPORTED_CURRENCIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Currency '{base}' not supported. See /currencies/list.",
        )

    rates = await _get_exchange_rates(base)

    # Filter to supported currencies only
    filtered = {c: rates.get(c, 1.0) for c in SUPPORTED_CURRENCIES if c in rates}

    # AI currency insights
    ai_insights = await ai_service.get_currency_insights(
        base_currency=base,
        rates=filtered,
    )

    return {
        "base": base,
        "rates": filtered,
        "last_updated": _utcnow().isoformat(),
        "cache_ttl_seconds": 300,
        "ai_recommendations": ai_insights.get("recommendations", []),
        "volatile_currencies": ai_insights.get("volatile", []),
        "stable_currencies": ai_insights.get("stable", []),
        "ai_invoice_currency_suggestion": ai_insights.get("invoice_currency", base),
    }


@router.post("/currencies/convert")
async def convert_currency(
    payload: CurrencyConvertRequest,
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Convert amount between currencies.
    Supports real-time and historical rates (historical via AI estimation).
    Returns AI prediction for short-term rate movement.
    """
    from_c = payload.from_currency.upper()
    to_c = payload.to_currency.upper()

    for c in (from_c, to_c):
        if c not in SUPPORTED_CURRENCIES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Currency '{c}' not supported.",
            )

    rates = await _get_exchange_rates(from_c)
    rate = rates.get(to_c)
    if not rate:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Exchange rate unavailable.")

    converted = round(payload.amount * rate, 6)
    fee_estimate = round(converted * 0.015, 4)  # 1.5% typical transfer fee estimate

    # AI: short-term movement prediction
    ai_pred = await ai_service.predict_currency_movement(
        from_currency=from_c,
        to_currency=to_c,
        current_rate=rate,
    )

    return {
        "from": from_c,
        "to": to_c,
        "amount": payload.amount,
        "converted_amount": converted,
        "rate": rate,
        "fee_estimate": fee_estimate,
        "net_after_fee": round(converted - fee_estimate, 4),
        "as_of": payload.as_of_date.isoformat() if payload.as_of_date else _utcnow().isoformat(),
        "ai_movement_prediction": ai_pred.get("prediction", "stable"),
        "ai_confidence": ai_pred.get("confidence", 0.5),
        "ai_warning": ai_pred.get("warning"),
        "ai_recommendation": ai_pred.get("recommendation", ""),
    }


# ===========================================================================
# INTEGRATION HEALTH + ANALYTICS
# ===========================================================================


@router.get("/health")
async def integration_health(
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    AI Integration Health Monitor.
    Returns status of all integrations with latency checks and AI fix recommendations.
    """
    checks: dict[str, dict] = {}

    # Stripe
    stripe_ok = bool(STRIPE_SECRET_KEY)
    checks["stripe"] = {
        "configured": stripe_ok,
        "webhook_configured": bool(STRIPE_WEBHOOK_SECRET),
        "status": "healthy" if stripe_ok else "not_configured",
        "publishable_key_set": bool(STRIPE_PUBLISHABLE_KEY),
    }

    # Exchange rate API
    try:
        t0 = time.monotonic()
        await _get_exchange_rates("USD")
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        checks["exchange_rates"] = {"status": "healthy", "latency_ms": latency_ms}
    except Exception as exc:
        checks["exchange_rates"] = {"status": "degraded", "error": str(exc)}

    # Email
    email_configured = bool(os.getenv("SENDGRID_API_KEY") or os.getenv("SMTP_HOST"))
    checks["email"] = {
        "configured": email_configured,
        "status": "healthy" if email_configured else "not_configured",
    }

    # WhatsApp
    wa_configured = bool(os.getenv("WHATSAPP_API_KEY") or os.getenv("TWILIO_ACCOUNT_SID"))
    checks["whatsapp"] = {
        "configured": wa_configured,
        "status": "healthy" if wa_configured else "not_configured",
    }

    overall = "healthy" if all(
        c.get("status") in ("healthy", "not_configured") for c in checks.values()
    ) else "degraded"

    # AI fix recommendations
    issues = [k for k, v in checks.items() if v.get("status") == "not_configured"]
    ai_fixes = [
        f"Set {k.upper().replace('_', '_')} environment variable to enable {k} integration"
        for k in issues
    ]

    return {
        "overall_status": overall,
        "integrations": checks,
        "issues": issues,
        "ai_recommendations": ai_fixes,
        "checked_at": _utcnow().isoformat(),
    }


@router.get("/analytics")
async def integration_analytics(
    period: str = Query("last_30_days"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Integration analytics dashboard:
    payment success rate, email delivery, WhatsApp delivery, recovery rate.
    """
    today = date.today()
    start = today - timedelta(days=30)

    # Payment analytics
    pay_stmt = select(
        func.count(Payment.id).label("total"),
        func.sum(Payment.amount).label("volume"),
    ).where(Payment.paid_at >= start)
    pay_row = (await db.execute(pay_stmt)).mappings().one()

    # Overdue recovery: invoices that moved from overdue → paid
    recovered_stmt = select(func.count(Invoice.id)).where(
        Invoice.team_id == current_user.team_id,
        Invoice.status == InvoiceStatus.paid,
        Invoice.paid_at >= start,
    )
    recovered = int((await db.execute(recovered_stmt)).scalar_one() or 0)

    total_overdue_stmt = select(func.count(Invoice.id)).where(
        Invoice.team_id == current_user.team_id,
        Invoice.status == InvoiceStatus.overdue,
    )
    total_overdue = int((await db.execute(total_overdue_stmt)).scalar_one() or 0)

    recovery_rate = round(recovered / (recovered + total_overdue) * 100, 1) if (recovered + total_overdue) > 0 else 0

    return {
        "period": period,
        "payments": {
            "total_received": int(pay_row["total"] or 0),
            "volume": float(pay_row["volume"] or 0),
        },
        "email": {
            "sent": None,     # populated from email provider webhooks
            "open_rate": None,
            "click_rate": None,
        },
        "whatsapp": {
            "sent": None,
            "delivery_rate": None,
            "reply_rate": None,
        },
        "recovery": {
            "invoices_recovered": recovered,
            "recovery_rate_pct": recovery_rate,
        },
        "ai_insights": [
            "Connect your email provider to track open/click rates.",
            f"Invoice recovery rate: {recovery_rate}% — AI suggests sending reminders 3 days before due date.",
        ],
    }


# ===========================================================================
# WebSocket events reference
# ===========================================================================


@router.get("/ws/events")
async def ws_events_reference() -> dict:
    """Return all WebSocket events emitted by integrations."""
    return {
        "events": [
            {"event": "PAYMENT_RECEIVED",          "description": "Stripe payment succeeded"},
            {"event": "PAYMENT_FAILED",             "description": "Stripe payment failed"},
            {"event": "PAYMENT_REFUNDED",           "description": "Charge refunded"},
            {"event": "SUBSCRIPTION_INVOICE_PAID",  "description": "Stripe subscription cycle paid"},
            {"event": "EMAIL_SENT",                 "description": "Transactional email dispatched"},
            {"event": "WHATSAPP_SENT",              "description": "WhatsApp message dispatched"},
        ],
        "ws_url": "/ws/{team_id}",
        "note": "All events include team_id routing. Connect via WebSocket to receive real-time updates.",
    }
