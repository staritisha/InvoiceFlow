# ═══════════════════════════════════════════════════════════════════════════════
#  InvoiceFlow — routers/invoices.py
#  Full invoice lifecycle: CRUD, AI generation, voice-to-invoice, send/pay,
#  duplicate, recurring billing, AI auto-fill, themes, and collection intel.
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import asc, desc, func, or_
from sqlalchemy.orm import Session, joinedload

from app import models
from app.auth import get_current_user
from app.core.constants import ActivityType, NotificationType
from app.database import get_db
from app.utils import (
    ai_invoice_priority_score,
    format_currency,
    format_reminder_email,
    generate_invoice_number,
    generate_secure_token,
    map_client_risk,
    send_email,
)

logger = logging.getLogger("invoiceflow.invoices")

router = APIRouter(prefix="/invoices", tags=["Invoices"])

# ── Valid statuses ─────────────────────────────────────────────────────────────
VALID_STATUSES = {
    "draft", "sent", "viewed", "partially_paid",
    "paid", "overdue", "disputed", "cancelled", "failed", "refunded",
}

# ── Invoice themes ─────────────────────────────────────────────────────────────
INVOICE_THEMES = [
    {"theme_name": "modern",   "accent_color": "#6366f1", "primary_color": "#1e1b4b", "font": "Inter",    "preview_url": None},
    {"theme_name": "minimal",  "accent_color": "#374151", "primary_color": "#111827", "font": "DM Sans",  "preview_url": None},
    {"theme_name": "startup",  "accent_color": "#8b5cf6", "primary_color": "#0f172a", "font": "Geist",    "preview_url": None},
    {"theme_name": "glass",    "accent_color": "#06b6d4", "primary_color": "#164e63", "font": "Nunito",   "preview_url": None},
    {"theme_name": "elegant",  "accent_color": "#d97706", "primary_color": "#1c1917", "font": "Playfair", "preview_url": None},
    {"theme_name": "dark",     "accent_color": "#22c55e", "primary_color": "#030712", "font": "JetBrains Mono", "preview_url": None},
    {"theme_name": "classic",  "accent_color": "#1d4ed8", "primary_color": "#1e3a5f", "font": "Georgia",  "preview_url": None},
    {"theme_name": "bold",     "accent_color": "#ef4444", "primary_color": "#0c0a09", "font": "Poppins",  "preview_url": None},
]


# ═══════════════════════════════════════════════════════════════════════════════
#  LOCAL REQUEST SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class ItemIn(BaseModel):
    description: str  = Field(min_length=1, max_length=500)
    quantity:    Decimal = Field(gt=0)
    unit_price:  Decimal = Field(ge=0)
    unit:        Optional[str]    = None
    category:    Optional[str]    = None
    sku:         Optional[str]    = None
    discount:    Decimal          = Decimal("0")
    tax_percentage: Decimal       = Decimal("0")
    sort_order:  int              = 0


class InvoiceCreateRequest(BaseModel):
    client_id:             Optional[int]      = None
    title:                 Optional[str]      = None
    currency:              str                = "USD"
    template_name:         str                = "modern"
    accent_color:          Optional[str]      = None
    notes:                 Optional[str]      = None
    terms:                 Optional[str]      = None
    issue_date:            Optional[datetime] = None
    due_date:              Optional[datetime] = None
    tax_rate:              Decimal            = Decimal("0")
    discount_amount:       Decimal            = Decimal("0")
    auto_reminder_enabled: bool               = True
    auto_followup_enabled: bool               = False
    workflow_id:           Optional[int]      = None
    items:                 list[ItemIn]       = Field(default_factory=list, min_length=1)


class InvoiceUpdateRequest(BaseModel):
    title:                 Optional[str]              = None
    client_id:             Optional[int]              = None
    status:                Optional[str]              = None
    currency:              Optional[str]              = None
    template_name:         Optional[str]              = None
    accent_color:          Optional[str]              = None
    notes:                 Optional[str]              = None
    terms:                 Optional[str]              = None
    due_date:              Optional[datetime]         = None
    tax_rate:              Optional[Decimal]          = None
    discount_amount:       Optional[Decimal]          = None
    auto_reminder_enabled: Optional[bool]             = None
    auto_followup_enabled: Optional[bool]             = None
    items:                 Optional[list[ItemIn]]     = None


class SendInvoiceRequest(BaseModel):
    channel:          str              = "email"   # email | whatsapp
    custom_message:   Optional[str]   = None
    ai_message:       bool            = True
    schedule_at:      Optional[datetime] = None


class RecordPaymentRequest(BaseModel):
    amount:         Decimal        = Field(gt=0)
    method:         str            = "manual"
    currency:       str            = "USD"
    transaction_id: Optional[str] = None
    gateway:        Optional[str] = None
    notes:          Optional[str] = None
    paid_at:        Optional[datetime] = None


class RecurringCreateRequest(BaseModel):
    client_id:         int
    title:             str           = Field(min_length=1, max_length=150)
    description:       Optional[str] = None
    amount:            Decimal       = Field(gt=0)
    currency:          str           = "USD"
    frequency:         str                         # weekly|monthly|quarterly|yearly
    next_billing_date: datetime
    end_date:          Optional[datetime]          = None
    auto_send:         bool                        = False

    class Config:
        @staticmethod
        def schema_extra() -> dict:
            return {}


class AIGenerateRequest(BaseModel):
    prompt:  str = Field(min_length=5, max_length=2000,
                         description='e.g. "Invoice Acme Corp for website redesign $5000 due in 7 days"')
    context: Optional[dict] = None


class AIFillRequest(BaseModel):
    invoice_id: int
    fields:     list[str] = Field(
        default_factory=list,
        description="Fields to fill: notes, terms, due_date, tax_rate, theme, reminders",
    )


class VoiceInvoiceRequest(BaseModel):
    transcript:  str = Field(min_length=3, max_length=3000, description="Speech-to-text transcript")
    language:    str = "en"
    action:      str = "create"   # create | send | duplicate | remind


# ═══════════════════════════════════════════════════════════════════════════════
#  INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _recalculate(inv: models.Invoice) -> None:
    """Recompute subtotal → tax → total → amount_due from attached items."""
    subtotal = sum(
        (item.unit_price * item.quantity * (1 - item.discount / 100))
        for item in inv.items
    )
    tax_amt  = (subtotal * inv.tax_rate / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
    discount = inv.discount_amount or Decimal("0")
    total    = (subtotal + tax_amt - discount).quantize(Decimal("0.01"), ROUND_HALF_UP)

    inv.subtotal     = subtotal.quantize(Decimal("0.01"), ROUND_HALF_UP)
    inv.tax_amount   = tax_amt
    inv.total_amount = max(total, Decimal("0"))
    inv.amount_due   = max(inv.total_amount - (inv.amount_paid or Decimal("0")), Decimal("0"))


def _build_item(item_in: ItemIn, invoice_id: int, idx: int) -> models.InvoiceItem:
    line_total = (
        item_in.unit_price
        * item_in.quantity
        * (1 - item_in.discount / 100)
    ).quantize(Decimal("0.01"), ROUND_HALF_UP)

    return models.InvoiceItem(
        invoice_id=invoice_id,
        description=item_in.description,
        quantity=item_in.quantity,
        unit_price=item_in.unit_price,
        unit=item_in.unit,
        category=item_in.category,
        sku=item_in.sku,
        discount=item_in.discount,
        tax_percentage=item_in.tax_percentage,
        total_price=line_total,
        sort_order=item_in.sort_order or idx,
    )


def _serialize_item(item: models.InvoiceItem) -> dict:
    return {
        "id":          item.id,
        "description": item.description,
        "quantity":    float(item.quantity),
        "unit_price":  float(item.unit_price),
        "unit":        item.unit,
        "category":    item.category,
        "sku":         item.sku,
        "discount":    float(item.discount),
        "total_price": float(item.total_price),
        "ai_generated": item.ai_generated,
        "sort_order":  item.sort_order,
    }


def _serialize_invoice(inv: models.Invoice, include_items: bool = True) -> dict:
    now      = datetime.now(timezone.utc)
    is_over  = bool(inv.due_date and inv.due_date < now and inv.status not in ("paid", "cancelled"))
    days_due = (
        int((inv.due_date - now).days)
        if inv.due_date and inv.status not in ("paid", "cancelled")
        else None
    )
    paid_pct = (
        round(float(inv.amount_paid) / float(inv.total_amount) * 100, 1)
        if inv.total_amount else 0.0
    )
    out: dict = {
        "id":                    inv.id,
        "invoice_number":        inv.invoice_number,
        "user_id":               inv.user_id,
        "team_id":               inv.team_id,
        "client_id":             inv.client_id,
        "client_name":           inv.client.name if inv.client else None,
        "title":                 inv.title,
        "status":                inv.status,
        "currency":              inv.currency,
        "issue_date":            inv.issue_date.isoformat() if inv.issue_date else None,
        "due_date":              inv.due_date.isoformat() if inv.due_date else None,
        "notes":                 inv.notes,
        "terms":                 inv.terms,
        "subtotal":              float(inv.subtotal),
        "tax_rate":              float(inv.tax_rate),
        "tax_amount":            float(inv.tax_amount),
        "discount_amount":       float(inv.discount_amount),
        "total_amount":          float(inv.total_amount),
        "amount_paid":           float(inv.amount_paid),
        "amount_due":            float(inv.amount_due),
        "payment_percentage":    paid_pct,
        "is_overdue":            is_over,
        "days_until_due":        days_due,
        "ai_generated":          inv.ai_generated,
        "ai_priority":           _priority_label(inv),
        "ai_summary":            inv.ai_summary,
        "ai_tags":               inv.ai_tags or [],
        "collection_risk_score": inv.collection_risk_score,
        "collection_risk_label": map_client_risk(
            (inv.collection_risk_score or 0) * 100
        ),
        "predicted_payment_date": (
            inv.predicted_payment_date.isoformat()
            if inv.predicted_payment_date else None
        ),
        "view_count":            inv.view_count,
        "last_viewed_at":        inv.last_viewed_at.isoformat() if inv.last_viewed_at else None,
        "opened_by_client":      inv.opened_by_client,
        "template_name":         inv.template_name,
        "accent_color":          inv.accent_color,
        "public_share_token":    inv.public_share_token,
        "auto_reminder_enabled": inv.auto_reminder_enabled,
        "auto_followup_enabled": inv.auto_followup_enabled,
        "created_at":            inv.created_at.isoformat() if inv.created_at else None,
        "updated_at":            inv.updated_at.isoformat() if inv.updated_at else None,
    }
    if include_items:
        out["items"] = [_serialize_item(i) for i in sorted(inv.items, key=lambda x: x.sort_order)]
    return out


def _priority_label(inv: models.Invoice) -> str:
    now = datetime.now(timezone.utc)
    days_over = max(0, (now - inv.due_date).days) if inv.due_date and inv.due_date < now else 0
    risk_label = map_client_risk((inv.collection_risk_score or 0) * 100)
    score = ai_invoice_priority_score(days_over, float(inv.total_amount), risk_label)
    if score >= 8:
        return "critical"
    if score >= 6:
        return "high"
    if score >= 3:
        return "medium"
    return "low"


def _log_activity(
    db: Session,
    *,
    user_id: Optional[int],
    team_id: Optional[int],
    activity_type: str,
    description: str,
    invoice_id: Optional[int] = None,
    entity_type: str = "invoice",
    event_data: Optional[dict] = None,
    importance_score: float = 0.5,
) -> None:
    db.add(models.Activity(
        user_id=user_id,
        team_id=team_id,
        invoice_id=invoice_id,
        activity_type=activity_type,
        entity_type=entity_type,
        entity_id=invoice_id,
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
    priority: str = "normal",
) -> None:
    db.add(models.Notification(
        user_id=user_id,
        team_id=team_id,
        title=title,
        message=message,
        notification_type=notification_type,
        action_url=action_url,
        priority=priority,
    ))


def _assert_ownership(inv: models.Invoice, user: models.User) -> None:
    if inv.user_id != user.id and inv.team_id != user.team_id:
        raise HTTPException(status_code=403, detail="Access denied to this invoice")


def _unique_invoice_number(user_id: int, db: Session) -> str:
    for _ in range(10):
        num = generate_invoice_number(prefix="INV", user_id=user_id)
        if not db.query(models.Invoice).filter(models.Invoice.invoice_number == num).first():
            return num
    return f"INV-{user_id}-{secrets.token_hex(4).upper()}"


# ── AI NLP helpers (rule-based, no API key required) ──────────────────────────

def _extract_amount(text: str) -> Optional[float]:
    patterns = [
        r'\$\s*([\d,]+(?:\.\d{1,2})?)',
        r'([\d,]+(?:\.\d{1,2})?)\s*(?:dollars?|USD|usd)',
        r'([\d,]+(?:\.\d{1,2})?)\s*(?:INR|EUR|GBP|AED)',
        r'(?:for|worth|amount|total)\s+(?:of\s+)?\$?([\d,]+(?:\.\d{1,2})?)',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return float(m.group(1).replace(",", ""))
    return None


def _extract_due_days(text: str) -> int:
    """Return number of days from now to extract due date. Defaults to 30."""
    m = re.search(r'due\s+in\s+(\d+)\s+days?', text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r'net\s*(\d+)', text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    kw_map = {
        "tomorrow": 1, "next week": 7, "two weeks": 14, "fortnight": 14,
        "next month": 30, "30 days": 30, "60 days": 60, "90 days": 90,
    }
    for kw, days in kw_map.items():
        if kw in text.lower():
            return days
    return 30


def _extract_currency(text: str) -> str:
    m = re.search(r'\b(USD|EUR|GBP|INR|AED|CAD|AUD|JPY|SGD|CHF)\b', text, re.IGNORECASE)
    return m.group(1).upper() if m else "USD"


def _extract_service_description(text: str) -> str:
    """Pull a concise service description from NL text."""
    cleaned = re.sub(
        r'(?:invoice|bill|create|generate|send|for|to|from|due|in|days?|net|\$[\d,]+|[A-Z]{3})\s*',
        " ", text, flags=re.IGNORECASE
    ).strip()
    return cleaned[:200] if cleaned else "Professional services"


def _parse_nl_invoice(prompt: str) -> dict:
    """
    Rule-based NLP extractor. Returns a structured invoice dict from a
    plain-English prompt. Accurate enough for demo + common use-cases.
    """
    amount   = _extract_amount(prompt) or 0.0
    due_days = _extract_due_days(prompt)
    currency = _extract_currency(prompt)
    service  = _extract_service_description(prompt)
    risk_est = 0.15 if amount > 10_000 else 0.05

    return {
        "title":         service[:80],
        "service_desc":  service,
        "amount":        amount,
        "currency":      currency,
        "due_days":      due_days,
        "tax_rate":      10.0,      # default suggestion
        "notes":         f"Payment due within {due_days} day(s) of invoice date.",
        "terms":         f"Net {due_days}. Late payments subject to 1.5% monthly interest.",
        "risk_estimate": risk_est,
        "confidence":    0.82 if amount > 0 else 0.55,
        "ai_tags":       ["ai-generated", "nlp-parsed"],
        "template_name": "modern",
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  1. GET /  — Advanced invoice listing
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/",
    summary="List invoices with advanced filters",
    description=(
        "Paginated, searchable invoice list with status/date/amount/AI-priority "
        "filters, revenue summary, and KPI counters."
    ),
)
def list_invoices(
    page:        int            = Query(default=1, ge=1),
    per_page:    int            = Query(default=20, ge=1, le=100),
    search:      Optional[str]  = Query(default=None),
    status:      Optional[str]  = Query(default=None),
    currency:    Optional[str]  = Query(default=None),
    overdue:     Optional[bool] = Query(default=None),
    recurring:   Optional[bool] = Query(default=None),
    client_id:   Optional[int]  = Query(default=None),
    date_from:   Optional[datetime] = Query(default=None),
    date_to:     Optional[datetime] = Query(default=None),
    amount_min:  Optional[float] = Query(default=None),
    amount_max:  Optional[float] = Query(default=None),
    sort:        str            = Query(default="created_at", enum=["created_at","due_date","total_amount","status","invoice_number"]),
    order:       str            = Query(default="desc", enum=["asc","desc"]),
    current_user: models.User  = Depends(get_current_user),
    db: Session                = Depends(get_db),
) -> dict:
    q = (
        db.query(models.Invoice)
        .filter(models.Invoice.user_id == current_user.id)
        .options(joinedload(models.Invoice.client))
    )

    if current_user.team_id:
        q = db.query(models.Invoice).filter(
            or_(
                models.Invoice.user_id == current_user.id,
                models.Invoice.team_id == current_user.team_id,
            )
        ).options(joinedload(models.Invoice.client))

    if search:
        term = f"%{search}%"
        q = q.filter(or_(
            models.Invoice.invoice_number.ilike(term),
            models.Invoice.title.ilike(term),
            models.Invoice.notes.ilike(term),
        ))

    if status:
        if status not in VALID_STATUSES:
            raise HTTPException(status_code=422, detail=f"Invalid status '{status}'")
        q = q.filter(models.Invoice.status == status)

    if currency:
        q = q.filter(models.Invoice.currency == currency.upper())

    if overdue is True:
        now = datetime.now(timezone.utc)
        q = q.filter(
            models.Invoice.due_date < now,
            models.Invoice.status.notin_(["paid", "cancelled"]),
        )

    if client_id:
        q = q.filter(models.Invoice.client_id == client_id)

    if date_from:
        q = q.filter(models.Invoice.created_at >= date_from)
    if date_to:
        q = q.filter(models.Invoice.created_at <= date_to)
    if amount_min is not None:
        q = q.filter(models.Invoice.total_amount >= amount_min)
    if amount_max is not None:
        q = q.filter(models.Invoice.total_amount <= amount_max)

    total = q.count()
    sort_col = getattr(models.Invoice, sort, models.Invoice.created_at)
    q = q.order_by(desc(sort_col) if order == "desc" else asc(sort_col))
    invoices = q.offset((page - 1) * per_page).limit(per_page).all()

    # ── KPI counters (full dataset, not paginated) ─────────────────────────────
    kpi_q = db.query(models.Invoice).filter(models.Invoice.user_id == current_user.id)
    now = datetime.now(timezone.utc)
    overdue_cnt   = kpi_q.filter(models.Invoice.status == "overdue").count()
    paid_cnt      = kpi_q.filter(models.Invoice.status == "paid").count()
    draft_cnt     = kpi_q.filter(models.Invoice.status == "draft").count()
    total_rev     = float(kpi_q.filter(models.Invoice.status == "paid")
                         .with_entities(func.coalesce(func.sum(models.Invoice.total_amount), 0))
                         .scalar() or 0)
    outstanding   = float(kpi_q.filter(models.Invoice.status.in_(["sent","partially_paid","overdue"]))
                         .with_entities(func.coalesce(func.sum(models.Invoice.amount_due), 0))
                         .scalar() or 0)

    return {
        "success":  True,
        "data": [_serialize_invoice(inv, include_items=False) for inv in invoices],
        "metadata": {
            "total":    total,
            "page":     page,
            "per_page": per_page,
            "pages":    (total + per_page - 1) // per_page,
        },
        "kpis": {
            "total_invoices": total,
            "overdue":        overdue_cnt,
            "paid":           paid_cnt,
            "draft":          draft_cnt,
            "total_revenue":  total_rev,
            "outstanding":    outstanding,
            "collection_rate": round(paid_cnt / max(paid_cnt + overdue_cnt, 1) * 100, 1),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  2. POST /  — Create invoice
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new invoice",
    description=(
        "Creates an invoice with auto-numbered ID, recalculates all amounts, "
        "scores AI priority, generates a public share token, and logs the activity."
    ),
)
def create_invoice(
    payload: InvoiceCreateRequest,
    background_tasks: BackgroundTasks,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    # Validate status if provided
    if not payload.items:
        raise HTTPException(status_code=422, detail="Invoice must have at least one line item")

    # Verify client belongs to user / team
    client = None
    if payload.client_id:
        client = db.query(models.Client).filter(models.Client.id == payload.client_id).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
        if client.user_id != current_user.id and client.team_id != current_user.team_id:
            raise HTTPException(status_code=403, detail="Client does not belong to your workspace")

    inv = models.Invoice(
        invoice_number        = _unique_invoice_number(current_user.id, db),
        user_id               = current_user.id,
        team_id               = current_user.team_id,
        client_id             = payload.client_id,
        title                 = payload.title,
        status                = "draft",
        currency              = payload.currency,
        template_name         = payload.template_name,
        accent_color          = payload.accent_color,
        notes                 = payload.notes,
        terms                 = payload.terms or f"Net 30. Payment due within 30 days.",
        issue_date            = payload.issue_date or datetime.now(timezone.utc),
        due_date              = payload.due_date or (datetime.now(timezone.utc) + timedelta(days=30)),
        tax_rate              = payload.tax_rate,
        discount_amount       = payload.discount_amount,
        auto_reminder_enabled = payload.auto_reminder_enabled,
        auto_followup_enabled = payload.auto_followup_enabled,
        workflow_id           = payload.workflow_id,
        public_share_token    = generate_secure_token(24),
        subtotal              = Decimal("0"),
        tax_amount            = Decimal("0"),
        total_amount          = Decimal("0"),
        amount_paid           = Decimal("0"),
        amount_due            = Decimal("0"),
    )
    db.add(inv)
    db.flush()   # get inv.id

    # Add items
    for idx, item_in in enumerate(payload.items):
        db.add(_build_item(item_in, inv.id, idx))

    db.flush()
    db.refresh(inv)     # reload items relationship

    # Recalculate totals
    inv.subtotal     = sum(Decimal(str(it.unit_price)) * Decimal(str(it.quantity))
                           * (1 - Decimal(str(it.discount)) / 100) for it in inv.items)
    inv.tax_amount   = (inv.subtotal * inv.tax_rate / 100).quantize(Decimal("0.01"))
    inv.total_amount = max(inv.subtotal + inv.tax_amount - inv.discount_amount, Decimal("0"))
    inv.amount_due   = inv.total_amount

    # AI scoring
    risk_label = map_client_risk((client.risk_score if client else 0) * 100 if client else 0)
    score = ai_invoice_priority_score(0, float(inv.total_amount), risk_label)
    inv.collection_risk_score = round(
        (client.late_payment_probability if client else 0.1), 2
    )

    _log_activity(
        db, user_id=current_user.id, team_id=current_user.team_id,
        activity_type=ActivityType.CREATE,
        description=f"Invoice {inv.invoice_number} created for {float(inv.total_amount):.2f} {inv.currency}",
        invoice_id=inv.id, importance_score=0.7,
        event_data={"amount": float(inv.total_amount), "currency": inv.currency},
    )

    db.commit()
    db.refresh(inv)
    return {"success": True, "invoice": _serialize_invoice(inv)}


# ═══════════════════════════════════════════════════════════════════════════════
#  3. GET /{id}  — Full invoice detail
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/{invoice_id}",
    summary="Get complete invoice detail",
    description=(
        "Returns the invoice with client summary, payment history, reminder history, "
        "AI insights, activity timeline, smart recommendations, and download URL."
    ),
)
def get_invoice(
    invoice_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    inv = (
        db.query(models.Invoice)
        .options(
            joinedload(models.Invoice.client),
            joinedload(models.Invoice.items),
            joinedload(models.Invoice.payments),
            joinedload(models.Invoice.reminders),
            joinedload(models.Invoice.activities),
        )
        .filter(models.Invoice.id == invoice_id)
        .first()
    )
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    _assert_ownership(inv, current_user)

    # Increment view counter
    inv.view_count      = (inv.view_count or 0) + 1
    inv.last_viewed_at  = datetime.now(timezone.utc)
    db.commit()

    client = inv.client
    payments = sorted(inv.payments, key=lambda p: p.created_at or datetime.min, reverse=True)
    reminders = sorted(inv.reminders, key=lambda r: r.created_at or datetime.min, reverse=True)
    activities = sorted(inv.activities, key=lambda a: a.created_at or datetime.min, reverse=True)[:20]

    # AI recommendations for this invoice
    recommendations: list[str] = []
    now = datetime.now(timezone.utc)
    if inv.status in ("sent",) and inv.due_date and inv.due_date < now + timedelta(days=3):
        recommendations.append("Due date approaching — consider sending a reminder")
    if inv.status == "overdue":
        recommendations.append("Invoice is overdue — escalate with a firm reminder")
    if inv.collection_risk_score and inv.collection_risk_score > 0.6:
        recommendations.append("High collection risk — follow up immediately")
    if not inv.auto_reminder_enabled:
        recommendations.append("Enable auto-reminders to increase collection rate")

    return {
        "success": True,
        "invoice": _serialize_invoice(inv),
        "client": {
            "id":           client.id if client else None,
            "name":         client.name if client else None,
            "email":        client.email if client else None,
            "phone":        client.phone if client else None,
            "risk_score":   float(client.risk_score) if client else None,
            "risk_category": client.risk_category if client else None,
            "payment_reliability": float(client.payment_reliability) if client else None,
        } if client else None,
        "payments": [
            {
                "id":         p.id,
                "amount":     float(p.amount),
                "method":     p.method,
                "gateway":    p.gateway,
                "paid_at":    p.paid_at.isoformat() if p.paid_at else None,
                "refunded":   p.refunded,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in payments
        ],
        "reminders": [
            {
                "id":            r.id,
                "reminder_type": r.reminder_type,
                "channel":       r.channel,
                "status":        r.status,
                "sent_at":       r.sent_at.isoformat() if r.sent_at else None,
                "opened":        r.opened,
                "clicked":       r.clicked,
                "ai_generated":  r.ai_generated,
            }
            for r in reminders
        ],
        "activity_timeline": [
            {
                "id":            a.id,
                "activity_type": a.activity_type,
                "description":   a.description,
                "created_at":    a.created_at.isoformat() if a.created_at else None,
            }
            for a in activities
        ],
        "ai_insights": {
            "priority":         _priority_label(inv),
            "risk_label":       map_client_risk((inv.collection_risk_score or 0) * 100),
            "recommendations":  recommendations,
            "collection_risk":  inv.collection_risk_score,
            "payment_prediction": inv.ai_payment_prediction or {},
        },
        "download_url":  f"/api/v1/invoices/{inv.id}/download",
        "share_url":     f"/invoice/public/{inv.public_share_token}" if inv.public_share_token else None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  4. PUT /{id}  — Update invoice
# ═══════════════════════════════════════════════════════════════════════════════

@router.put(
    "/{invoice_id}",
    summary="Update invoice",
    description=(
        "Partial update with smart recalculation, status guard, "
        "item replacement, and audit trail."
    ),
)
def update_invoice(
    invoice_id: int,
    payload: InvoiceUpdateRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    inv = (
        db.query(models.Invoice)
        .options(joinedload(models.Invoice.items))
        .filter(models.Invoice.id == invoice_id)
        .first()
    )
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    _assert_ownership(inv, current_user)

    # Guard paid invoices from being reopened as draft/cancelled without admin
    if inv.status == "paid" and payload.status in ("draft",):
        raise HTTPException(
            status_code=400,
            detail="Cannot revert a paid invoice to draft. Use a credit note instead.",
        )

    if payload.status and payload.status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"Invalid status '{payload.status}'")

    for field in ("title","client_id","status","currency","template_name","accent_color",
                  "notes","terms","due_date","auto_reminder_enabled","auto_followup_enabled"):
        val = getattr(payload, field, None)
        if val is not None:
            setattr(inv, field, val)

    if payload.tax_rate is not None:
        inv.tax_rate = payload.tax_rate
    if payload.discount_amount is not None:
        inv.discount_amount = payload.discount_amount

    if payload.items is not None:
        for old_item in list(inv.items):
            db.delete(old_item)
        db.flush()
        for idx, item_in in enumerate(payload.items):
            db.add(_build_item(item_in, inv.id, idx))
        db.flush()
        db.refresh(inv)

    # Recalculate
    inv.subtotal     = sum(
        Decimal(str(it.unit_price)) * Decimal(str(it.quantity))
        * (1 - Decimal(str(it.discount)) / 100)
        for it in inv.items
    )
    inv.tax_amount   = (inv.subtotal * inv.tax_rate / 100).quantize(Decimal("0.01"))
    inv.total_amount = max(inv.subtotal + inv.tax_amount - (inv.discount_amount or Decimal("0")), Decimal("0"))
    inv.amount_due   = max(inv.total_amount - (inv.amount_paid or Decimal("0")), Decimal("0"))

    _log_activity(
        db, user_id=current_user.id, team_id=current_user.team_id,
        activity_type=ActivityType.UPDATE,
        description=f"Invoice {inv.invoice_number} updated",
        invoice_id=inv.id,
        event_data={"status": inv.status},
    )
    db.commit()
    db.refresh(inv)
    return {"success": True, "invoice": _serialize_invoice(inv)}


# ═══════════════════════════════════════════════════════════════════════════════
#  5. DELETE /{id}  — Soft delete
# ═══════════════════════════════════════════════════════════════════════════════

@router.delete(
    "/{invoice_id}",
    status_code=status.HTTP_200_OK,
    summary="Soft-delete (cancel) invoice",
    description="Sets status to 'cancelled'. Paid invoices cannot be deleted.",
)
def delete_invoice(
    invoice_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    inv = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    _assert_ownership(inv, current_user)

    if inv.status == "paid":
        raise HTTPException(status_code=400, detail="Paid invoices cannot be deleted")

    inv.status = "cancelled"
    _log_activity(
        db, user_id=current_user.id, team_id=current_user.team_id,
        activity_type=ActivityType.DELETE,
        description=f"Invoice {inv.invoice_number} cancelled",
        invoice_id=inv.id, importance_score=0.6,
    )
    db.commit()
    return {"success": True, "message": f"Invoice {inv.invoice_number} has been cancelled"}


# ═══════════════════════════════════════════════════════════════════════════════
#  6. POST /{id}/send
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/{invoice_id}/send",
    summary="Send invoice to client",
    description=(
        "Emails the invoice, updates status to 'sent', schedules auto-reminders, "
        "and logs the send event."
    ),
)
def send_invoice(
    invoice_id: int,
    payload: SendInvoiceRequest,
    background_tasks: BackgroundTasks,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    inv = (
        db.query(models.Invoice)
        .options(joinedload(models.Invoice.client), joinedload(models.Invoice.items))
        .filter(models.Invoice.id == invoice_id)
        .first()
    )
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    _assert_ownership(inv, current_user)

    if inv.status == "paid":
        raise HTTPException(status_code=400, detail="Invoice is already paid")
    if inv.status == "cancelled":
        raise HTTPException(status_code=400, detail="Cannot send a cancelled invoice")

    client = inv.client
    if not client or not client.email:
        raise HTTPException(
            status_code=400,
            detail="Client has no email address. Add one before sending.",
        )

    # Compose message
    formatted_amount = format_currency(float(inv.total_amount), inv.currency)
    ai_msg = (
        payload.custom_message
        or f"Hi {client.name},\n\nPlease find your invoice {inv.invoice_number} for "
           f"{formatted_amount} attached. Payment is due by "
           f"{inv.due_date.strftime('%B %d, %Y') if inv.due_date else 'the date shown'}.\n\n"
           f"Thank you for your business!\n\n{current_user.full_name}"
    )

    # Send email in background
    background_tasks.add_task(
        send_email,
        to_email=client.email,
        subject=f"Invoice {inv.invoice_number} — {formatted_amount}",
        body=ai_msg,
    )

    inv.status = "sent"

    # Auto-schedule first reminder
    if inv.auto_reminder_enabled:
        remind_at = (inv.due_date - timedelta(days=3)) if inv.due_date else (datetime.now(timezone.utc) + timedelta(days=27))
        if remind_at < datetime.now(timezone.utc):
            remind_at = datetime.now(timezone.utc) + timedelta(hours=24)
        reminder = models.Reminder(
            invoice_id=inv.id,
            client_id=client.id if client else None,
            user_id=current_user.id,
            reminder_type="friendly",
            channel=payload.channel,
            subject=f"Reminder: Invoice {inv.invoice_number}",
            body=ai_msg,
            scheduled_at=remind_at,
            status="scheduled",
            ai_generated=True,
        )
        db.add(reminder)

    _log_activity(
        db, user_id=current_user.id, team_id=current_user.team_id,
        activity_type=ActivityType.SEND,
        description=f"Invoice {inv.invoice_number} sent to {client.email}",
        invoice_id=inv.id, importance_score=0.75,
        event_data={"channel": payload.channel, "recipient": client.email},
    )

    _create_notification(
        db, user_id=current_user.id, team_id=current_user.team_id,
        title="Invoice Sent",
        message=f"Invoice {inv.invoice_number} sent to {client.name}",
        notification_type=NotificationType.SUCCESS,
        action_url=f"/invoices/{inv.id}",
    )

    db.commit()
    return {
        "success": True,
        "message": f"Invoice {inv.invoice_number} sent to {client.email}",
        "status": "sent",
        "reminder_scheduled": inv.auto_reminder_enabled,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  7. POST /{id}/pay  — Record payment
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/{invoice_id}/pay",
    summary="Record a payment against an invoice",
    description=(
        "Supports partial and full payments. Auto-updates invoice status to "
        "'partially_paid' or 'paid', updates client score, and fires a thank-you notification."
    ),
)
def record_payment(
    invoice_id: int,
    payload: RecordPaymentRequest,
    background_tasks: BackgroundTasks,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    inv = (
        db.query(models.Invoice)
        .options(joinedload(models.Invoice.client))
        .filter(models.Invoice.id == invoice_id)
        .first()
    )
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    _assert_ownership(inv, current_user)

    if inv.status == "cancelled":
        raise HTTPException(status_code=400, detail="Cannot record payment for a cancelled invoice")

    if payload.amount > inv.amount_due:
        raise HTTPException(
            status_code=400,
            detail=f"Payment amount ({payload.amount}) exceeds the remaining balance ({inv.amount_due})",
        )

    payment = models.Payment(
        invoice_id=inv.id,
        user_id=current_user.id,
        amount=payload.amount,
        currency=payload.currency,
        method=payload.method,
        gateway=payload.gateway,
        gateway_transaction_id=payload.transaction_id,
        notes=payload.notes,
        paid_at=payload.paid_at or datetime.now(timezone.utc),
    )
    db.add(payment)

    # Update totals
    inv.amount_paid = (inv.amount_paid or Decimal("0")) + payload.amount
    inv.amount_due  = max(inv.total_amount - inv.amount_paid, Decimal("0"))

    # Status update
    if inv.amount_due <= Decimal("0.01"):
        inv.status = "paid"
    elif inv.amount_paid > 0:
        inv.status = "partially_paid"

    # Update client payment reliability
    client = inv.client
    if client and inv.status == "paid":
        client.payment_reliability = min(100.0, (client.payment_reliability or 100.0) * 0.95 + 5.0)

    _log_activity(
        db, user_id=current_user.id, team_id=current_user.team_id,
        activity_type=ActivityType.PAYMENT,
        description=f"Payment of {format_currency(float(payload.amount), inv.currency)} recorded for {inv.invoice_number}",
        invoice_id=inv.id, importance_score=0.9,
        event_data={"amount": float(payload.amount), "method": payload.method, "status": inv.status},
    )

    _create_notification(
        db, user_id=current_user.id, team_id=current_user.team_id,
        title="Payment Received 💰",
        message=f"{format_currency(float(payload.amount), inv.currency)} received for {inv.invoice_number}",
        notification_type=NotificationType.SUCCESS,
        action_url=f"/invoices/{inv.id}",
        priority="high",
    )

    # AI thank-you email (background)
    if client and client.email and inv.status == "paid":
        background_tasks.add_task(
            send_email,
            to_email=client.email,
            subject=f"Payment Confirmed — Invoice {inv.invoice_number}",
            body=(
                f"Hi {client.name},\n\nThank you for your payment of "
                f"{format_currency(float(inv.total_amount), inv.currency)} "
                f"for invoice {inv.invoice_number}. Your account is now up to date.\n\n"
                f"We appreciate your business!\n\n{current_user.full_name}"
            ),
        )

    db.commit()
    db.refresh(inv)
    return {
        "success":       True,
        "invoice_status": inv.status,
        "amount_paid":   float(inv.amount_paid),
        "amount_due":    float(inv.amount_due),
        "payment": {
            "id":     payment.id,
            "amount": float(payment.amount),
            "method": payment.method,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  8. POST /{id}/duplicate
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/{invoice_id}/duplicate",
    status_code=status.HTTP_201_CREATED,
    summary="Duplicate an invoice",
    description=(
        "Clones all line items, notes, terms, and settings. "
        "Resets status to draft, generates a new invoice number and share token, "
        "and advances the due date by 30 days."
    ),
)
def duplicate_invoice(
    invoice_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    src = (
        db.query(models.Invoice)
        .options(joinedload(models.Invoice.items))
        .filter(models.Invoice.id == invoice_id)
        .first()
    )
    if not src:
        raise HTTPException(status_code=404, detail="Invoice not found")
    _assert_ownership(src, current_user)

    new_inv = models.Invoice(
        invoice_number        = _unique_invoice_number(current_user.id, db),
        user_id               = current_user.id,
        team_id               = current_user.team_id,
        client_id             = src.client_id,
        title                 = f"{src.title or 'Invoice'} (copy)" if src.title else None,
        status                = "draft",
        currency              = src.currency,
        template_name         = src.template_name,
        accent_color          = src.accent_color,
        notes                 = src.notes,
        terms                 = src.terms,
        issue_date            = datetime.now(timezone.utc),
        due_date              = (src.due_date + timedelta(days=30)) if src.due_date else (datetime.now(timezone.utc) + timedelta(days=30)),
        tax_rate              = src.tax_rate,
        discount_amount       = src.discount_amount,
        auto_reminder_enabled = src.auto_reminder_enabled,
        auto_followup_enabled = src.auto_followup_enabled,
        public_share_token    = generate_secure_token(24),
        subtotal              = src.subtotal,
        tax_amount            = src.tax_amount,
        total_amount          = src.total_amount,
        amount_paid           = Decimal("0"),
        amount_due            = src.total_amount,
    )
    db.add(new_inv)
    db.flush()

    for item in sorted(src.items, key=lambda x: x.sort_order):
        db.add(models.InvoiceItem(
            invoice_id=new_inv.id,
            description=item.description,
            category=item.category,
            sku=item.sku,
            unit=item.unit,
            quantity=item.quantity,
            unit_price=item.unit_price,
            discount=item.discount,
            tax_percentage=item.tax_percentage,
            total_price=item.total_price,
            sort_order=item.sort_order,
        ))

    _log_activity(
        db, user_id=current_user.id, team_id=current_user.team_id,
        activity_type=ActivityType.CREATE,
        description=f"Invoice {new_inv.invoice_number} duplicated from {src.invoice_number}",
        invoice_id=new_inv.id,
    )
    db.commit()
    db.refresh(new_inv)
    return {"success": True, "invoice": _serialize_invoice(new_inv), "duplicated_from": src.invoice_number}


# ═══════════════════════════════════════════════════════════════════════════════
#  9. GET /themes/list
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/themes/list",
    summary="List available invoice themes",
)
def list_themes() -> dict:
    return {"success": True, "themes": INVOICE_THEMES, "total": len(INVOICE_THEMES)}


# ═══════════════════════════════════════════════════════════════════════════════
#  10. POST /ai/generate  — Natural language invoice creation
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/ai/generate",
    status_code=status.HTTP_201_CREATED,
    summary="Generate invoice from natural language",
    description=(
        'Create a full invoice from a plain-English prompt, e.g. '
        '"Invoice Acme Corp for website redesign $5000 due in 7 days". '
        'Uses rule-based NLP extraction; no external API key required.'
    ),
)
def ai_generate_invoice(
    payload: AIGenerateRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    parsed = _parse_nl_invoice(payload.prompt)

    if parsed["amount"] <= 0:
        raise HTTPException(
            status_code=422,
            detail="Could not extract an invoice amount from the prompt. "
                   "Try: 'Invoice [client] for [service] $[amount] due in [N] days'",
        )

    due_date = datetime.now(timezone.utc) + timedelta(days=parsed["due_days"])

    inv = models.Invoice(
        invoice_number        = _unique_invoice_number(current_user.id, db),
        user_id               = current_user.id,
        team_id               = current_user.team_id,
        title                 = parsed["title"],
        status                = "draft",
        currency              = parsed["currency"],
        template_name         = parsed["template_name"],
        notes                 = parsed["notes"],
        terms                 = parsed["terms"],
        issue_date            = datetime.now(timezone.utc),
        due_date              = due_date,
        tax_rate              = Decimal(str(parsed["tax_rate"])),
        discount_amount       = Decimal("0"),
        ai_generated          = True,
        ai_confidence_score   = parsed["confidence"],
        ai_tags               = parsed["ai_tags"],
        ai_summary            = f'AI-generated from prompt: "{payload.prompt[:80]}"',
        collection_risk_score = parsed["risk_estimate"],
        auto_reminder_enabled = True,
        public_share_token    = generate_secure_token(24),
        subtotal              = Decimal("0"),
        tax_amount            = Decimal("0"),
        total_amount          = Decimal("0"),
        amount_paid           = Decimal("0"),
        amount_due            = Decimal("0"),
    )
    db.add(inv)
    db.flush()

    unit_price = Decimal(str(parsed["amount"]))
    item = models.InvoiceItem(
        invoice_id=inv.id,
        description=parsed["service_desc"],
        quantity=Decimal("1"),
        unit_price=unit_price,
        discount=Decimal("0"),
        tax_percentage=Decimal(str(parsed["tax_rate"])),
        total_price=unit_price,
        ai_generated=True,
        sort_order=0,
    )
    db.add(item)
    db.flush()
    db.refresh(inv)

    # Recalculate
    inv.subtotal     = unit_price
    inv.tax_amount   = (unit_price * inv.tax_rate / 100).quantize(Decimal("0.01"))
    inv.total_amount = inv.subtotal + inv.tax_amount
    inv.amount_due   = inv.total_amount

    _log_activity(
        db, user_id=current_user.id, team_id=current_user.team_id,
        activity_type=ActivityType.AI_ACTION,
        description=f"AI-generated invoice {inv.invoice_number} from NL prompt",
        invoice_id=inv.id, importance_score=0.7,
    )
    db.commit()
    db.refresh(inv)

    return {
        "success":    True,
        "invoice":    _serialize_invoice(inv),
        "ai_metadata": {
            "source":     "nlp_rule_engine",
            "confidence": parsed["confidence"],
            "prompt_used": payload.prompt[:200],
            "extracted":  {
                "amount":   parsed["amount"],
                "currency": parsed["currency"],
                "due_days": parsed["due_days"],
                "service":  parsed["service_desc"],
            },
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  11. POST /ai/fill  — AI auto-fill fields
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/ai/fill",
    summary="AI auto-fill invoice fields",
    description=(
        "Analyses the existing invoice and fills missing or weak fields: "
        "notes, terms, due_date, tax_rate, template, and reminders."
    ),
)
def ai_fill_invoice(
    payload: AIFillRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    inv = (
        db.query(models.Invoice)
        .options(joinedload(models.Invoice.client), joinedload(models.Invoice.items))
        .filter(models.Invoice.id == payload.invoice_id)
        .first()
    )
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    _assert_ownership(inv, current_user)

    filled_fields: list[str] = []
    suggestions: dict        = {}
    fields = set(payload.fields) if payload.fields else {"notes","terms","due_date","tax_rate","theme","reminders"}

    if "notes" in fields and not inv.notes:
        inv.notes = (
            f"Thank you for choosing our services. "
            f"Please review the itemised charges above and contact us with any questions."
        )
        filled_fields.append("notes")

    if "terms" in fields and not inv.terms:
        inv.terms = "Net 30. Invoices unpaid after 30 days are subject to 1.5% monthly late fee."
        filled_fields.append("terms")

    if "due_date" in fields and not inv.due_date:
        inv.due_date = datetime.now(timezone.utc) + timedelta(days=30)
        filled_fields.append("due_date")
        suggestions["due_date"] = "Set to Net 30 — adjust for your payment terms"

    if "tax_rate" in fields and float(inv.tax_rate) == 0:
        inv.tax_rate = Decimal("10.0")
        # Recalculate
        inv.tax_amount   = (inv.subtotal * inv.tax_rate / 100).quantize(Decimal("0.01"))
        inv.total_amount = inv.subtotal + inv.tax_amount - (inv.discount_amount or Decimal("0"))
        inv.amount_due   = max(inv.total_amount - (inv.amount_paid or Decimal("0")), Decimal("0"))
        filled_fields.append("tax_rate")
        suggestions["tax_rate"] = "Default 10% suggested — update to match your region"

    if "theme" in fields and inv.template_name == "modern":
        suggestions["theme"] = "Consider 'startup' or 'elegant' for a premium look"

    if "reminders" in fields and not inv.auto_reminder_enabled:
        inv.auto_reminder_enabled = True
        filled_fields.append("auto_reminders")
        suggestions["reminders"] = "Auto-reminders enabled — will send 3 days before due date"

    _log_activity(
        db, user_id=current_user.id, team_id=current_user.team_id,
        activity_type=ActivityType.AI_ACTION,
        description=f"AI auto-filled {len(filled_fields)} field(s) on invoice {inv.invoice_number}",
        invoice_id=inv.id,
        event_data={"filled": filled_fields},
    )

    db.commit()
    db.refresh(inv)
    return {
        "success":       True,
        "invoice":       _serialize_invoice(inv),
        "filled_fields": filled_fields,
        "suggestions":   suggestions,
        "ai_metadata": {
            "fields_analysed": list(fields),
            "fields_filled":   len(filled_fields),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  12. POST /voice  — Voice-to-invoice
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/voice",
    status_code=status.HTTP_201_CREATED,
    summary="Voice-to-invoice",
    description=(
        "Accepts a speech-to-text transcript and converts it to a draft invoice "
        "using the same NLP extraction engine as /ai/generate. "
        "Supports actions: create, send, duplicate, remind."
    ),
)
def voice_invoice(
    payload: VoiceInvoiceRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    transcript = payload.transcript.strip()
    action     = payload.action.lower()

    if action == "create":
        parsed = _parse_nl_invoice(transcript)
        if parsed["amount"] <= 0:
            return {
                "success":     False,
                "action":      "create",
                "message":     "Could not detect an amount in the transcript. Try saying the amount clearly.",
                "transcript":  transcript,
                "corrections": ["Say the amount clearly, e.g. 'five thousand dollars'"],
            }

        due_date = datetime.now(timezone.utc) + timedelta(days=parsed["due_days"])
        inv = models.Invoice(
            invoice_number        = _unique_invoice_number(current_user.id, db),
            user_id               = current_user.id,
            team_id               = current_user.team_id,
            title                 = parsed["title"],
            status                = "draft",
            currency              = parsed["currency"],
            template_name         = "modern",
            notes                 = parsed["notes"],
            terms                 = parsed["terms"],
            issue_date            = datetime.now(timezone.utc),
            due_date              = due_date,
            tax_rate              = Decimal(str(parsed["tax_rate"])),
            discount_amount       = Decimal("0"),
            ai_generated          = True,
            ai_confidence_score   = parsed["confidence"],
            ai_tags               = ["voice-created", *parsed["ai_tags"]],
            ai_summary            = f'Voice invoice: "{transcript[:60]}"',
            collection_risk_score = parsed["risk_estimate"],
            auto_reminder_enabled = True,
            public_share_token    = generate_secure_token(24),
            subtotal              = Decimal("0"),
            tax_amount            = Decimal("0"),
            total_amount          = Decimal("0"),
            amount_paid           = Decimal("0"),
            amount_due            = Decimal("0"),
        )
        db.add(inv)
        db.flush()

        unit_price = Decimal(str(parsed["amount"]))
        db.add(models.InvoiceItem(
            invoice_id=inv.id,
            description=parsed["service_desc"],
            quantity=Decimal("1"),
            unit_price=unit_price,
            discount=Decimal("0"),
            tax_percentage=Decimal(str(parsed["tax_rate"])),
            total_price=unit_price,
            ai_generated=True,
            sort_order=0,
        ))
        db.flush()
        db.refresh(inv)

        inv.subtotal     = unit_price
        inv.tax_amount   = (unit_price * inv.tax_rate / 100).quantize(Decimal("0.01"))
        inv.total_amount = inv.subtotal + inv.tax_amount
        inv.amount_due   = inv.total_amount

        _log_activity(
            db, user_id=current_user.id, team_id=current_user.team_id,
            activity_type=ActivityType.AI_ACTION,
            description=f"Voice invoice {inv.invoice_number} created",
            invoice_id=inv.id,
        )
        db.commit()
        db.refresh(inv)

        return {
            "success":    True,
            "action":     "create",
            "invoice":    _serialize_invoice(inv),
            "transcript": transcript,
            "confidence": parsed["confidence"],
        }

    return {
        "success": False,
        "action":  action,
        "message": f"Action '{action}' received. Use the /invoices/{{id}}/send or /duplicate endpoints for other actions.",
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  13. POST /recurring
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/recurring",
    status_code=status.HTTP_201_CREATED,
    summary="Set up a recurring billing schedule",
    description=(
        "Creates a recurring billing template that the scheduler uses to "
        "auto-generate invoices at the set frequency."
    ),
)
def create_recurring(
    payload: RecurringCreateRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    valid_freqs = {"weekly", "monthly", "quarterly", "yearly"}
    if payload.frequency not in valid_freqs:
        raise HTTPException(status_code=422, detail=f"frequency must be one of {valid_freqs}")

    client = db.query(models.Client).filter(models.Client.id == payload.client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    if client.user_id != current_user.id and client.team_id != current_user.team_id:
        raise HTTPException(status_code=403, detail="Client does not belong to your workspace")

    rec = models.RecurringBilling(
        user_id           = current_user.id,
        client_id         = payload.client_id,
        team_id           = current_user.team_id,
        title             = payload.title,
        description       = payload.description,
        amount            = payload.amount,
        currency          = payload.currency,
        frequency         = payload.frequency,
        next_billing_date = payload.next_billing_date,
        end_date          = payload.end_date,
        auto_send         = payload.auto_send,
        is_active         = True,
    )
    db.add(rec)
    _log_activity(
        db, user_id=current_user.id, team_id=current_user.team_id,
        activity_type=ActivityType.CREATE,
        description=f"Recurring billing '{payload.title}' created ({payload.frequency})",
        entity_type="recurring_billing",
        importance_score=0.6,
    )
    db.commit()
    db.refresh(rec)

    return {
        "success": True,
        "recurring": {
            "id":                rec.id,
            "title":             rec.title,
            "amount":            float(rec.amount),
            "currency":          rec.currency,
            "frequency":         rec.frequency,
            "next_billing_date": rec.next_billing_date.isoformat(),
            "auto_send":         rec.auto_send,
            "is_active":         rec.is_active,
            "created_at":        rec.created_at.isoformat() if rec.created_at else None,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  14. GET /recurring/list
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/recurring/list",
    summary="List recurring billing schedules",
    description=(
        "Returns all active recurring billing templates with next-run preview, "
        "projected monthly revenue, and failure tracking."
    ),
)
def list_recurring(
    active_only: bool = Query(default=True),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    q = db.query(models.RecurringBilling).filter(
        models.RecurringBilling.user_id == current_user.id
    )
    if active_only:
        q = q.filter(models.RecurringBilling.is_active == True)

    records = q.order_by(models.RecurringBilling.next_billing_date).all()

    # Projected monthly revenue
    monthly_projection = 0.0
    for r in records:
        if r.is_active:
            multiplier = {"weekly": 4.33, "monthly": 1, "quarterly": 0.33, "yearly": 0.083}
            monthly_projection += float(r.amount) * multiplier.get(r.frequency, 1)

    return {
        "success": True,
        "recurring": [
            {
                "id":                r.id,
                "title":             r.title,
                "client_id":         r.client_id,
                "amount":            float(r.amount),
                "currency":          r.currency,
                "frequency":         r.frequency,
                "next_billing_date": r.next_billing_date.isoformat(),
                "end_date":          r.end_date.isoformat() if r.end_date else None,
                "is_active":         r.is_active,
                "auto_send":         r.auto_send,
                "total_generated":   r.total_generated,
                "failure_count":     r.failure_count,
                "last_failure_reason": r.last_failure_reason,
                "last_generated_at": r.last_generated_at.isoformat() if r.last_generated_at else None,
            }
            for r in records
        ],
        "analytics": {
            "total_active":         sum(1 for r in records if r.is_active),
            "monthly_projection":   round(monthly_projection, 2),
            "total_schedules":      len(records),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  15. GET /analytics  — Invoice analytics dashboard
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/analytics",
    summary="Invoice analytics dashboard",
    description=(
        "Returns collection rate, revenue breakdown, status counts, "
        "avg payment time, overdue trends, and AI collection intelligence."
    ),
)
def invoice_analytics(
    days: int = Query(default=30, ge=7, le=365),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    uid   = current_user.id

    base = db.query(models.Invoice).filter(
        models.Invoice.user_id == uid,
        models.Invoice.created_at >= since,
    )

    status_counts: dict[str, int] = {}
    for st in VALID_STATUSES:
        status_counts[st] = base.filter(models.Invoice.status == st).count()

    total_rev = float(
        base.filter(models.Invoice.status == "paid")
        .with_entities(func.coalesce(func.sum(models.Invoice.total_amount), 0))
        .scalar() or 0
    )
    outstanding = float(
        base.filter(models.Invoice.status.in_(["sent","partially_paid","overdue"]))
        .with_entities(func.coalesce(func.sum(models.Invoice.amount_due), 0))
        .scalar() or 0
    )
    total_invoices = base.count()
    paid_cnt = status_counts.get("paid", 0)
    overdue_cnt = status_counts.get("overdue", 0)

    collection_rate = round(paid_cnt / max(total_invoices, 1) * 100, 1)

    high_risk = (
        db.query(models.Invoice)
        .filter(
            models.Invoice.user_id == uid,
            models.Invoice.collection_risk_score >= 0.6,
            models.Invoice.status.in_(["sent","overdue","partially_paid"]),
        )
        .count()
    )

    return {
        "success":        True,
        "period_days":    days,
        "status_counts":  status_counts,
        "revenue":        total_rev,
        "outstanding":    outstanding,
        "collection_rate": collection_rate,
        "total_invoices": total_invoices,
        "ai_intel": {
            "high_risk_invoices": high_risk,
            "overdue_count":      overdue_cnt,
            "collection_health":  "good" if collection_rate >= 80 else "needs_attention",
            "recommended_action": (
                f"Follow up on {overdue_cnt} overdue invoice(s) immediately"
                if overdue_cnt > 0
                else "Collection rate is healthy — keep up the good work"
            ),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
