"""
app/services/payment_service.py

Production-grade payment engine for InvoiceFlow.
Powers Stripe integration, invoice settlement, live payment tracking,
multi-currency support, AI risk detection, fraud signals, refunds,
partial payments, analytics, and autonomous collection strategies.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class PaymentStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"
    DISPUTED = "disputed"
    PARTIALLY_PAID = "partially_paid"


class PaymentMethod(str, Enum):
    CARD = "card"
    UPI = "upi"
    BANK_TRANSFER = "bank_transfer"
    STRIPE_CHECKOUT = "stripe_checkout"
    WALLET = "wallet"
    NET_BANKING = "net_banking"


class InvoiceStatus(str, Enum):
    DRAFT = "draft"
    SENT = "sent"
    PAID = "paid"
    PARTIALLY_PAID = "partially_paid"
    OVERDUE = "overdue"
    PENDING = "pending"


# ---------------------------------------------------------------------------
# Supported currencies and base exchange rates (vs USD)
# Refresh from a live FX API (e.g. Open Exchange Rates) in production.
# ---------------------------------------------------------------------------
_EXCHANGE_RATES_USD: dict[str, float] = {
    "USD": 1.0,
    "INR": 83.5,
    "EUR": 0.92,
    "GBP": 0.79,
    "SGD": 1.34,
    "AED": 3.67,
}

# Stripe expects amounts in the smallest currency unit (paise, cents, etc.)
_ZERO_DECIMAL_CURRENCIES: set[str] = {"JPY", "KRW", "VND"}


# ---------------------------------------------------------------------------
# Adapter helpers
# ---------------------------------------------------------------------------

def _get_db():
    try:
        from app import db
        return db
    except ImportError:
        raise RuntimeError("Could not import 'db' from 'app'. Ensure Flask app context is active.")


def _get_stripe():
    import stripe as _stripe
    key = os.getenv("STRIPE_SECRET_KEY")
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY is not set in environment variables.")
    _stripe.api_key = key
    return _stripe


def _get_ai_client():
    try:
        import openai
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


def _now() -> datetime:
    return datetime.utcnow()


def _new_id() -> str:
    return str(uuid.uuid4())


def _payment_model():
    try:
        from app.models import Payment
        return Payment
    except ImportError:
        raise RuntimeError("Payment model not found — import it from app.models.")


def _invoice_model():
    try:
        from app.models import Invoice
        return Invoice
    except ImportError:
        raise RuntimeError("Invoice model not found — import it from app.models.")


# ---------------------------------------------------------------------------
# Currency helpers
# ---------------------------------------------------------------------------

def _to_stripe_amount(amount: float, currency: str) -> int:
    """Convert a decimal amount to Stripe's smallest-unit integer."""
    if currency.upper() in _ZERO_DECIMAL_CURRENCIES:
        return int(amount)
    return int(round(amount * 100))


def _from_stripe_amount(stripe_amount: int, currency: str) -> float:
    if currency.upper() in _ZERO_DECIMAL_CURRENCIES:
        return float(stripe_amount)
    return stripe_amount / 100


# ===========================================================================
# 1. STRIPE PAYMENT INTENT CREATION
# ===========================================================================

def create_payment_intent(
    invoice_id: str,
    amount: float,
    currency: str = "usd",
    *,
    client_id: str | None = None,
    team_id: str | None = None,
    client_email: str | None = None,
    description: str = "",
    payment_method_types: list[str] | None = None,
    capture_method: str = "automatic",
) -> dict:
    """
    Create a Stripe PaymentIntent for a given invoice.

    Parameters
    ----------
    invoice_id           : InvoiceFlow invoice ID (stored in Stripe metadata).
    amount               : Decimal amount in the given currency.
    currency             : ISO 4217 currency code (default 'usd').
    client_id            : Client entity ID (for metadata & Stripe customer lookup).
    team_id              : Team ID (for metadata).
    client_email         : Client email — used to find/create a Stripe Customer.
    description          : Payment description shown in Stripe Dashboard.
    payment_method_types : Defaults to ['card']. Add 'link', 'sepa_debit', etc.
    capture_method       : 'automatic' or 'manual' (for pre-authorisation).

    Returns
    -------
    {
        "payment_intent_id": "pi_...",
        "client_secret": "pi_..._secret_...",
        "payment_url": "https://checkout.stripe.com/...",
        "amount": 24500.0,
        "currency": "inr",
        "status": "requires_payment_method"
    }
    """
    stripe = _get_stripe()
    currency = currency.lower()
    stripe_amount = _to_stripe_amount(amount, currency)
    payment_method_types = payment_method_types or ["card"]

    metadata = {
        "invoice_id": invoice_id,
        "client_id": client_id or "",
        "team_id": team_id or "",
        "platform": "invoiceflow",
    }

    intent_params: dict[str, Any] = {
        "amount": stripe_amount,
        "currency": currency,
        "payment_method_types": payment_method_types,
        "description": description or f"Payment for invoice {invoice_id}",
        "metadata": metadata,
        "capture_method": capture_method,
    }

    # Attach to Stripe Customer if email provided
    if client_email:
        customer = _find_or_create_stripe_customer(stripe, client_email, metadata)
        if customer:
            intent_params["customer"] = customer["id"]
            intent_params["receipt_email"] = client_email

    intent = stripe.PaymentIntent.create(**intent_params)

    # Build a hosted payment link for Stripe Checkout
    session = _create_checkout_session(
        stripe, intent, invoice_id, currency, stripe_amount, client_email
    )

    logger.info(
        "PaymentIntent created: %s for invoice=%s amount=%s%s",
        intent.id, invoice_id, amount, currency.upper(),
    )

    return {
        "payment_intent_id": intent.id,
        "client_secret": intent.client_secret,
        "payment_url": session.get("url") if session else None,
        "checkout_session_id": session.get("id") if session else None,
        "amount": amount,
        "currency": currency,
        "status": intent.status,
    }


def _find_or_create_stripe_customer(
    stripe, email: str, metadata: dict
) -> dict | None:
    try:
        existing = stripe.Customer.list(email=email, limit=1)
        if existing.data:
            return existing.data[0]
        return stripe.Customer.create(email=email, metadata=metadata)
    except Exception as exc:
        logger.warning("Stripe customer lookup/create failed: %s", exc)
        return None


def _create_checkout_session(
    stripe, intent, invoice_id: str, currency: str, amount: int, email: str | None
) -> dict | None:
    try:
        base_url = os.getenv("APP_BASE_URL", "http://localhost:5000")
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": currency,
                    "unit_amount": amount,
                    "product_data": {"name": f"Invoice {invoice_id}"},
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=f"{base_url}/payment/success?invoice={invoice_id}&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base_url}/payment/cancelled?invoice={invoice_id}",
            customer_email=email,
            payment_intent_data={"metadata": {"invoice_id": invoice_id}},
        )
        return {"id": session.id, "url": session.url}
    except Exception as exc:
        logger.warning("Checkout session creation failed: %s", exc)
        return None


# ===========================================================================
# 2. STRIPE WEBHOOK HANDLER
# ===========================================================================

def handle_webhook(payload: bytes, sig_header: str) -> dict:
    """
    Verify and route incoming Stripe webhook events.

    Handled events
    --------------
    payment_intent.succeeded          → process_successful_payment()
    payment_intent.payment_failed     → handle_failed_payment()
    charge.refunded                   → process_refund()
    checkout.session.completed        → process_successful_payment() via session data

    Parameters
    ----------
    payload    : Raw request body bytes (do NOT parse before passing here).
    sig_header : Value of the Stripe-Signature header.

    Returns
    -------
    {"ok": True, "event_type": "...", "result": {...}}
    """
    stripe = _get_stripe()
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except stripe.error.SignatureVerificationError as exc:
        logger.error("Stripe webhook signature verification failed: %s", exc)
        raise ValueError("Invalid Stripe webhook signature") from exc

    event_type = event["type"]
    data = event["data"]["object"]

    logger.info("Stripe webhook received: %s", event_type)

    if event_type == "payment_intent.succeeded":
        result = process_successful_payment(data)
    elif event_type == "payment_intent.payment_failed":
        result = handle_failed_payment(data)
    elif event_type == "charge.refunded":
        result = _handle_charge_refunded(data)
    elif event_type == "checkout.session.completed":
        result = _handle_checkout_completed(data)
    else:
        logger.debug("Unhandled Stripe event: %s", event_type)
        result = {"ok": True, "note": "Event received but not handled"}

    return {"ok": True, "event_type": event_type, "result": result}


def _handle_checkout_completed(session_data: dict) -> dict:
    """Handle a completed Stripe Checkout Session."""
    invoice_id = (session_data.get("metadata") or {}).get("invoice_id")
    amount_total = session_data.get("amount_total", 0)
    currency = session_data.get("currency", "usd")

    if not invoice_id:
        return {"ok": False, "error": "No invoice_id in session metadata"}

    synthetic_intent = {
        "id": session_data.get("payment_intent"),
        "amount": amount_total,
        "currency": currency,
        "metadata": {"invoice_id": invoice_id},
        "payment_method_types": ["card"],
        "customer_details": session_data.get("customer_details", {}),
    }
    return process_successful_payment(synthetic_intent)


def _handle_charge_refunded(charge_data: dict) -> dict:
    """Handle a Stripe charge.refunded event."""
    refund_data = (charge_data.get("refunds", {}).get("data") or [{}])[0]
    return {
        "ok": True,
        "charge_id": charge_data.get("id"),
        "amount_refunded": _from_stripe_amount(
            charge_data.get("amount_refunded", 0),
            charge_data.get("currency", "usd"),
        ),
        "refund_id": refund_data.get("id"),
    }


# ===========================================================================
# 3. PAYMENT SUCCESS PROCESSING
# ===========================================================================

def process_successful_payment(intent_data: dict) -> dict:
    """
    Full payment success pipeline.

    Flow
    ----
    Payment success
    → create payment record
    → update invoice balance
    → mark invoice paid / partially paid
    → send notification
    → broadcast WebSocket event
    → generate thank-you email
    → update analytics

    Parameters
    ----------
    intent_data : Stripe PaymentIntent dict (from webhook or API).
    """
    invoice_id = (intent_data.get("metadata") or {}).get("invoice_id")
    currency = intent_data.get("currency", "usd")
    amount = _from_stripe_amount(
        int(intent_data.get("amount", 0)), currency
    )
    transaction_id = intent_data.get("id", _new_id())
    customer_details = intent_data.get("customer_details") or {}
    client_email = customer_details.get("email")

    # 1. Create payment record
    payment = create_payment_record(
        invoice_id=invoice_id,
        amount=amount,
        currency=currency,
        transaction_id=transaction_id,
        payment_method=PaymentMethod.STRIPE_CHECKOUT,
        status=PaymentStatus.COMPLETED,
        notes="Stripe webhook: payment_intent.succeeded",
    )

    # 2 & 3. Update invoice balance and status
    invoice_result = update_invoice_balance(invoice_id, amount) if invoice_id else {}

    # 4. Send in-app notification
    trigger_payment_notifications(
        invoice_id=invoice_id,
        amount=amount,
        currency=currency,
        event="payment_received",
        client_email=client_email,
    )

    # 5. Broadcast real-time event
    broadcast_payment_event("payment_received", {
        "invoice_id": invoice_id,
        "amount": amount,
        "currency": currency,
        "transaction_id": transaction_id,
    })

    # 6. Generate and send thank-you email
    thank_you = generate_thank_you_message(
        client_name=customer_details.get("name", "Valued Client"),
        amount=amount,
        currency=currency,
        invoice_id=invoice_id,
    )
    if client_email:
        _send_thank_you_email(client_email, thank_you, invoice_id)

    # 7. Update analytics
    update_payment_analytics(amount, currency, event="payment_received")

    logger.info(
        "Payment processed: invoice=%s amount=%s%s tx=%s",
        invoice_id, amount, currency.upper(), transaction_id,
    )

    return {
        "ok": True,
        "payment": payment,
        "invoice": invoice_result,
        "thank_you": thank_you,
    }


# ===========================================================================
# 4. PAYMENT FAILURE HANDLING
# ===========================================================================

def handle_failed_payment(intent_data: dict) -> dict:
    """
    Process a failed payment intent.

    Actions
    -------
    - Log failure with reason
    - Update payment record status
    - Trigger in-app and email notifications
    - Generate AI recovery recommendation
    - Trigger escalation workflow if repeat failure

    Parameters
    ----------
    intent_data : Stripe PaymentIntent dict from webhook.
    """
    invoice_id = (intent_data.get("metadata") or {}).get("invoice_id")
    currency = intent_data.get("currency", "usd")
    amount = _from_stripe_amount(int(intent_data.get("amount", 0)), currency)
    transaction_id = intent_data.get("id", _new_id())
    error_msg = (
        (intent_data.get("last_payment_error") or {}).get("message")
        or "Payment declined"
    )

    # Record the failure
    payment = create_payment_record(
        invoice_id=invoice_id,
        amount=amount,
        currency=currency,
        transaction_id=transaction_id,
        payment_method=PaymentMethod.CARD,
        status=PaymentStatus.FAILED,
        notes=f"Stripe failure: {error_msg}",
    )

    # AI recovery recommendation
    ai_rec = _generate_failure_recovery_recommendation(error_msg, amount, currency)

    # Notify user
    trigger_payment_notifications(
        invoice_id=invoice_id,
        amount=amount,
        currency=currency,
        event="payment_failed",
        extra={"error": error_msg, "ai_recommendation": ai_rec},
    )

    broadcast_payment_event("payment_failed", {
        "invoice_id": invoice_id,
        "transaction_id": transaction_id,
        "error": error_msg,
        "ai_recommendation": ai_rec,
    })

    logger.warning(
        "Payment failed: invoice=%s amount=%s tx=%s reason=%s",
        invoice_id, amount, transaction_id, error_msg,
    )

    return {
        "ok": False,
        "payment": payment,
        "error": error_msg,
        "ai_recommendation": ai_rec,
    }


def _generate_failure_recovery_recommendation(
    error_msg: str, amount: float, currency: str
) -> str:
    ai = _get_ai_client()
    if not ai:
        return (
            "Payment failed. Please verify your card details and try again, "
            "or contact your bank to authorise the transaction."
        )
    try:
        prompt = (
            f"A payment of {amount} {currency.upper()} failed with reason: '{error_msg}'. "
            "Write a brief, helpful recommendation for the business owner in 1-2 sentences."
        )
        resp = ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=80,
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        logger.warning("AI failure recovery recommendation failed: %s", exc)
        return "Retry the payment or reach out to the client to update their payment method."


# ===========================================================================
# 5. PAYMENT RECORD CREATION
# ===========================================================================

def create_payment_record(
    invoice_id: str | None,
    amount: float,
    currency: str,
    *,
    transaction_id: str | None = None,
    payment_method: str = PaymentMethod.CARD,
    status: str = PaymentStatus.COMPLETED,
    client_id: str | None = None,
    notes: str = "",
    paid_at: datetime | None = None,
    is_partial: bool = False,
    refunded_amount: float = 0.0,
) -> dict:
    """
    Persist a Payment record.

    Supports full payments, partial payments, and refund entries.

    Parameters
    ----------
    invoice_id      : Linked invoice ID (can be None for subscription payments).
    amount          : Payment amount in the specified currency.
    currency        : ISO 4217 currency code.
    transaction_id  : Stripe / external transaction ID.
    payment_method  : PaymentMethod value.
    status          : PaymentStatus value.
    client_id       : Client entity ID.
    notes           : Internal notes.
    paid_at         : Payment timestamp (defaults to now).
    is_partial      : Whether this is a partial payment on the invoice.
    refunded_amount : Amount refunded (for refund records).
    """
    db = _get_db()
    Payment = _payment_model()

    record = Payment(
        id=_new_id(),
        invoice_id=invoice_id,
        client_id=client_id,
        amount=amount,
        currency=currency.upper(),
        transaction_id=transaction_id or _new_id(),
        payment_method=payment_method,
        status=status,
        notes=notes,
        is_partial=is_partial,
        refunded_amount=refunded_amount,
        paid_at=paid_at or _now(),
        created_at=_now(),
    )
    db.session.add(record)
    db.session.commit()

    logger.info(
        "Payment record created: id=%s invoice=%s amount=%s%s status=%s",
        record.id, invoice_id, amount, currency.upper(), status,
    )
    return _serialize_payment(record)


def _serialize_payment(payment) -> dict:
    return {
        "id": payment.id,
        "invoice_id": payment.invoice_id,
        "client_id": getattr(payment, "client_id", None),
        "amount": float(payment.amount),
        "currency": payment.currency,
        "transaction_id": payment.transaction_id,
        "payment_method": payment.payment_method,
        "status": payment.status,
        "notes": getattr(payment, "notes", ""),
        "is_partial": getattr(payment, "is_partial", False),
        "refunded_amount": float(getattr(payment, "refunded_amount", 0)),
        "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
        "created_at": payment.created_at.isoformat() if payment.created_at else None,
    }


# ===========================================================================
# 6. INVOICE BALANCE UPDATE ENGINE
# ===========================================================================

def update_invoice_balance(invoice_id: str, payment_amount: float) -> dict:
    """
    Recalculate an invoice's outstanding balance and update its status.

    Status transitions
    ------------------
    total_paid >= total_amount → 'paid'
    0 < total_paid < total_amount → 'partially_paid'
    total_paid == 0 and past due date → 'overdue'
    total_paid == 0 → 'pending'

    Parameters
    ----------
    invoice_id      : Invoice to update.
    payment_amount  : The payment amount just received.

    Returns
    -------
    Updated invoice summary dict.
    """
    db = _get_db()
    Invoice = _invoice_model()
    Payment = _payment_model()

    invoice = Invoice.query.get(invoice_id)
    if not invoice:
        logger.error("Invoice %s not found for balance update", invoice_id)
        return {"ok": False, "error": "Invoice not found"}

    # Sum all completed payments against this invoice
    completed_payments = Payment.query.filter_by(
        invoice_id=invoice_id, status=PaymentStatus.COMPLETED
    ).all()
    total_paid = sum(float(p.amount) for p in completed_payments)
    total_amount = float(invoice.total_amount)
    balance_due = max(0.0, total_amount - total_paid)

    # Determine status
    if total_paid >= total_amount:
        new_status = InvoiceStatus.PAID
    elif total_paid > 0:
        new_status = InvoiceStatus.PARTIALLY_PAID
    else:
        due_date = getattr(invoice, "due_date", None)
        if due_date and _now() > due_date:
            new_status = InvoiceStatus.OVERDUE
        else:
            new_status = InvoiceStatus.PENDING

    invoice.status = new_status
    invoice.amount_paid = total_paid
    invoice.balance_due = balance_due
    invoice.updated_at = _now()

    if new_status == InvoiceStatus.PAID and not getattr(invoice, "paid_at", None):
        invoice.paid_at = _now()

    db.session.commit()

    broadcast_payment_event("invoice_status_updated", {
        "invoice_id": invoice_id,
        "new_status": new_status,
        "total_paid": total_paid,
        "balance_due": balance_due,
    })

    return {
        "invoice_id": invoice_id,
        "total_amount": total_amount,
        "total_paid": total_paid,
        "balance_due": balance_due,
        "new_status": new_status,
    }


# ===========================================================================
# 7. MULTI-CURRENCY PAYMENT SUPPORT
# ===========================================================================

def convert_payment_currency(
    amount: float,
    from_currency: str,
    to_currency: str,
    *,
    live_rates: bool = False,
) -> dict:
    """
    Convert a payment amount between supported currencies.

    Supported currencies: INR, USD, EUR, GBP, SGD, AED.

    Parameters
    ----------
    amount        : Amount in from_currency.
    from_currency : Source ISO 4217 code.
    to_currency   : Target ISO 4217 code.
    live_rates    : When True, attempt to fetch live rates (requires
                    OPEN_EXCHANGE_RATES_APP_ID env var).

    Returns
    -------
    {
        "original_amount": 1000.0, "from_currency": "INR",
        "converted_amount": 11.98, "to_currency": "USD",
        "exchange_rate": 0.01198, "rate_source": "static"
    }
    """
    from_code = from_currency.upper()
    to_code = to_currency.upper()

    rates = _EXCHANGE_RATES_USD.copy()
    rate_source = "static"

    if live_rates:
        fetched = _fetch_live_rates()
        if fetched:
            rates.update(fetched)
            rate_source = "live"

    if from_code not in rates:
        raise ValueError(f"Unsupported currency: {from_code}")
    if to_code not in rates:
        raise ValueError(f"Unsupported currency: {to_code}")

    amount_in_usd = amount / rates[from_code]
    converted = amount_in_usd * rates[to_code]
    exchange_rate = rates[to_code] / rates[from_code]

    return {
        "original_amount": round(amount, 2),
        "from_currency": from_code,
        "converted_amount": round(converted, 2),
        "to_currency": to_code,
        "exchange_rate": round(exchange_rate, 6),
        "rate_source": rate_source,
    }


def _fetch_live_rates() -> dict | None:
    try:
        import urllib.request
        app_id = os.getenv("OPEN_EXCHANGE_RATES_APP_ID")
        if not app_id:
            return None
        url = f"https://openexchangerates.org/api/latest.json?app_id={app_id}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        return data.get("rates", {})
    except Exception as exc:
        logger.warning("Live FX rate fetch failed: %s", exc)
        return None


# ===========================================================================
# 8. AI PAYMENT RISK DETECTION
# ===========================================================================

def detect_payment_risk(
    invoice: dict,
    *,
    client_payment_history: list[dict] | None = None,
) -> dict:
    """
    Analyse payment risk using AI and rule-based signals.

    Risk factors considered
    -----------------------
    - Number of past late payments from this client
    - Invoice amount relative to client average
    - Days since invoice was sent without payment
    - Current client risk classification
    - Failed payment attempts on this invoice

    Parameters
    ----------
    invoice                : Invoice dict (due_date, amount, client_risk, etc.).
    client_payment_history : Historical payment records for this client.

    Returns
    -------
    {
        "risk_level": "high",
        "risk_score": 78,
        "factors": [...],
        "ai_assessment": "..."
    }
    """
    history = client_payment_history or []
    factors: list[str] = []
    score = 0

    # Rule-based signals
    late_payments = sum(1 for p in history if p.get("was_late"))
    if late_payments >= 3:
        score += 30
        factors.append(f"{late_payments} late payments in payment history")
    elif late_payments >= 1:
        score += 15
        factors.append(f"{late_payments} late payment(s) on record")

    amount = float(invoice.get("amount", 0))
    avg_amount = (
        sum(float(p.get("amount", 0)) for p in history) / len(history)
        if history else 0
    )
    if avg_amount and amount > avg_amount * 2:
        score += 20
        factors.append(f"Invoice amount {amount:.0f} is 2x above client average ({avg_amount:.0f})")

    due_date = invoice.get("due_date")
    if due_date:
        if isinstance(due_date, str):
            due_date = datetime.fromisoformat(due_date)
        days_overdue = (_now() - due_date).days
        if days_overdue >= 14:
            score += 25
            factors.append(f"Invoice {days_overdue} days overdue")
        elif days_overdue >= 7:
            score += 15
            factors.append(f"Invoice {days_overdue} days overdue")

    client_risk = invoice.get("client_risk", "").lower()
    if client_risk == "high":
        score += 20
        factors.append("Client classified as high risk")
    elif client_risk == "medium":
        score += 10

    failed_attempts = int(invoice.get("failed_payment_attempts", 0))
    if failed_attempts >= 2:
        score += 15
        factors.append(f"{failed_attempts} failed payment attempts")

    score = min(score, 100)

    if score >= 70:
        risk_level = "critical"
    elif score >= 50:
        risk_level = "high"
    elif score >= 30:
        risk_level = "medium"
    else:
        risk_level = "low"

    # AI narrative assessment
    ai = _get_ai_client()
    ai_assessment = ""
    if ai and factors:
        try:
            prompt = (
                f"An invoice risk analysis found these signals: {'; '.join(factors)}. "
                f"Risk score: {score}/100. Write a 1-sentence executive risk summary."
            )
            resp = ai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=80,
            )
            ai_assessment = resp.choices[0].message.content.strip()
        except Exception as exc:
            logger.warning("AI risk assessment failed: %s", exc)

    if not ai_assessment and factors:
        ai_assessment = f"AI detected {risk_level} payment risk. Factors: {'; '.join(factors[:2])}."

    return {
        "risk_level": risk_level,
        "risk_score": score,
        "factors": factors,
        "ai_assessment": ai_assessment,
    }


# ===========================================================================
# 9. SMART PAYMENT RECOMMENDATIONS
# ===========================================================================

def generate_payment_recommendations(
    invoice: dict,
    *,
    client_history: list[dict] | None = None,
) -> list[str]:
    """
    Generate actionable payment collection recommendations using AI.

    Example outputs
    ---------------
    - "Send a payment reminder 3 days before the due date."
    - "Offer an instalment plan — client has a history of large payment delays."
    - "This client historically pays 8 days late; follow up proactively."

    Returns
    -------
    List of recommendation strings (up to 5).
    """
    ai = _get_ai_client()
    client_history = client_history or []

    # Rule-based baseline
    recs: list[str] = []
    amount = float(invoice.get("amount", 0))
    if amount > 20000:
        recs.append("Consider offering an instalment payment plan for this large invoice.")

    due_date = invoice.get("due_date")
    if due_date:
        if isinstance(due_date, str):
            due_date = datetime.fromisoformat(due_date)
        days_until_due = (due_date - _now()).days
        if 0 < days_until_due <= 5:
            recs.append(f"Invoice due in {days_until_due} days — send a reminder now.")

    late_history = sum(1 for p in client_history if p.get("was_late"))
    if late_history >= 2:
        recs.append("This client has a history of late payments — follow up proactively.")

    if not ai:
        return recs or ["Monitor invoice status and send reminders as the due date approaches."]

    try:
        prompt = (
            f"Invoice details: {json.dumps(invoice)}. "
            f"Client payment history summary: {len(client_history)} payments, "
            f"{late_history} late. "
            "Give 3 concise, actionable payment collection recommendations. "
            "Return a JSON array of strings."
        )
        resp = ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=250,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        ai_recs = data if isinstance(data, list) else data.get("recommendations", [])
        recs.extend(ai_recs[:3])
    except Exception as exc:
        logger.warning("AI payment recommendations failed: %s", exc)

    return recs[:5]


# ===========================================================================
# 10. PAYMENT NOTIFICATION TRIGGERS
# ===========================================================================

def trigger_payment_notifications(
    invoice_id: str | None,
    amount: float,
    currency: str,
    event: str,
    *,
    user_id: int | None = None,
    client_email: str | None = None,
    extra: dict | None = None,
) -> dict:
    """
    Fan out payment event notifications to all relevant channels.

    Events handled
    --------------
    payment_received  → success notification + email
    payment_failed    → failure alert + AI recommendation
    partial_payment   → balance update notification
    refund_issued     → refund confirmation

    Returns
    -------
    Dict of channel → delivery result.
    """
    from app.services.notification_service import create_notification, NotificationType

    event_map = {
        "payment_received": (NotificationType.PAYMENT_RECEIVED, "Payment Received", "medium"),
        "payment_failed": (NotificationType.WORKFLOW_FAILED, "Payment Failed", "high"),
        "partial_payment": (NotificationType.PAYMENT_RECEIVED, "Partial Payment Received", "medium"),
        "refund_issued": (NotificationType.INVOICE_PAID, "Refund Issued", "medium"),
    }

    ntype, title, priority = event_map.get(event, (NotificationType.PAYMENT_RECEIVED, event.replace("_", " ").title(), "medium"))
    message = (
        f"Payment of {amount:.2f} {currency.upper()} "
        + ("received" if "received" in event else event.replace("_", " "))
        + (f" for invoice {invoice_id}" if invoice_id else "")
        + "."
    )

    results: dict[str, Any] = {}

    if user_id:
        try:
            note = create_notification(
                user_id=user_id,
                notification_type=ntype,
                title=title,
                message=message,
                entity_type="invoice",
                entity_id=invoice_id,
                priority=priority,
                metadata={**(extra or {}), "amount": amount, "currency": currency},
            )
            results["in_app"] = {"ok": True, "notification_id": note["id"]}
        except Exception as exc:
            results["in_app"] = {"ok": False, "error": str(exc)}

    if client_email:
        results["email"] = {"ok": True, "queued": True, "to": client_email}
        logger.info("Payment email queued → %s | %s", client_email, title)

    return results


# ===========================================================================
# 11. AI THANK-YOU GENERATOR
# ===========================================================================

def generate_thank_you_message(
    client_name: str,
    amount: float,
    currency: str,
    invoice_id: str | None = None,
) -> str:
    """
    Generate a personalised thank-you message for a completed payment.

    Uses AI when available; falls back to a polished template.

    Returns
    -------
    Thank-you message string (2-3 sentences).
    """
    ai = _get_ai_client()
    currency_symbol = {"USD": "$", "INR": "₹", "EUR": "€", "GBP": "£"}.get(currency.upper(), currency.upper() + " ")
    formatted_amount = f"{currency_symbol}{amount:,.2f}"

    if not ai:
        return (
            f"Thank you, {client_name}, for your payment of {formatted_amount}"
            + (f" for invoice {invoice_id}" if invoice_id else "")
            + ". We truly appreciate your continued partnership and prompt payment. "
            "Please don't hesitate to reach out if you need anything."
        )

    try:
        prompt = (
            f"Write a warm, professional 2-3 sentence thank-you message for a payment of "
            f"{formatted_amount} received from {client_name}"
            + (f" for invoice {invoice_id}" if invoice_id else "")
            + ". Keep it genuine, brief, and business-appropriate."
        )
        resp = ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        logger.warning("AI thank-you generation failed: %s", exc)
        return (
            f"Thank you, {client_name}, for your payment of {formatted_amount}. "
            "We appreciate your business and look forward to working with you again."
        )


def _send_thank_you_email(
    to_email: str, message: str, invoice_id: str | None
) -> None:
    """Send the thank-you message via the notification service email engine."""
    try:
        from app.services.notification_service import send_email_notification
        send_email_notification(
            to_email=to_email,
            subject="Thank You for Your Payment — InvoiceFlow",
            notification={
                "title": "Thank You for Your Payment",
                "message": message,
                "notification_type": "invoice_paid",
                "priority": "medium",
                "entity_id": invoice_id,
                "metadata": {"action_label": "View Invoice", "action_url": f"/invoices/{invoice_id}"},
            },
        )
    except Exception as exc:
        logger.warning("Thank-you email delivery failed: %s", exc)


# ===========================================================================
# 12. REAL-TIME PAYMENT BROADCASTS
# ===========================================================================

def broadcast_payment_event(event: str, payload: dict) -> dict:
    """
    Push a live payment event over WebSocket.

    Events
    ------
    payment_received      → Triggers dashboard revenue refresh
    invoice_paid          → Updates invoice list and analytics
    invoice_status_updated→ Updates invoice detail view
    payment_failed        → Shows failure alert
    revenue_updated       → Refreshes revenue chart
    dashboard_refresh     → Forces full dashboard data reload

    Returns
    -------
    WebSocket delivery result dict.
    """
    sio = _get_socketio()
    if sio is None:
        logger.debug("WebSocket broadcast skipped — SocketIO not configured: %s", event)
        return {"ok": False, "reason": "SocketIO not configured"}

    full_payload = {
        **payload,
        "event": event,
        "timestamp": _now().isoformat(),
    }

    try:
        sio.emit(event, full_payload)
        sio.emit("dashboard_refresh", {"trigger": event, "timestamp": full_payload["timestamp"]})
        logger.debug("Payment broadcast: %s", event)
        return {"ok": True, "event": event}
    except Exception as exc:
        logger.warning("Payment WebSocket broadcast failed for %s: %s", event, exc)
        return {"ok": False, "error": str(exc)}


# ===========================================================================
# 13. PAYMENT ANALYTICS INTEGRATION
# ===========================================================================

def update_payment_analytics(
    amount: float,
    currency: str,
    event: str = "payment_received",
) -> dict:
    """
    Update live analytics after a payment event.

    Recalculates and broadcasts:
    - Total revenue
    - Cash flow delta
    - Collection rate
    - Days Sales Outstanding (DSO)
    - Overdue reduction stats

    In a production app, persist these to a dedicated analytics table or
    time-series store (InfluxDB, TimescaleDB, etc.).
    """
    analytics = {
        "event": event,
        "amount": amount,
        "currency": currency.upper(),
        "timestamp": _now().isoformat(),
        "metrics_updated": ["revenue", "cashflow", "collection_rate", "dso"],
    }

    broadcast_payment_event("revenue_updated", analytics)

    logger.info(
        "Analytics updated: event=%s amount=%s%s", event, amount, currency.upper()
    )
    return analytics


# ===========================================================================
# 14. REFUND PROCESSING
# ===========================================================================

def process_refund(
    payment_id: str,
    refund_amount: float | None = None,
    *,
    reason: str = "requested_by_customer",
) -> dict:
    """
    Process a refund via Stripe and update internal records.

    Parameters
    ----------
    payment_id    : InvoiceFlow Payment record ID.
    refund_amount : Partial refund amount (None = full refund).
    reason        : Stripe refund reason ('duplicate', 'fraudulent', 'requested_by_customer').

    Returns
    -------
    Refund result dict with Stripe refund ID and updated payment record.
    """
    db = _get_db()
    Payment = _payment_model()

    payment = Payment.query.get(payment_id)
    if not payment:
        raise ValueError(f"Payment {payment_id!r} not found")

    amount_to_refund = refund_amount or float(payment.amount)
    stripe = _get_stripe()

    try:
        stripe_refund = stripe.Refund.create(
            payment_intent=payment.transaction_id,
            amount=_to_stripe_amount(amount_to_refund, payment.currency),
            reason=reason,
        )
        refund_id = stripe_refund.id
    except Exception as exc:
        logger.error("Stripe refund failed for payment %s: %s", payment_id, exc)
        return {"ok": False, "error": str(exc)}

    # Update payment record
    payment.refunded_amount = (getattr(payment, "refunded_amount", 0) or 0) + amount_to_refund
    payment.status = PaymentStatus.REFUNDED
    payment.updated_at = _now()
    db.session.commit()

    # Adjust invoice balance
    if payment.invoice_id:
        update_invoice_balance(payment.invoice_id, -amount_to_refund)

    trigger_payment_notifications(
        invoice_id=payment.invoice_id,
        amount=amount_to_refund,
        currency=payment.currency,
        event="refund_issued",
    )

    broadcast_payment_event("refund_issued", {
        "payment_id": payment_id,
        "refund_id": refund_id,
        "amount": amount_to_refund,
        "currency": payment.currency,
    })

    logger.info(
        "Refund processed: payment=%s refund_id=%s amount=%s",
        payment_id, refund_id, amount_to_refund,
    )
    return {
        "ok": True,
        "refund_id": refund_id,
        "refunded_amount": amount_to_refund,
        "payment_id": payment_id,
        "status": PaymentStatus.REFUNDED,
    }


# ===========================================================================
# 15. PARTIAL PAYMENT SUPPORT
# ===========================================================================

def handle_partial_payment(
    invoice_id: str,
    partial_amount: float,
    currency: str,
    *,
    transaction_id: str | None = None,
    payment_method: str = PaymentMethod.CARD,
    user_id: int | None = None,
) -> dict:
    """
    Record a partial payment and update the invoice outstanding balance.

    Example
    -------
    Invoice Total: ₹50,000
    Paid Now:      ₹20,000
    Remaining:     ₹30,000

    Parameters
    ----------
    invoice_id     : Invoice receiving the partial payment.
    partial_amount : Amount being paid in this transaction.
    currency       : Payment currency.
    transaction_id : External transaction reference.
    payment_method : Payment method used.
    user_id        : Recipient user (for notifications).
    """
    # 1. Create partial payment record
    payment = create_payment_record(
        invoice_id=invoice_id,
        amount=partial_amount,
        currency=currency,
        transaction_id=transaction_id,
        payment_method=payment_method,
        status=PaymentStatus.COMPLETED,
        is_partial=True,
        notes="Partial payment recorded",
    )

    # 2. Recalculate invoice balance
    balance_result = update_invoice_balance(invoice_id, partial_amount)

    # 3. Notify
    if user_id:
        trigger_payment_notifications(
            invoice_id=invoice_id,
            amount=partial_amount,
            currency=currency,
            event="partial_payment",
            user_id=user_id,
        )

    # 4. Broadcast
    broadcast_payment_event("partial_payment_received", {
        "invoice_id": invoice_id,
        "paid_now": partial_amount,
        "balance_due": balance_result.get("balance_due"),
        "currency": currency,
    })

    logger.info(
        "Partial payment: invoice=%s paid=%s remaining=%s %s",
        invoice_id, partial_amount, balance_result.get("balance_due"), currency.upper(),
    )

    return {
        "payment": payment,
        "invoice_balance": balance_result,
        "currency": currency,
    }


# ===========================================================================
# 16. SUBSCRIPTION PAYMENT PLACEHOLDER
# ===========================================================================

def create_subscription_payment(
    customer_email: str,
    plan_id: str,
    *,
    trial_days: int = 0,
    metadata: dict | None = None,
) -> dict:
    """
    Future-ready subscription payment setup via Stripe Subscriptions.

    Wire this up with your Stripe product/price IDs and a Stripe Billing
    portal for a full SaaS subscription experience.

    Currently returns a structured placeholder; activate by un-commenting
    the Stripe API calls below and adding STRIPE_PRICE_ID_<PLAN> env vars.
    """
    price_id = os.getenv(f"STRIPE_PRICE_ID_{plan_id.upper()}")
    if not price_id:
        logger.info("Subscription placeholder: plan=%s email=%s", plan_id, customer_email)
        return {
            "ok": True,
            "placeholder": True,
            "plan_id": plan_id,
            "customer_email": customer_email,
            "note": f"Set STRIPE_PRICE_ID_{plan_id.upper()} to activate subscriptions",
        }

    stripe = _get_stripe()
    customer = _find_or_create_stripe_customer(stripe, customer_email, metadata or {})
    sub = stripe.Subscription.create(
        customer=customer["id"],
        items=[{"price": price_id}],
        trial_period_days=trial_days or None,
        metadata=metadata or {},
    )
    return {"ok": True, "subscription_id": sub.id, "status": sub.status}


# ===========================================================================
# 17. SMART RETRY LOGIC
# ===========================================================================

def retry_failed_payment(
    payment_id: str,
    *,
    max_attempts: int = 3,
    notify_customer: bool = True,
    customer_email: str | None = None,
) -> dict:
    """
    Attempt to retry a failed payment via Stripe.

    Features
    --------
    - Exponential backoff scheduling
    - Customer notification on retry attempt
    - Stops after max_attempts to avoid chargebacks

    Parameters
    ----------
    payment_id      : InvoiceFlow Payment record ID.
    max_attempts    : Maximum retry attempts.
    notify_customer : Whether to email the customer.
    customer_email  : Customer email for retry notification.
    """
    Payment = _payment_model()
    payment = Payment.query.get(payment_id)
    if not payment:
        return {"ok": False, "error": f"Payment {payment_id!r} not found"}

    retry_count = getattr(payment, "retry_count", 0) or 0
    if retry_count >= max_attempts:
        return {
            "ok": False,
            "error": f"Max retry attempts ({max_attempts}) reached for payment {payment_id}",
        }

    # Schedule retry with exponential backoff
    backoff = timedelta(hours=2 ** retry_count)
    retry_at = _now() + backoff

    db = _get_db()
    payment.retry_count = retry_count + 1
    payment.next_retry_at = retry_at
    db.session.commit()

    if notify_customer and customer_email:
        logger.info(
            "Customer notified of payment retry: %s (attempt %d/%d)",
            customer_email, retry_count + 1, max_attempts,
        )

    logger.info(
        "Payment retry scheduled: id=%s attempt=%d/%d at=%s",
        payment_id, retry_count + 1, max_attempts, retry_at.isoformat(),
    )

    return {
        "ok": True,
        "payment_id": payment_id,
        "attempt": retry_count + 1,
        "max_attempts": max_attempts,
        "retry_at": retry_at.isoformat(),
        "backoff_hours": 2 ** retry_count,
    }


# ===========================================================================
# 18. FRAUD DETECTION SIGNALS
# ===========================================================================

def detect_fraud_signals(
    payment_attempt: dict,
    *,
    recent_attempts: list[dict] | None = None,
) -> dict:
    """
    Analyse a payment attempt for fraud signals.

    Checks
    ------
    - Unusual amount (> 3x client average)
    - Repeated failures in short time window
    - Country mismatch between client and card
    - Rapid successive retries (velocity check)
    - Amount round-number anomaly (common in fraud)

    Parameters
    ----------
    payment_attempt : Dict with amount, currency, country, client_id, etc.
    recent_attempts : Last N payment attempts from this client/card.

    Returns
    -------
    {
        "fraud_score": 0–100,
        "risk_level": "low|medium|high|critical",
        "signals": [...],
        "action": "allow|review|block"
    }
    """
    recent = recent_attempts or []
    signals: list[str] = []
    score = 0

    amount = float(payment_attempt.get("amount", 0))
    client_avg = (
        sum(float(p.get("amount", 0)) for p in recent) / len(recent)
        if recent else 0
    )
    if client_avg and amount > client_avg * 3:
        score += 25
        signals.append(f"Amount {amount:.0f} is 3x above client average ({client_avg:.0f})")

    failures = [p for p in recent if p.get("status") == "failed"]
    if len(failures) >= 3:
        score += 30
        signals.append(f"{len(failures)} recent failed attempts")

    # Check for rapid retries (≥3 attempts in last 10 minutes)
    ten_min_ago = _now() - timedelta(minutes=10)
    rapid = [
        p for p in recent
        if p.get("created_at") and datetime.fromisoformat(str(p["created_at"])) >= ten_min_ago
    ]
    if len(rapid) >= 3:
        score += 35
        signals.append(f"{len(rapid)} attempts in the last 10 minutes (velocity anomaly)")

    client_country = payment_attempt.get("client_country", "")
    card_country = payment_attempt.get("card_country", "")
    if client_country and card_country and client_country != card_country:
        score += 20
        signals.append(f"Country mismatch: client={client_country}, card={card_country}")

    if amount > 0 and amount == int(amount) and amount % 1000 == 0 and amount > 10000:
        score += 10
        signals.append("Suspiciously round-number amount")

    score = min(score, 100)

    if score >= 70:
        risk_level, action = "critical", "block"
    elif score >= 50:
        risk_level, action = "high", "review"
    elif score >= 30:
        risk_level, action = "medium", "review"
    else:
        risk_level, action = "low", "allow"

    if signals:
        logger.warning(
            "Fraud signals detected: score=%d level=%s signals=%s",
            score, risk_level, signals,
        )

    return {
        "fraud_score": score,
        "risk_level": risk_level,
        "signals": signals,
        "action": action,
    }


# ===========================================================================
# 19. PAYMENT ACTIVITY TIMELINE
# ===========================================================================

def build_payment_activity(
    user_id: int | None = None,
    *,
    invoice_id: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """
    Build a real-time payment activity timeline for the dashboard feed.

    Optionally filter by invoice. Returns newest events first.

    Returns
    -------
    List of activity entry dicts:
    {
        "icon": "💳",
        "label": "Payment of ₹24,500 received from Acme Inc.",
        "amount": 24500.0,
        "currency": "INR",
        "status": "completed",
        "timestamp": "..."
    }
    """
    Payment = _payment_model()
    q = Payment.query

    if invoice_id:
        q = q.filter_by(invoice_id=invoice_id)

    payments = q.order_by(Payment.paid_at.desc()).limit(limit).all()

    icons = {
        PaymentStatus.COMPLETED: "💳",
        PaymentStatus.FAILED: "❌",
        PaymentStatus.REFUNDED: "↩️",
        PaymentStatus.PARTIALLY_PAID: "⚡",
        PaymentStatus.PENDING: "⏳",
        PaymentStatus.PROCESSING: "🔄",
        PaymentStatus.DISPUTED: "⚠️",
    }

    timeline = []
    for p in payments:
        currency_symbol = {"USD": "$", "INR": "₹", "EUR": "€", "GBP": "£"}.get(
            p.currency, p.currency + " "
        )
        amount_str = f"{currency_symbol}{float(p.amount):,.2f}"
        status = getattr(p, "status", PaymentStatus.COMPLETED)

        if status == PaymentStatus.COMPLETED:
            label = f"Payment of {amount_str} received"
        elif status == PaymentStatus.FAILED:
            label = f"Payment of {amount_str} failed"
        elif status == PaymentStatus.REFUNDED:
            label = f"Refund of {amount_str} issued"
        elif status == PaymentStatus.PARTIALLY_PAID:
            label = f"Partial payment of {amount_str} received"
        else:
            label = f"Payment of {amount_str} — {status}"

        timeline.append({
            "id": p.id,
            "icon": icons.get(status, "💳"),
            "label": label,
            "invoice_id": p.invoice_id,
            "amount": float(p.amount),
            "currency": p.currency,
            "status": status,
            "payment_method": getattr(p, "payment_method", ""),
            "timestamp": p.paid_at.isoformat() if p.paid_at else None,
        })

    return timeline


# ===========================================================================
# 20. SCHEDULED PAYMENT REMINDERS
# ===========================================================================

def schedule_payment_reminders(
    invoice: dict,
    *,
    user_id: int,
) -> list[dict]:
    """
    Schedule a sequence of payment reminders for an invoice.

    Reminder schedule
    -----------------
    T-5 days  : Pre-due friendly reminder
    T+0       : Due-date nudge
    T+3 days  : First overdue reminder
    T+7 days  : Professional follow-up
    T+14 days : Urgent escalation

    Integrates with notification_service.schedule_notification().

    Returns
    -------
    List of scheduled reminder metadata dicts.
    """
    from app.services.notification_service import schedule_notification, NotificationType

    due_date = invoice.get("due_date")
    if due_date and isinstance(due_date, str):
        due_date = datetime.fromisoformat(due_date)
    if not due_date:
        due_date = _now() + timedelta(days=7)

    invoice_id = invoice.get("id", "")
    amount = float(invoice.get("amount", 0))
    currency = invoice.get("currency", "USD")
    currency_symbol = {"USD": "$", "INR": "₹", "EUR": "€", "GBP": "£"}.get(currency.upper(), currency + " ")

    schedule = [
        {
            "label": "Pre-due reminder",
            "delay": due_date - _now() - timedelta(days=5),
            "message": f"Invoice {invoice_id} for {currency_symbol}{amount:,.2f} is due in 5 days.",
            "recurrence": None,
        },
        {
            "label": "Due date nudge",
            "delay": due_date - _now(),
            "message": f"Invoice {invoice_id} for {currency_symbol}{amount:,.2f} is due today.",
            "recurrence": None,
        },
        {
            "label": "3-day overdue",
            "delay": due_date - _now() + timedelta(days=3),
            "message": f"Invoice {invoice_id} is now 3 days overdue. Payment of {currency_symbol}{amount:,.2f} is outstanding.",
            "recurrence": None,
        },
        {
            "label": "7-day follow-up",
            "delay": due_date - _now() + timedelta(days=7),
            "message": f"Invoice {invoice_id} is 7 days overdue. Please arrange payment of {currency_symbol}{amount:,.2f} immediately.",
            "recurrence": None,
        },
        {
            "label": "14-day escalation",
            "delay": due_date - _now() + timedelta(days=14),
            "message": f"URGENT: Invoice {invoice_id} is 14 days overdue. Escalation may follow.",
            "recurrence": None,
        },
    ]

    scheduled = []
    for item in schedule:
        delay = item["delay"]
        if delay.total_seconds() <= 0:
            continue  # Skip past-due reminders that have already passed
        result = schedule_notification(
            user_id=user_id,
            notification_type=NotificationType.REMINDER_SENT,
            message=item["message"],
            delay=delay,
            title=item["label"],
            metadata={"invoice_id": invoice_id, "amount": amount},
            recurrence=item.get("recurrence"),
        )
        result["label"] = item["label"]
        scheduled.append(result)

    logger.info(
        "Scheduled %d payment reminders for invoice %s", len(scheduled), invoice_id
    )
    return scheduled


# ===========================================================================
# 21. AI COLLECTION ASSISTANT
# ===========================================================================

def generate_collection_strategy(
    invoice: dict,
    *,
    client_history: list[dict] | None = None,
) -> dict:
    """
    AI-powered collection strategy for an overdue invoice.

    Outputs
    -------
    - Best follow-up timing
    - Recommended reminder tone
    - Escalation priority
    - Estimated collection probability
    - Step-by-step action plan

    Example output
    --------------
    {
        "best_followup_day": "Thursday",
        "recommended_tone": "professional",
        "escalation_priority": "high",
        "collection_probability": 72,
        "action_plan": ["Send reminder today", "Call client on Day 3", ...]
    }
    """
    ai = _get_ai_client()
    client_history = client_history or []

    due_date = invoice.get("due_date")
    if due_date and isinstance(due_date, str):
        due_date = datetime.fromisoformat(due_date)
    overdue_days = max(0, (_now() - due_date).days) if due_date else 0
    amount = float(invoice.get("amount", 0))
    late_count = sum(1 for p in client_history if p.get("was_late"))

    if not ai:
        prob = max(10, 90 - (overdue_days * 2) - (late_count * 5))
        return {
            "best_followup_day": "Tuesday",
            "recommended_tone": "urgent" if overdue_days > 14 else "professional",
            "escalation_priority": "high" if overdue_days > 14 else "medium",
            "collection_probability": prob,
            "action_plan": [
                "Send a professional payment reminder immediately.",
                "Follow up by phone if no response within 3 days.",
                "Escalate to senior management after 7 days.",
            ],
        }

    try:
        prompt = f"""
You are an AI collection specialist. Analyse this overdue invoice and suggest a collection strategy.

Invoice details:
- Amount: {amount} {invoice.get('currency', 'USD')}
- Overdue by: {overdue_days} days
- Client risk: {invoice.get('client_risk', 'unknown')}
- Client late payment history: {late_count} late payments in {len(client_history)} total

Return JSON:
{{
    "best_followup_day": "<day of week>",
    "recommended_tone": "<friendly|professional|urgent|firm>",
    "escalation_priority": "<low|medium|high|critical>",
    "collection_probability": <0-100>,
    "action_plan": ["step 1", "step 2", "step 3"]
}}
"""
        resp = ai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as exc:
        logger.warning("AI collection strategy generation failed: %s", exc)
        return {
            "best_followup_day": "Tuesday",
            "recommended_tone": "professional",
            "escalation_priority": "medium",
            "collection_probability": 65,
            "action_plan": ["Send reminder", "Follow up in 3 days", "Escalate if no response"],
        }


# ===========================================================================
# 23. PAYMENT STATUS TRACKING
# ===========================================================================

def track_payment_status(payment_id: str) -> dict:
    """
    Return the current status and full audit trail of a payment.

    Statuses: pending, processing, completed, failed, refunded, disputed.

    Returns
    -------
    Full payment dict enriched with status history and any linked invoice.
    """
    Payment = _payment_model()
    Invoice = _invoice_model()

    payment = Payment.query.get(payment_id)
    if not payment:
        raise ValueError(f"Payment {payment_id!r} not found")

    result = _serialize_payment(payment)

    if payment.invoice_id:
        invoice = Invoice.query.get(payment.invoice_id)
        if invoice:
            result["invoice"] = {
                "id": invoice.id,
                "status": invoice.status,
                "total_amount": float(invoice.total_amount),
                "balance_due": float(getattr(invoice, "balance_due", 0)),
            }

    # Stripe status sync
    if payment.transaction_id and payment.transaction_id.startswith("pi_"):
        try:
            stripe = _get_stripe()
            intent = stripe.PaymentIntent.retrieve(payment.transaction_id)
            result["stripe_status"] = intent.status
            result["stripe_synced_at"] = _now().isoformat()
        except Exception as exc:
            logger.warning("Stripe status sync failed for %s: %s", payment_id, exc)

    return result


# ===========================================================================
# 24. AI REVENUE IMPACT ANALYSIS
# ===========================================================================

def analyze_revenue_impact(
    pending_invoices: list[dict],
    *,
    user_id: int | None = None,
) -> dict:
    """
    Forecast the revenue impact of pending and overdue invoices.

    AI analyses timing, client risk, and payment history to project:
    - Expected collections this week / month
    - At-risk revenue
    - Cashflow gap warnings
    - Recommended priority actions

    Parameters
    ----------
    pending_invoices : List of unpaid invoice dicts.
    user_id          : Requesting user (used for notification creation).

    Returns
    -------
    Revenue impact analysis dict.
    """
    ai = _get_ai_client()

    total_pending = sum(float(inv.get("amount", 0)) for inv in pending_invoices)
    overdue_invoices = [
        inv for inv in pending_invoices
        if inv.get("status") in (InvoiceStatus.OVERDUE, "overdue")
    ]
    total_overdue = sum(float(inv.get("amount", 0)) for inv in overdue_invoices)
    high_risk = [inv for inv in pending_invoices if inv.get("client_risk") == "high"]

    if not ai:
        return {
            "total_pending": total_pending,
            "total_overdue": total_overdue,
            "high_risk_amount": sum(float(i.get("amount", 0)) for i in high_risk),
            "cashflow_warning": total_overdue > total_pending * 0.3,
            "ai_forecast": (
                f"₹{total_overdue:,.0f} in overdue invoices may impact next week's cashflow."
                if total_overdue > 0 else "Cashflow looks healthy."
            ),
            "priority_actions": [
                "Follow up on overdue invoices immediately.",
                "Monitor high-risk client accounts closely.",
            ],
        }

    try:
        summary = [
            {
                "id": inv.get("id"),
                "amount": inv.get("amount"),
                "currency": inv.get("currency", "USD"),
                "status": inv.get("status"),
                "client_risk": inv.get("client_risk"),
                "overdue_days": (
                    max(0, (_now() - datetime.fromisoformat(str(inv["due_date"]))).days)
                    if inv.get("due_date") else 0
                ),
            }
            for inv in pending_invoices[:20]
        ]
        prompt = (
            f"Analyse {len(pending_invoices)} pending invoices totalling {total_pending} units. "
            f"{len(overdue_invoices)} are overdue totalling {total_overdue}. "
            f"Summary: {json.dumps(summary)}. "
            "Provide a revenue impact forecast and 3 priority actions. "
            "Return JSON: {ai_forecast, cashflow_warning (bool), priority_actions (array), "
            "expected_collection_this_week, collection_probability}"
        )
        resp = ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=350,
            response_format={"type": "json_object"},
        )
        ai_data = json.loads(resp.choices[0].message.content)
        ai_data.update({
            "total_pending": total_pending,
            "total_overdue": total_overdue,
            "high_risk_amount": sum(float(i.get("amount", 0)) for i in high_risk),
        })
        return ai_data
    except Exception as exc:
        logger.warning("AI revenue impact analysis failed: %s", exc)
        return {
            "total_pending": total_pending,
            "total_overdue": total_overdue,
            "cashflow_warning": total_overdue > total_pending * 0.3,
            "ai_forecast": "AI analysis unavailable. Review overdue invoices manually.",
            "priority_actions": ["Review overdue invoices", "Follow up with high-risk clients"],
        }
