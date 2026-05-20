# ═══════════════════════════════════════════════════════════════════════════════
#  AI Invoice Intelligence Platform — utils.py
#  Comprehensive utility layer: email, formatting, finance, AI helpers,
#  file safety, search, analytics, and API response builders.
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import hashlib
import html
import math
import os
import re
import secrets
import smtplib
import string
import unicodedata
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional

from app.config import settings

# ═══════════════════════════════════════════════════════════════════════════════
#  EMAIL
# ═══════════════════════════════════════════════════════════════════════════════

def send_email(to_email: str, subject: str, body: str, html_body: Optional[str] = None) -> None:
    """
    Send an email via SMTP. Raises if email is not configured or sending fails.
    Supports optional HTML body alongside the plain-text fallback.
    """
    if not settings.email_host:
        raise Exception("Email is not configured. Set EMAIL_HOST in your .env file.")

    msg = MIMEMultipart("alternative")
    msg["From"] = settings.email_from
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    if html_body:
        msg.attach(MIMEText(html_body, "html"))

    try:
        server = smtplib.SMTP(settings.email_host, settings.email_port)
        if settings.email_use_tls:
            server.starttls()
        if settings.email_username and settings.email_password:
            server.login(settings.email_username, settings.email_password)
        server.send_message(msg)
        server.quit()
    except Exception as exc:
        raise Exception(f"Email delivery failed: {exc}") from exc


def format_reminder_email(invoice_number: str, amount: float, tone: str, currency: str = "USD") -> dict:
    """Return subject + body dict for a reminder email based on tone."""
    formatted = format_currency(amount, currency)
    tones = {
        "polite": (
            f"Friendly Reminder — Invoice {invoice_number}",
            f"Hi,\n\nThis is a gentle reminder that invoice {invoice_number} for {formatted} is awaiting payment.\n\nThank you for your business!\n\nInvoiceFlow AI",
        ),
        "firm": (
            f"Payment Due — Invoice {invoice_number}",
            f"Hi,\n\nInvoice {invoice_number} for {formatted} remains unpaid. Please arrange payment at your earliest convenience.\n\nRegards,\nInvoiceFlow AI",
        ),
        "urgent": (
            f"URGENT: Overdue Invoice {invoice_number}",
            f"Hi,\n\nInvoice {invoice_number} for {formatted} is now overdue. Immediate action is required to avoid service interruption.\n\nInvoiceFlow AI",
        ),
    }
    subject, body = tones.get(tone, tones["polite"])
    return {"subject": subject, "body": body, "tone": tone}


def format_ai_email(content: str, recipient_name: str = "", sender_name: str = "InvoiceFlow AI") -> str:
    """Clean and wrap AI-generated email content with a professional signature."""
    content = clean_ai_response(content).strip()
    greeting = f"Hi {recipient_name}," if recipient_name else "Hi,"
    return f"{greeting}\n\n{content}\n\nBest regards,\n{sender_name}"


# ═══════════════════════════════════════════════════════════════════════════════
#  ID & SLUG GENERATORS
# ═══════════════════════════════════════════════════════════════════════════════

def generate_uuid() -> str:
    """Return a new UUID4 string."""
    return str(uuid.uuid4())


def generate_invoice_number(prefix: str = "INV", user_id: Optional[int] = None) -> str:
    """
    Generate a unique, human-readable invoice number.
    Format: INV-2026-05-A3F7
    """
    now = datetime.now(timezone.utc)
    suffix = secrets.token_hex(2).upper()
    parts = [prefix, str(now.year), f"{now.month:02d}", suffix]
    if user_id:
        parts.insert(1, str(user_id))
    return "-".join(parts)


def generate_secure_token(length: int = 32) -> str:
    """Return a cryptographically secure URL-safe token."""
    return secrets.token_urlsafe(length)


def generate_team_slug(name: str) -> str:
    """Convert a team name to a URL-safe slug. e.g. 'My Company Ltd' → 'my-company-ltd'"""
    slug = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^\w\s-]", "", slug).strip().lower()
    slug = re.sub(r"[-\s]+", "-", slug)
    return slug[:50]


def generate_unique_slug(base: str, existing_slugs: list[str]) -> str:
    """Append a numeric suffix to *base* slug until unique within *existing_slugs*."""
    slug = generate_team_slug(base)
    candidate = slug
    counter = 1
    while candidate in existing_slugs:
        candidate = f"{slug}-{counter}"
        counter += 1
    return candidate


def generate_pdf_filename(invoice_number: str) -> str:
    return f"invoice_{invoice_number.replace('-', '_').lower()}.pdf"


def generate_export_filename(entity: str, fmt: str = "csv") -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{entity}_export_{ts}.{fmt}"


# ═══════════════════════════════════════════════════════════════════════════════
#  CURRENCY & FINANCE
# ═══════════════════════════════════════════════════════════════════════════════

_CURRENCY_SYMBOLS: dict[str, str] = {
    "USD": "$", "EUR": "€", "GBP": "£", "INR": "₹",
    "AED": "د.إ", "JPY": "¥", "CAD": "C$", "AUD": "A$",
    "SGD": "S$", "CHF": "Fr", "CNY": "¥", "BRL": "R$",
}


def currency_symbol(currency_code: str) -> str:
    """Return the symbol for a given ISO 4217 currency code."""
    return _CURRENCY_SYMBOLS.get(currency_code.upper(), currency_code)


def format_currency(amount: float, currency: str = "USD", decimals: int = 2) -> str:
    """Format *amount* as a currency string. e.g. format_currency(1234.5, 'INR') → '₹1,234.50'"""
    symbol = currency_symbol(currency)
    value = Decimal(str(amount)).quantize(Decimal(f"0.{'0' * decimals}"), rounding=ROUND_HALF_UP)
    return f"{symbol}{value:,}"


def convert_currency(amount: float, from_currency: str, to_currency: str, rate: float) -> float:
    """Convert *amount* from one currency to another using *rate*."""
    if from_currency.upper() == to_currency.upper():
        return round(amount, 2)
    return round(amount * rate, 2)


def calculate_tax(subtotal: float, tax_rate: float) -> float:
    """Return tax amount for a given subtotal and percentage rate."""
    return round(subtotal * (tax_rate / 100), 2)


def calculate_subtotal(items: list[dict]) -> float:
    """Sum quantity * rate for each item dict. Expects keys: 'quantity', 'rate'."""
    return round(sum(float(i.get("quantity", 0)) * float(i.get("rate", 0)) for i in items), 2)


def calculate_discount(subtotal: float, discount_pct: float = 0, discount_flat: float = 0) -> float:
    """Return discount amount (percentage takes priority over flat)."""
    if discount_pct:
        return round(subtotal * (discount_pct / 100), 2)
    return round(min(discount_flat, subtotal), 2)


def calculate_balance_due(total: float, amount_paid: float) -> float:
    """Return remaining balance. Never negative."""
    return max(round(total - amount_paid, 2), 0.0)


def revenue_growth_pct(current: float, previous: float) -> float:
    """Return percentage growth from *previous* to *current*. Returns 0 if previous is 0."""
    if not previous:
        return 0.0
    return round(((current - previous) / previous) * 100, 2)


def kpi_percentage(part: float, total: float) -> float:
    """Return *part* as a percentage of *total*. Safe division."""
    return round((part / total) * 100, 2) if total else 0.0


def smart_round(value: float, decimals: int = 2) -> float:
    return round(value, decimals)


def abbreviate_number(value: float) -> str:
    """Format large numbers as 1K, 1.2M, etc."""
    abs_val = abs(value)
    sign = "-" if value < 0 else ""
    for threshold, suffix in [(1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")]:
        if abs_val >= threshold:
            return f"{sign}{abs_val / threshold:.1f}{suffix}"
    return f"{sign}{abs_val:.0f}"


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def format_decimal(value: float, decimals: int = 2) -> str:
    return f"{value:.{decimals}f}"


# ═══════════════════════════════════════════════════════════════════════════════
#  DATES & TIME
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_due_date(issue_date: date, net_days: int = 30) -> date:
    """Return a due date *net_days* calendar days after *issue_date*."""
    return issue_date + timedelta(days=net_days)


def recommend_due_date(payment_behavior_days: float = 30) -> date:
    """Suggest a due date based on a client's average payment speed."""
    buffer = max(int(payment_behavior_days * 1.2), 7)
    return date.today() + timedelta(days=buffer)


def days_overdue(due_date: date) -> int:
    """Return how many calendar days past *due_date* today is. 0 if not overdue."""
    delta = (date.today() - due_date).days
    return max(delta, 0)


def business_days_between(start: date, end: date) -> int:
    """Count business days (Mon–Fri) between two dates."""
    count = 0
    current = start
    while current < end:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


def time_ago(dt: datetime) -> str:
    """Return a human-readable 'X time ago' string for a datetime."""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        m = seconds // 60
        return f"{m} minute{'s' if m != 1 else ''} ago"
    if seconds < 86400:
        h = seconds // 3600
        return f"{h} hour{'s' if h != 1 else ''} ago"
    if seconds < 2592000:
        d = seconds // 86400
        return f"{d} day{'s' if d != 1 else ''} ago"
    if seconds < 31536000:
        mo = seconds // 2592000
        return f"{mo} month{'s' if mo != 1 else ''} ago"
    y = seconds // 31536000
    return f"{y} year{'s' if y != 1 else ''} ago"


def format_date_range(start: date, end: date) -> str:
    if start.year == end.year:
        if start.month == end.month:
            return f"{start.strftime('%b %d')}–{end.strftime('%d, %Y')}"
        return f"{start.strftime('%b %d')} – {end.strftime('%b %d, %Y')}"
    return f"{start.strftime('%b %d, %Y')} – {end.strftime('%b %d, %Y')}"


def is_overdue(due_date: date, status: str) -> bool:
    return due_date < date.today() and status not in ("paid", "cancelled")


def late_payment_status(days: int) -> str:
    """Classify overdue severity."""
    if days <= 0:
        return "on_time"
    if days <= 7:
        return "slightly_late"
    if days <= 30:
        return "late"
    if days <= 90:
        return "very_late"
    return "critical"


# ═══════════════════════════════════════════════════════════════════════════════
#  TEXT & STRING
# ═══════════════════════════════════════════════════════════════════════════════

def truncate_text(text: str, max_length: int = 100, suffix: str = "…") -> str:
    return text if len(text) <= max_length else text[:max_length - len(suffix)] + suffix


def sanitize_html(text: str) -> str:
    """Escape HTML special characters to prevent XSS."""
    return html.escape(text)


def clean_xss(text: str) -> str:
    """Strip script tags and event handlers from text."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"on\w+=['\"][^'\"]*['\"]", "", text, flags=re.IGNORECASE)
    return sanitize_html(text)


def strip_markdown(text: str) -> str:
    """Remove common Markdown symbols for plain-text output."""
    text = re.sub(r"[*_~`#>-]", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    return text.strip()


def clean_ai_response(text: str) -> str:
    """Remove AI artifacts like excessive whitespace, repeated punctuation, code fences."""
    text = re.sub(r"```[\w]*\n?", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def clean_ai_prompt(prompt: str, max_length: int = 4000) -> str:
    """Sanitize and cap a prompt before sending to an AI provider."""
    prompt = clean_xss(prompt)
    prompt = re.sub(r"\s+", " ", prompt).strip()
    return prompt[:max_length]


def clean_voice_transcript(transcript: str) -> str:
    """Normalize voice-to-text output for downstream parsing."""
    transcript = transcript.strip().lower()
    transcript = re.sub(r"\s+", " ", transcript)
    return transcript


def parse_voice_command(text: str) -> dict:
    """
    Very lightweight voice command classifier.
    Returns: {"intent": "create"|"send"|"query"|"remind"|"unknown", "raw": text}
    """
    t = clean_voice_transcript(text)
    if any(w in t for w in ["create", "make", "new invoice", "generate"]):
        intent = "create"
    elif any(w in t for w in ["send", "email", "notify"]):
        intent = "send"
    elif any(w in t for w in ["show", "list", "how many", "total", "what"]):
        intent = "query"
    elif any(w in t for w in ["remind", "follow up", "chase"]):
        intent = "remind"
    else:
        intent = "unknown"
    return {"intent": intent, "raw": text}


def parse_ai_response_json(text: str) -> dict:
    """
    Attempt to extract a JSON object from an AI response string.
    Returns the parsed dict or {"raw": text} on failure.
    """
    import json
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {"raw": text}


def format_conversation_context(history: list[dict], max_turns: int = 10) -> str:
    """
    Format the last *max_turns* of conversation history into a plain-text
    prompt context string for an AI model.
    """
    recent = history[-max_turns:]
    lines = [f"{m.get('role', 'user').capitalize()}: {m.get('content', '')}" for m in recent]
    return "\n".join(lines)


def parse_search_query(query: str) -> dict:
    """
    Extract keywords, filters, and intent from a natural-language search string.
    Returns: {"keywords": [...], "filters": {...}, "raw": query}
    """
    query = query.strip()
    filters: dict[str, str] = {}
    for match in re.finditer(r"(\w+):(\S+)", query):
        filters[match.group(1)] = match.group(2)
    keywords = [w for w in re.sub(r"\w+:\S+", "", query).split() if w]
    return {"keywords": keywords, "filters": filters, "raw": query}


def search_ranking_score(query: str, text: str) -> float:
    """Simple term-frequency ranking score between 0 and 1."""
    if not query or not text:
        return 0.0
    terms = query.lower().split()
    text_lower = text.lower()
    hits = sum(1 for t in terms if t in text_lower)
    return round(hits / len(terms), 3)


def safe_filename(filename: str) -> str:
    """Strip unsafe characters from an uploaded filename."""
    filename = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode()
    filename = re.sub(r"[^\w.\-]", "_", filename)
    return filename[:255]


# ═══════════════════════════════════════════════════════════════════════════════
#  FILE VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def validate_file_extension(filename: str, allowed: Optional[list[str]] = None) -> bool:
    allowed = allowed or settings.allowed_extensions
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in allowed


def validate_file_size(size_bytes: int, max_mb: Optional[int] = None) -> bool:
    limit = (max_mb or settings.max_file_size_mb) * 1024 * 1024
    return size_bytes <= limit


# ═══════════════════════════════════════════════════════════════════════════════
#  AI SCORING & ANALYTICS
# ═══════════════════════════════════════════════════════════════════════════════

def ai_invoice_priority_score(
    days_overdue: int,
    amount: float,
    client_risk: str = "low",
) -> int:
    """
    Return a priority score 1–10 for AI-driven follow-up scheduling.
    Higher = more urgent.
    """
    score = 1
    if days_overdue > 0:
        score += min(days_overdue // 7, 4)
    if amount > 10000:
        score += 3
    elif amount > 1000:
        score += 1
    risk_bonus = {"low": 0, "medium": 1, "high": 2}.get(client_risk, 0)
    score += risk_bonus
    return min(score, 10)


def map_client_risk(risk_score: float) -> str:
    """Map a 0–100 risk score to a label."""
    if risk_score >= 70:
        return "high"
    if risk_score >= 40:
        return "medium"
    return "low"


def classify_payment_behavior(avg_days_to_pay: float) -> str:
    if avg_days_to_pay <= 7:
        return "excellent"
    if avg_days_to_pay <= 20:
        return "good"
    if avg_days_to_pay <= 45:
        return "fair"
    return "poor"


def normalize_health_score(raw: float, min_val: float = 0, max_val: float = 100) -> float:
    """Clamp and normalize a health score to 0–100."""
    return round(max(min_val, min(raw, max_val)), 1)


def format_ai_confidence(score: float) -> str:
    """Convert a 0–1 float confidence to a human label."""
    if score >= 0.85:
        return "high"
    if score >= 0.60:
        return "medium"
    return "low"


def format_ai_insight_card(insight: dict) -> dict:
    """Normalize an AI insight dict for frontend consumption."""
    return {
        "id": insight.get("id", generate_uuid()),
        "title": truncate_text(insight.get("title", ""), 80),
        "content": truncate_text(insight.get("content", ""), 300),
        "severity": insight.get("severity", "info"),
        "category": insight.get("category", "general"),
        "is_read": insight.get("is_read", False),
        "created_at": insight.get("created_at", datetime.now(timezone.utc).isoformat()),
    }


def format_revenue_forecast(months: list[float]) -> list[dict]:
    """Turn a list of monthly revenue predictions into chart-ready dicts."""
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    current_month = datetime.now().month
    return [
        {
            "month": month_names[(current_month - 1 + i) % 12],
            "predicted_revenue": round(v, 2),
        }
        for i, v in enumerate(months)
    ]


def format_cash_flow_trend(data: list[dict]) -> list[dict]:
    """Add a trend label (up/down/flat) to each cash-flow data point."""
    result = []
    for i, point in enumerate(data):
        if i == 0:
            trend = "flat"
        else:
            prev = data[i - 1].get("amount", 0)
            curr = point.get("amount", 0)
            trend = "up" if curr > prev else ("down" if curr < prev else "flat")
        result.append({**point, "trend": trend})
    return result


def format_heatmap_data(invoices: list[dict]) -> list[dict]:
    """Aggregate invoice dates into a day-of-week × hour heatmap."""
    grid: dict[tuple[int, int], int] = {}
    for inv in invoices:
        dt_raw = inv.get("created_at") or inv.get("issue_date")
        if not dt_raw:
            continue
        if isinstance(dt_raw, str):
            try:
                dt_raw = datetime.fromisoformat(dt_raw)
            except ValueError:
                continue
        key = (dt_raw.weekday(), dt_raw.hour)
        grid[key] = grid.get(key, 0) + 1

    return [{"day": k[0], "hour": k[1], "count": v} for k, v in sorted(grid.items())]


def format_dashboard_analytics(summary: dict) -> dict:
    """Add human-readable fields to a raw dashboard summary dict."""
    return {
        **summary,
        "total_revenue_formatted": format_currency(summary.get("total_revenue", 0)),
        "unpaid_amount_formatted": format_currency(summary.get("unpaid_amount", 0)),
        "collection_rate": kpi_percentage(
            summary.get("paid_invoices", 0), summary.get("total_invoices", 1)
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  RESPONSE & API HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def success_response(data: Any = None, message: str = "Success") -> dict:
    return {"success": True, "message": message, "data": data}


def error_response(message: str, detail: Any = None, code: Optional[str] = None) -> dict:
    return {"success": False, "message": message, "detail": detail, "code": code}


def paginated_response(items: list, total: int, page: int, page_size: int) -> dict:
    total_pages = math.ceil(total / page_size) if page_size else 1
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_previous": page > 1,
    }


def api_response_wrapper(data: Any, meta: Optional[dict] = None) -> dict:
    return {
        "success": True,
        "data": data,
        "meta": meta or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def filter_none_values(d: dict) -> dict:
    """Remove keys with None values from a dictionary."""
    return {k: v for k, v in d.items() if v is not None}


def extract_nested(d: dict, *keys: str, default: Any = None) -> Any:
    """Safely extract a deeply nested value. e.g. extract_nested(d, 'a', 'b', 'c')"""
    current = d
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key, default)
    return current


def json_safe(obj: Any) -> Any:
    """Recursively convert non-JSON-serializable types (datetime, Decimal, etc.)."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(i) for i in obj]
    return obj


# ═══════════════════════════════════════════════════════════════════════════════
#  NOTIFICATIONS, ACTIVITY & WORKFLOWS
# ═══════════════════════════════════════════════════════════════════════════════

def format_notification_payload(
    type: str,
    title: str,
    message: str,
    user_id: Optional[int] = None,
    data: Optional[dict] = None,
) -> dict:
    return {
        "type": type,
        "title": truncate_text(title, 80),
        "message": truncate_text(message, 300),
        "user_id": user_id,
        "data": data or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def format_activity_log(
    action_type: str,
    entity_type: str,
    entity_id: Any,
    description: str,
    user_id: Optional[int] = None,
    team_id: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> dict:
    return {
        "action_type": action_type,
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "description": truncate_text(description, 200),
        "user_id": user_id,
        "team_id": team_id,
        "metadata": metadata or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def generate_audit_metadata(request_id: Optional[str] = None, ip: Optional[str] = None) -> dict:
    return {
        "request_id": request_id or generate_uuid(),
        "ip": ip,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def format_workflow_execution(workflow_id: int, status: str, log: list[str]) -> dict:
    return {
        "workflow_id": workflow_id,
        "status": status,
        "log": log,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }


def format_ws_event(event_type: str, payload: dict, team_id: Optional[int] = None) -> dict:
    return {
        "event": event_type,
        "payload": payload,
        "team_id": team_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  INVOICE & THEME HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def format_invoice_theme(theme: str) -> dict:
    """Return a config dict for a named invoice PDF theme."""
    themes: dict[str, dict] = {
        "modern":   {"primary": "#2563EB", "font": "Helvetica", "accent": "#DBEAFE"},
        "classic":  {"primary": "#1F2937", "font": "Times-Roman", "accent": "#F3F4F6"},
        "minimal":  {"primary": "#000000", "font": "Helvetica", "accent": "#FFFFFF"},
        "bold":     {"primary": "#DC2626", "font": "Helvetica-Bold", "accent": "#FEE2E2"},
        "startup":  {"primary": "#7C3AED", "font": "Helvetica", "accent": "#EDE9FE"},
        "elegant":  {"primary": "#065F46", "font": "Times-Roman", "accent": "#D1FAE5"},
    }
    return themes.get(theme, themes["modern"])


def format_reminder_tone(tone: str) -> dict:
    """Return tone metadata used by the reminder service."""
    tones = {
        "polite":   {"label": "Polite", "urgency": 1, "delay_days": 3},
        "firm":     {"label": "Firm",   "urgency": 2, "delay_days": 1},
        "urgent":   {"label": "Urgent", "urgency": 3, "delay_days": 0},
    }
    return tones.get(tone, tones["polite"])


# ═══════════════════════════════════════════════════════════════════════════════
#  PERSONALISATION & ONBOARDING
# ═══════════════════════════════════════════════════════════════════════════════

def personalized_greeting(full_name: str, hour: Optional[int] = None) -> str:
    hour = hour if hour is not None else datetime.now().hour
    first = full_name.split()[0] if full_name else "there"
    if 5 <= hour < 12:
        period = "Good morning"
    elif 12 <= hour < 17:
        period = "Good afternoon"
    elif 17 <= hour < 21:
        period = "Good evening"
    else:
        period = "Hello"
    return f"{period}, {first}!"


def ai_onboarding_progress(completed_steps: list[str], all_steps: Optional[list[str]] = None) -> dict:
    all_steps = all_steps or [
        "profile_complete", "first_customer", "first_invoice",
        "payment_setup", "ai_assistant_used", "first_reminder_sent",
    ]
    done = [s for s in all_steps if s in completed_steps]
    remaining = [s for s in all_steps if s not in completed_steps]
    pct = kpi_percentage(len(done), len(all_steps))
    return {
        "completed": done,
        "remaining": remaining,
        "percent_complete": pct,
        "is_complete": pct == 100.0,
    }


def format_suggestion(suggestion: str, context: Optional[str] = None) -> dict:
    return {
        "suggestion": truncate_text(suggestion, 200),
        "context": context,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def format_dashboard_widget_config(widget_type: str, config: dict) -> dict:
    return {
        "widget_type": widget_type,
        "config": config,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
