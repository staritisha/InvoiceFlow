"""
app/services/voice_service.py

Futuristic voice-first AI interface for InvoiceFlow.
Converts speech to structured business actions: invoice creation, analytics
queries, workflow triggers, report generation, and a full conversational
assistant — with multilingual (English / Hindi / Hinglish) support and
real-time streaming responses.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Generator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class VoiceIntent(str, Enum):
    CREATE_INVOICE = "create_invoice"
    SEND_INVOICE = "send_invoice"
    REMIND_CLIENT = "remind_client"
    ANALYTICS_QUERY = "analytics_query"
    GENERATE_REPORT = "generate_report"
    CREATE_WORKFLOW = "create_workflow"
    SEARCH_INVOICE = "search_invoice"
    DASHBOARD_QUERY = "dashboard_query"
    MARK_PAID = "mark_invoice_paid"
    DUPLICATE_INVOICE = "duplicate_invoice"
    CASHFLOW_QUERY = "cashflow_query"
    TOP_CLIENTS_QUERY = "top_clients_query"
    OVERDUE_QUERY = "overdue_query"
    SEND_REMINDER = "send_reminder"
    THANK_CUSTOMER = "thank_customer"
    SCHEDULE_FOLLOWUP = "schedule_followup"
    EXPORT_CSV = "export_csv"
    EMAIL_REPORT = "email_report"
    UNKNOWN = "unknown"


class SupportedLanguage(str, Enum):
    ENGLISH = "en"
    HINDI = "hi"
    HINGLISH = "hinglish"


# ---------------------------------------------------------------------------
# Adapter helpers
# ---------------------------------------------------------------------------

def _get_db():
    try:
        from app import db
        return db
    except ImportError:
        raise RuntimeError("Could not import 'db' from 'app'.")


def _get_openai():
    try:
        import openai
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            logger.warning("OPENAI_API_KEY not set — AI voice features degraded")
            return None
        openai.api_key = key
        return openai
    except ImportError:
        logger.warning("openai not installed — voice AI features disabled")
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


def _voice_log_model():
    try:
        from app.models import VoiceInteraction
        return VoiceInteraction
    except ImportError:
        return None


# ===========================================================================
# 1. AUDIO TRANSCRIPTION ENGINE
# ===========================================================================

def transcribe_audio(
    audio_file,
    *,
    language: str | None = None,
    provider: str = "openai",
) -> dict:
    """
    Transcribe an uploaded audio file to text using Whisper.

    Supported formats: mp3, wav, m4a, webm, ogg, flac.

    Parameters
    ----------
    audio_file : File-like object or file path string.
    language   : ISO 639-1 code hint (e.g. 'en', 'hi'). None = auto-detect.
    provider   : 'openai' (Whisper) or 'google' (placeholder for Cloud STT).

    Returns
    -------
    {
        "text": "Create an invoice for Acme for twenty thousand rupees.",
        "confidence": 0.94,
        "language": "en",
        "duration_seconds": 4.2,
        "provider": "openai_whisper"
    }
    """
    if provider == "openai":
        return _transcribe_with_whisper(audio_file, language=language)
    elif provider == "google":
        return _transcribe_with_google(audio_file, language=language)
    else:
        raise ValueError(f"Unknown transcription provider: {provider!r}")


def _transcribe_with_whisper(audio_file, *, language: str | None = None) -> dict:
    """Transcribe via OpenAI Whisper API."""
    ai = _get_openai()
    if not ai:
        return _dummy_transcription()

    # Handle file-like objects and file paths
    if isinstance(audio_file, (str, os.PathLike)):
        with open(audio_file, "rb") as f:
            audio_bytes = f.read()
        filename = os.path.basename(str(audio_file))
    else:
        audio_bytes = audio_file.read() if hasattr(audio_file, "read") else audio_file
        filename = getattr(audio_file, "filename", "audio.wav")

    # Write to a named temp file (Whisper API requires a real file)
    suffix = os.path.splitext(filename)[-1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        kwargs: dict[str, Any] = {"model": "whisper-1", "response_format": "verbose_json"}
        if language:
            kwargs["language"] = language

        with open(tmp_path, "rb") as f:
            result = ai.audio.transcriptions.create(file=f, **kwargs)

        text = getattr(result, "text", "") or ""
        detected_lang = getattr(result, "language", language or "en")

        # Clean up transcription artifacts
        cleaned = clean_transcription(text)
        lang_detected = detect_language(cleaned)

        logger.info("Whisper transcription: %d chars, lang=%s", len(cleaned), detected_lang)

        return {
            "text": cleaned,
            "raw_text": text,
            "confidence": 0.93,  # Whisper doesn't expose per-transcript confidence; use high default
            "language": lang_detected,
            "duration_seconds": getattr(result, "duration", None),
            "provider": "openai_whisper",
        }
    except Exception as exc:
        logger.error("Whisper transcription failed: %s", exc)
        return {"text": "", "confidence": 0.0, "language": "en", "error": str(exc), "provider": "openai_whisper"}
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _transcribe_with_google(audio_file, *, language: str | None = None) -> dict:
    """Placeholder for Google Cloud Speech-to-Text integration."""
    logger.info("Google STT provider selected — configure google-cloud-speech SDK to activate")
    return {
        "text": "",
        "confidence": 0.0,
        "language": language or "en",
        "provider": "google_stt",
        "error": "Google STT not yet configured. Set GOOGLE_APPLICATION_CREDENTIALS.",
    }


def _dummy_transcription() -> dict:
    """Return an empty transcription when no AI provider is available."""
    return {
        "text": "",
        "confidence": 0.0,
        "language": "en",
        "provider": "none",
        "error": "OPENAI_API_KEY not configured",
    }


# ===========================================================================
# 2. VOICE COMMAND PARSER
# ===========================================================================

def parse_voice_command(text: str) -> dict:
    """
    Parse a transcribed text string into a structured business command.

    Uses rule-based pre-processing followed by AI NLU extraction.

    Parameters
    ----------
    text : Transcribed speech text.

    Returns
    -------
    {
        "intent": "create_invoice",
        "client": "Acme",
        "amount": 20000,
        "currency": "INR",
        "due_date": "2026-05-29",
        "items": [...],
        "raw_text": "...",
        "confidence": 0.91
    }
    """
    if not text or not text.strip():
        return {"intent": VoiceIntent.UNKNOWN, "raw_text": text, "confidence": 0.0}

    text = text.strip()
    intent = classify_command(text)
    confidence = calculate_command_confidence(text, intent)

    parsed: dict[str, Any] = {
        "intent": intent,
        "raw_text": text,
        "confidence": confidence["confidence"],
        "needs_confirmation": confidence["needs_confirmation"],
    }

    if intent == VoiceIntent.CREATE_INVOICE:
        invoice_data = extract_invoice_data(text)
        parsed.update(invoice_data)

    elif intent in (VoiceIntent.ANALYTICS_QUERY, VoiceIntent.DASHBOARD_QUERY,
                    VoiceIntent.CASHFLOW_QUERY, VoiceIntent.OVERDUE_QUERY,
                    VoiceIntent.TOP_CLIENTS_QUERY):
        parsed["query"] = text

    elif intent == VoiceIntent.REMIND_CLIENT:
        parsed["client"] = _extract_client_name(text)
        parsed["delay"] = _extract_delay(text)

    elif intent == VoiceIntent.SEND_INVOICE:
        parsed["client"] = _extract_client_name(text)

    elif intent == VoiceIntent.GENERATE_REPORT:
        parsed["report_type"] = _extract_report_type(text)

    return parsed


# ===========================================================================
# 3. VOICE INTENT CLASSIFICATION
# ===========================================================================

def classify_command(text: str) -> str:
    """
    Classify a transcribed command into a VoiceIntent category.

    Uses keyword matching first (fast, offline), then AI disambiguation for
    ambiguous inputs.

    Parameters
    ----------
    text : Cleaned transcription string.

    Returns
    -------
    VoiceIntent value string.
    """
    text_lower = text.lower()

    # Keyword rule map — ordered by specificity
    rules = [
        (VoiceIntent.CREATE_INVOICE, [
            "create invoice", "new invoice", "make invoice", "generate invoice",
            "invoice bana", "invoice banao", "invoice for", "bill for", "bana do",
        ]),
        (VoiceIntent.SEND_INVOICE, [
            "send invoice", "email invoice", "share invoice", "invoice bhejo",
        ]),
        (VoiceIntent.MARK_PAID, [
            "mark paid", "mark as paid", "paid", "payment received", "paid kar",
        ]),
        (VoiceIntent.DUPLICATE_INVOICE, [
            "duplicate invoice", "copy invoice", "same invoice",
        ]),
        (VoiceIntent.REMIND_CLIENT, [
            "remind", "send reminder", "reminder bhejo", "follow up", "nudge",
        ]),
        (VoiceIntent.THANK_CUSTOMER, [
            "thank", "thanks", "shukriya",
        ]),
        (VoiceIntent.SCHEDULE_FOLLOWUP, [
            "schedule follow", "follow up tomorrow", "follow up next",
        ]),
        (VoiceIntent.OVERDUE_QUERY, [
            "overdue", "unpaid", "pending invoice", "due invoice",
        ]),
        (VoiceIntent.CASHFLOW_QUERY, [
            "cashflow", "cash flow", "forecast", "next month revenue",
        ]),
        (VoiceIntent.TOP_CLIENTS_QUERY, [
            "top client", "best client", "highest paying", "most revenue",
        ]),
        (VoiceIntent.ANALYTICS_QUERY, [
            "revenue", "how much", "total", "analytics", "stats", "kitna",
            "performance", "this month", "last month",
        ]),
        (VoiceIntent.GENERATE_REPORT, [
            "report", "pdf", "summary report", "generate report", "executive report",
        ]),
        (VoiceIntent.EXPORT_CSV, [
            "export", "csv", "download", "excel",
        ]),
        (VoiceIntent.EMAIL_REPORT, [
            "email report", "send report",
        ]),
        (VoiceIntent.CREATE_WORKFLOW, [
            "workflow", "automate", "automation", "trigger",
        ]),
        (VoiceIntent.SEARCH_INVOICE, [
            "find invoice", "search invoice", "show invoice", "invoice number",
        ]),
        (VoiceIntent.DASHBOARD_QUERY, [
            "dashboard", "overview", "status", "summary",
        ]),
    ]

    for intent, keywords in rules:
        if any(kw in text_lower for kw in keywords):
            return intent

    # AI disambiguation for unclear inputs
    ai = _get_openai()
    if ai:
        return _ai_classify_intent(text, ai)

    return VoiceIntent.UNKNOWN


def _ai_classify_intent(text: str, ai) -> str:
    """Use GPT to classify intent when rules don't match."""
    intents = [i.value for i in VoiceIntent if i != VoiceIntent.UNKNOWN]
    try:
        resp = ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": (
                    f"Classify this voice command for an invoice management platform "
                    f"into exactly one intent.\n\nCommand: \"{text}\"\n\n"
                    f"Valid intents: {', '.join(intents)}\n\n"
                    "Return JSON: {\"intent\": \"...\"}"
                ),
            }],
            max_tokens=50,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        return data.get("intent", VoiceIntent.UNKNOWN)
    except Exception as exc:
        logger.warning("AI intent classification failed: %s", exc)
        return VoiceIntent.UNKNOWN


# ===========================================================================
# 4. VOICE-TO-INVOICE CREATION
# ===========================================================================

def create_invoice_from_voice(
    text: str,
    *,
    user_id: int,
    auto_save: bool = True,
) -> dict:
    """
    Full pipeline: transcribed text → structured invoice → saved record.

    Flow
    ----
    Voice text
    → AI extraction (client, items, amount, due date)
    → Structured invoice dict
    → AI-generated item descriptions
    → Save invoice (if auto_save=True)
    → Return invoice preview + AI summary

    Parameters
    ----------
    text      : Transcribed voice command.
    user_id   : Owning user.
    auto_save : Whether to immediately persist the invoice.

    Returns
    -------
    {
        "invoice": {...},
        "ai_summary": "...",
        "saved": True,
        "suggestions": [...]
    }
    """
    invoice_data = extract_invoice_data(text)

    # Assign defaults
    invoice_id = _new_id()
    due_date = invoice_data.get("due_date") or (_now() + timedelta(days=30)).strftime("%Y-%m-%d")
    currency = invoice_data.get("currency", "INR")
    items = invoice_data.get("items", [])
    amount = invoice_data.get("amount", sum(
        float(i.get("quantity", 1)) * float(i.get("rate", 0)) for i in items
    ))

    invoice = {
        "id": invoice_id,
        "number": f"INV-{_now().strftime('%Y%m')}-{invoice_id[:4].upper()}",
        "client": invoice_data.get("client", "Unknown Client"),
        "client_id": invoice_data.get("client_id"),
        "items": items,
        "amount": amount,
        "currency": currency,
        "due_date": due_date,
        "notes": invoice_data.get("notes", ""),
        "status": "draft",
        "source": "voice",
        "created_by": user_id,
        "created_at": _now().isoformat(),
    }

    # AI-generate missing item descriptions
    if items and not all(i.get("description") for i in items):
        invoice["items"] = _enrich_item_descriptions(items)

    if auto_save:
        invoice = _save_voice_invoice(invoice)

    ai_summary = _summarise_invoice(invoice)
    suggestions = generate_voice_suggestions(context={"last_action": "create_invoice", "invoice": invoice})

    logger.info(
        "Voice invoice created: %s amount=%s%s user=%s",
        invoice.get("number"), amount, currency, user_id,
    )

    return {
        "invoice": invoice,
        "ai_summary": ai_summary,
        "saved": auto_save,
        "suggestions": suggestions,
    }


def _save_voice_invoice(invoice: dict) -> dict:
    """Persist the voice-generated invoice using the Invoice model."""
    try:
        from app.models import Invoice
        from app import db
        inv = Invoice(
            id=invoice["id"],
            number=invoice["number"],
            client_id=invoice.get("client_id"),
            total_amount=invoice.get("amount", 0),
            currency=invoice.get("currency", "INR"),
            status="draft",
            notes=invoice.get("notes", ""),
            source="voice",
            created_at=_now(),
            updated_at=_now(),
        )
        db.session.add(inv)
        db.session.commit()
        invoice["saved"] = True
    except Exception as exc:
        logger.warning("Voice invoice save failed: %s", exc)
        invoice["saved"] = False
        invoice["save_error"] = str(exc)
    return invoice


def _enrich_item_descriptions(items: list[dict]) -> list[dict]:
    """Use AI to fill in professional item descriptions."""
    ai = _get_openai()
    if not ai:
        return items
    try:
        prompt = (
            f"Enrich these invoice line items with professional descriptions: "
            f"{json.dumps(items)}. Return the same array with improved 'description' strings."
        )
        resp = ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        return data if isinstance(data, list) else data.get("items", items)
    except Exception:
        return items


def _summarise_invoice(invoice: dict) -> str:
    ai = _get_openai()
    if not ai:
        return (
            f"Invoice {invoice.get('number', '')} created for {invoice.get('client', 'client')} "
            f"— {invoice.get('currency', '')} {invoice.get('amount', 0):,.0f} due {invoice.get('due_date', '')}."
        )
    try:
        resp = ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": f"Write a 1-sentence summary of this invoice: {json.dumps(invoice)}",
            }],
            max_tokens=80,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return f"Invoice {invoice.get('number', '')} created successfully via voice."


# ===========================================================================
# 5. AI NATURAL LANGUAGE UNDERSTANDING — INVOICE DATA EXTRACTION
# ===========================================================================

def extract_invoice_data(text: str) -> dict:
    """
    Use AI to extract structured invoice fields from free-form speech.

    Handles:
    - Client names (including company names)
    - Monetary amounts (with currency detection)
    - Line items with quantity and rate
    - Due dates (relative and absolute)
    - Notes, tax, and currency

    Example
    -------
    Input:  "2 UI designs at 15k each for Acme, due next Friday"
    Output: {
        "client": "Acme",
        "items": [{"description": "UI Design", "quantity": 2, "rate": 15000}],
        "amount": 30000,
        "currency": "INR",
        "due_date": "2026-05-29"
    }
    """
    ai = _get_openai()

    # Pre-process relative dates before sending to AI
    text_with_dates = _resolve_relative_dates_in_text(text)

    if not ai:
        return _rule_based_extraction(text)

    today_str = _now().strftime("%Y-%m-%d")
    try:
        prompt = f"""
Extract invoice data from this voice command. Today is {today_str}.

Voice command: "{text_with_dates}"

Return JSON with these fields (omit fields not mentioned):
{{
    "client": "<client name or null>",
    "items": [
        {{"description": "<item>", "quantity": <number>, "rate": <number per unit>}}
    ],
    "amount": <total amount as number, or null if unknown>,
    "currency": "<INR|USD|EUR|GBP|SGD — default INR if rupees/₹ mentioned, USD otherwise>",
    "due_date": "<YYYY-MM-DD or null>",
    "tax_rate": <0-100 or null>,
    "notes": "<any special notes or null>"
}}

Currency hints: rupee/rupees/₹/rs = INR; dollar/$= USD; pound/£ = GBP; euro/€ = EUR.
Amount hints: k/K = ×1000; lakh = ×100000; thousand = ×1000.
"""
        resp = ai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)

        # Recalculate amount from items if not provided
        if not data.get("amount") and data.get("items"):
            data["amount"] = sum(
                float(i.get("quantity", 1)) * float(i.get("rate", 0))
                for i in data["items"]
            )

        return data

    except Exception as exc:
        logger.warning("AI invoice extraction failed: %s", exc)
        return _rule_based_extraction(text)


def _rule_based_extraction(text: str) -> dict:
    """Lightweight regex-based fallback extraction."""
    result: dict[str, Any] = {}

    # Amount detection: 20000 / 20k / 20 thousand / 2 lakh
    amount_patterns = [
        (r"(?:rs\.?|₹|inr|rupees?)\s*([\d,]+(?:\.\d+)?)\s*(?:k|thousand)?", 1000),
        (r"([\d,]+(?:\.\d+)?)\s*(?:lakh|lac)", 100000),
        (r"([\d,]+(?:\.\d+)?)\s*(?:k|thousand)", 1000),
        (r"(?:rs\.?|₹|inr|rupees?)\s*([\d,]+(?:\.\d+)?)", 1),
        (r"\$\s*([\d,]+(?:\.\d+)?)", 1),
        (r"([\d,]+(?:\.\d+)?)\s*(?:dollars?|usd)", 1),
        (r"([\d,]+)\s*(?:rupees?|rs\.?|₹)", 1),
    ]

    for pattern, multiplier in amount_patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            raw = m.group(1).replace(",", "")
            result["amount"] = float(raw) * multiplier
            result["currency"] = "INR" if multiplier in (1000, 100000) or "rupee" in text.lower() else "USD"
            break

    # Due date
    result["due_date"] = _extract_due_date(text)

    # Client name: text after "for" and before amount/due keywords
    client_match = re.search(
        r"\bfor\b\s+([A-Za-z][A-Za-z\s]{1,30}?)(?:\s+(?:for|due|at|of|worth|\d|₹|\$))",
        text, re.IGNORECASE,
    )
    if client_match:
        result["client"] = client_match.group(1).strip()

    return result


def _resolve_relative_dates_in_text(text: str) -> str:
    """Replace relative date phrases with ISO date strings before AI extraction."""
    now = _now()
    replacements = {
        "today": now.strftime("%Y-%m-%d"),
        "tomorrow": (now + timedelta(days=1)).strftime("%Y-%m-%d"),
        "day after tomorrow": (now + timedelta(days=2)).strftime("%Y-%m-%d"),
        "next week": (now + timedelta(weeks=1)).strftime("%Y-%m-%d"),
        "end of month": now.replace(day=28).strftime("%Y-%m-%d"),
        "end of the month": now.replace(day=28).strftime("%Y-%m-%d"),
        "in 7 days": (now + timedelta(days=7)).strftime("%Y-%m-%d"),
        "in 14 days": (now + timedelta(days=14)).strftime("%Y-%m-%d"),
        "in 30 days": (now + timedelta(days=30)).strftime("%Y-%m-%d"),
        "in a week": (now + timedelta(weeks=1)).strftime("%Y-%m-%d"),
        "in a month": (now + timedelta(days=30)).strftime("%Y-%m-%d"),
    }

    # Next <weekday>
    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for i, day in enumerate(weekdays):
        match = re.search(rf"next {day}", text, re.IGNORECASE)
        if match:
            days_ahead = (i - now.weekday()) % 7 or 7
            date_str = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
            text = re.sub(rf"next {day}", date_str, text, flags=re.IGNORECASE)

    for phrase, date_str in replacements.items():
        text = re.sub(re.escape(phrase), date_str, text, flags=re.IGNORECASE)

    return text


# ===========================================================================
# 6. SMART DATE RECOGNITION
# ===========================================================================

def parse_relative_dates(date_phrase: str) -> str | None:
    """
    Convert a relative date phrase to an ISO 8601 date string.

    Supported phrases
    -----------------
    tomorrow, next monday, next week, end of month, in 7 days,
    in 2 weeks, next friday, in a month, day after tomorrow

    Returns
    -------
    "YYYY-MM-DD" string, or None if phrase cannot be parsed.
    """
    now = _now()
    phrase = date_phrase.lower().strip()

    simple_map = {
        "today": now,
        "tomorrow": now + timedelta(days=1),
        "day after tomorrow": now + timedelta(days=2),
        "next week": now + timedelta(weeks=1),
        "end of month": now.replace(day=28),
        "end of the month": now.replace(day=28),
        "in a week": now + timedelta(weeks=1),
        "in a month": now + timedelta(days=30),
        "in 7 days": now + timedelta(days=7),
        "in 14 days": now + timedelta(days=14),
        "in 30 days": now + timedelta(days=30),
    }

    if phrase in simple_map:
        return simple_map[phrase].strftime("%Y-%m-%d")

    # "in N days/weeks"
    m = re.match(r"in (\d+) (days?|weeks?|months?)", phrase)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if "week" in unit:
            return (now + timedelta(weeks=n)).strftime("%Y-%m-%d")
        elif "month" in unit:
            return (now + timedelta(days=30 * n)).strftime("%Y-%m-%d")
        return (now + timedelta(days=n)).strftime("%Y-%m-%d")

    # "next <weekday>"
    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    m = re.match(r"next (\w+)", phrase)
    if m and m.group(1) in weekdays:
        target = weekdays.index(m.group(1))
        days_ahead = (target - now.weekday()) % 7 or 7
        return (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    return None


def _extract_due_date(text: str) -> str | None:
    date_phrases = [
        "tomorrow", "next monday", "next tuesday", "next wednesday", "next thursday",
        "next friday", "next saturday", "next sunday", "next week", "end of month",
        "end of the month", "in 7 days", "in 14 days", "in 30 days", "in a week",
        "in a month",
    ]
    for phrase in date_phrases:
        if phrase in text.lower():
            return parse_relative_dates(phrase)
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if m:
        return m.group(1)
    return None


# ===========================================================================
# 7. VOICE ANALYTICS QUERIES
# ===========================================================================

def process_analytics_query(
    text: str,
    *,
    user_id: int,
) -> dict:
    """
    Answer a natural-language analytics question using live data.

    Examples
    --------
    "How much revenue did we generate this month?"
    "Show overdue invoices"
    "Who is the top paying client?"

    Returns
    -------
    {
        "query": "...",
        "answer": "...",
        "data": {...},
        "intent": "analytics_query"
    }
    """
    from app.services.report_service import (
        _collect_report_data, build_kpi_sections, generate_health_score_report
    )

    data = _collect_report_data(user_id, {})
    kpis = build_kpi_sections(data)
    health = generate_health_score_report(data)

    context = {
        "total_revenue": data.get("total_revenue", 0),
        "total_overdue": data.get("total_overdue", 0),
        "invoice_count": data.get("invoice_count", 0),
        "paid_count": data.get("paid_count", 0),
        "top_clients": data.get("top_clients", [])[:3],
        "health_score": health.get("score", 0),
        "overdue_count": data.get("overdue_analysis", {}).get("count", 0),
        "monthly_revenue": data.get("monthly_revenue", {}),
    }

    ai = _get_openai()
    if not ai:
        return _rule_based_analytics_answer(text, context)

    try:
        prompt = (
            f"You are an AI business analyst assistant. "
            f"Answer this question in 1-2 sentences using the provided data.\n\n"
            f"Question: \"{text}\"\n\n"
            f"Business data: {json.dumps(context)}\n\n"
            "Be specific, use actual numbers, and sound like a CFO."
        )
        resp = ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
        )
        answer = resp.choices[0].message.content.strip()
    except Exception as exc:
        logger.warning("AI analytics query failed: %s", exc)
        answer = _rule_based_analytics_answer(text, context).get("answer", "")

    return {"query": text, "answer": answer, "data": context, "intent": VoiceIntent.ANALYTICS_QUERY}


def _rule_based_analytics_answer(text: str, context: dict) -> dict:
    text_lower = text.lower()
    revenue = context.get("total_revenue", 0)
    overdue = context.get("total_overdue", 0)
    health = context.get("health_score", 0)
    top = context.get("top_clients", [{}])[0] if context.get("top_clients") else {}

    if "revenue" in text_lower or "how much" in text_lower:
        answer = f"Total revenue collected is ${revenue:,.2f}."
    elif "overdue" in text_lower:
        answer = f"${overdue:,.2f} is outstanding in {context.get('overdue_count', 0)} overdue invoices."
    elif "health" in text_lower:
        answer = f"Business health score is {health}/100."
    elif "top client" in text_lower or "best client" in text_lower:
        answer = f"Top client by revenue: {top.get('client_id', 'N/A')} (${top.get('revenue', 0):,.0f})."
    else:
        answer = f"Revenue: ${revenue:,.2f} | Overdue: ${overdue:,.2f} | Health: {health}/100."

    return {"query": text, "answer": answer, "data": context, "intent": VoiceIntent.ANALYTICS_QUERY}


# ===========================================================================
# 8. CONVERSATIONAL VOICE ASSISTANT
# ===========================================================================

def voice_chat_assistant(
    message: str,
    *,
    user_id: int,
    conversation_history: list[dict] | None = None,
    stream: bool = False,
) -> dict:
    """
    Multi-turn conversational AI assistant with business context awareness.

    Features
    --------
    - Contextual memory via conversation_history
    - Business context injection (overdue invoices, risky clients, revenue)
    - Follow-up question handling
    - Streaming support (set stream=True to get a generator)

    Parameters
    ----------
    message              : Latest user message.
    user_id              : Active user.
    conversation_history : List of prior {role, content} turns.
    stream               : If True, returns a streaming generator.

    Returns
    -------
    {"response": "...", "intent": "...", "actions": [...], "history": [...]}
    """
    ai = _get_openai()
    conversation_history = conversation_history or []

    if not ai:
        return {
            "response": "AI assistant requires OPENAI_API_KEY to be configured.",
            "intent": VoiceIntent.UNKNOWN,
            "actions": [],
            "history": conversation_history,
        }

    # Build business context
    business_context = _build_business_context(user_id)

    system_prompt = f"""You are an intelligent AI business assistant for InvoiceFlow, an invoice management platform.

Current business context:
{json.dumps(business_context, indent=2)}

Today: {_now().strftime("%A, %B %d, %Y")}

You can:
- Create invoices ("Create invoice for Acme for ₹20,000 due next Friday")
- Send reminders ("Remind Acme about their overdue invoice")
- Generate reports ("Generate this month's executive report")
- Answer analytics questions ("How much revenue this month?")
- Trigger workflows ("Automate overdue reminders")

Be concise, helpful, and proactive. After completing a task, suggest the next logical action.
If you need clarification, ask a single focused question.
Respond in the same language as the user (English/Hindi/Hinglish)."""

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(conversation_history[-10:])  # Keep last 10 turns for context
    messages.append({"role": "user", "content": message})

    try:
        if stream:
            return {
                "response": stream_voice_response(messages),
                "intent": classify_command(message),
                "actions": [],
                "history": messages,
                "streaming": True,
            }

        resp = ai.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=300,
        )
        response_text = resp.choices[0].message.content.strip()

        # Determine if the response implies an action to execute
        intent = classify_command(message)
        actions = _extract_assistant_actions(intent, message, user_id)

        updated_history = conversation_history + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": response_text},
        ]

        save_voice_interaction(
            user_id=user_id,
            transcription=message,
            intent=intent,
            response=response_text,
            confidence=0.9,
        )

        return {
            "response": response_text,
            "intent": intent,
            "actions": actions,
            "history": updated_history[-20:],  # Keep last 20 turns
        }

    except Exception as exc:
        logger.error("Voice assistant error: %s", exc)
        return {
            "response": "I encountered an error. Please try again.",
            "intent": VoiceIntent.UNKNOWN,
            "actions": [],
            "history": conversation_history,
            "error": str(exc),
        }


def _build_business_context(user_id: int) -> dict:
    """Gather live business metrics for the assistant's system prompt."""
    try:
        from app.services.report_service import _collect_report_data
        data = _collect_report_data(user_id, {})
        return {
            "total_revenue": data.get("total_revenue", 0),
            "total_overdue": data.get("total_overdue", 0),
            "overdue_count": data.get("overdue_analysis", {}).get("count", 0),
            "invoice_count": data.get("invoice_count", 0),
            "top_clients": data.get("top_clients", [])[:3],
        }
    except Exception:
        return {}


def _extract_assistant_actions(intent: str, text: str, user_id: int) -> list[dict]:
    """Determine and return executable action metadata from the assistant response."""
    if intent == VoiceIntent.CREATE_INVOICE:
        return [{"type": "create_invoice", "params": extract_invoice_data(text)}]
    elif intent == VoiceIntent.GENERATE_REPORT:
        return [{"type": "generate_report", "params": {"report_type": _extract_report_type(text)}}]
    elif intent == VoiceIntent.REMIND_CLIENT:
        return [{"type": "send_reminder", "params": {"client": _extract_client_name(text)}}]
    return []


# ===========================================================================
# 9. AI AUTO CORRECTION
# ===========================================================================

def clean_transcription(text: str) -> str:
    """
    Fix common speech-to-text errors and normalise business terminology.

    Corrections applied
    -------------------
    - Phonetic misspellings of currency (rupeez → rupees)
    - Number words to digits (twenty thousand → 20000)
    - Common STT errors for business terms
    - Normalise Hinglish currency references

    Parameters
    ----------
    text : Raw transcription string.

    Returns
    -------
    Cleaned text string.
    """
    if not text:
        return text

    corrections = {
        # Currency
        r"\brupeez\b": "rupees",
        r"\brupees\b": "rupees",
        r"\brupee\b": "rupee",
        r"\brs\b\.?": "₹",
        r"\binvoices\b(?=\s+for\b)": "invoice",
        # Common STT errors
        r"\bacmy\b": "Acme",
        r"\bvender\b": "vendor",
        r"\breciept\b": "receipt",
        r"\bpayement\b": "payment",
        r"\bclients\b(?=\s+name)": "client",
        r"\bdue date\b": "due date",
        # Number words (English)
        r"\btwenty[\s-]?thousand\b": "20000",
        r"\bten[\s-]?thousand\b": "10000",
        r"\bfifty[\s-]?thousand\b": "50000",
        r"\bone[\s-]?lakh\b": "100000",
        r"\btwo[\s-]?lakh\b": "200000",
        r"\bfive[\s-]?lakh\b": "500000",
        r"\bfifteen[\s-]?thousand\b": "15000",
        r"\btwenty[\s-]?five[\s-]?thousand\b": "25000",
        # Hinglish
        r"\bhazaar\b": "thousand",
        r"\blakhs?\b": "lakh",
        r"\bpaisa\b": "rupees",
        r"\bkal\b": "tomorrow",
        r"\baaj\b": "today",
    }

    result = text
    for pattern, replacement in corrections.items():
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    return result.strip()


# ===========================================================================
# 10. COMMAND CONFIDENCE SCORING
# ===========================================================================

def calculate_command_confidence(text: str, intent: str) -> dict:
    """
    Score how confidently the voice command was understood.

    Factors
    -------
    - Text length (very short = ambiguous)
    - Intent match quality (keyword hits vs AI fallback)
    - Required field presence (for create_invoice: amount + client)
    - Language clarity score

    Returns
    -------
    {
        "confidence": 0.91,
        "needs_confirmation": False,
        "suggestion": "Did you mean create invoice for Acme?"
    }
    """
    confidence = 0.5  # Baseline
    text_lower = text.lower()
    words = text.split()

    # Length bonus
    if len(words) >= 5:
        confidence += 0.15
    if len(words) >= 10:
        confidence += 0.10

    # Intent is known
    if intent != VoiceIntent.UNKNOWN:
        confidence += 0.20

    # Invoice-specific field presence
    if intent == VoiceIntent.CREATE_INVOICE:
        if any(kw in text_lower for kw in ["₹", "rupee", "$", "dollar", "k ", "thousand", "lakh"]):
            confidence += 0.15
        if re.search(r"\bfor\b\s+\w+", text_lower):
            confidence += 0.10

    # Analytics query clarity
    if intent in (VoiceIntent.ANALYTICS_QUERY, VoiceIntent.DASHBOARD_QUERY):
        if any(kw in text_lower for kw in ["revenue", "overdue", "invoice", "client", "cashflow"]):
            confidence += 0.15

    confidence = min(round(confidence, 2), 1.0)
    needs_confirmation = confidence < 0.70

    suggestion = None
    if needs_confirmation and intent == VoiceIntent.CREATE_INVOICE:
        client = _extract_client_name(text)
        suggestion = f"Did you mean: create invoice{f' for {client}' if client else ''}?"

    return {
        "confidence": confidence,
        "needs_confirmation": needs_confirmation,
        "suggestion": suggestion,
    }


# ===========================================================================
# 11. MULTI-LANGUAGE SUPPORT
# ===========================================================================

def detect_language(text: str) -> str:
    """
    Detect the language of a transcription.

    Supported
    ---------
    en        : English
    hi        : Hindi (Devanagari script)
    hinglish  : Code-switched Hindi-English

    Returns
    -------
    Language code string: 'en', 'hi', or 'hinglish'.
    """
    if not text:
        return SupportedLanguage.ENGLISH

    # Devanagari Unicode range
    hindi_chars = re.findall(r"[\u0900-\u097F]", text)
    if hindi_chars:
        return SupportedLanguage.HINDI

    # Hinglish keywords (romanised Hindi common in Indian business speech)
    hinglish_words = {
        "karo", "karo", "banao", "bana", "bhejo", "bata", "kitna", "kya",
        "hazaar", "lakh", "paisa", "abhi", "aaj", "kal", "rupaye",
        "invoice bana", "mujhe", "chahiye", "theek", "nahi", "haan",
    }
    text_lower = text.lower()
    hinglish_hits = sum(1 for w in hinglish_words if w in text_lower)
    if hinglish_hits >= 1:
        return SupportedLanguage.HINGLISH

    return SupportedLanguage.ENGLISH


# ===========================================================================
# 12. VOICE WORKFLOW TRIGGERS
# ===========================================================================

def trigger_voice_workflow(
    command: dict,
    *,
    user_id: int,
) -> dict:
    """
    Trigger an existing workflow or create a new one from a voice command.

    Supported voice-triggered actions
    ----------------------------------
    send_reminder     → Trigger reminder workflow for a client
    generate_report   → Run executive report generation
    notify_team       → Broadcast a team notification
    schedule_invoice  → Schedule a recurring invoice

    Parameters
    ----------
    command  : Parsed voice command dict from parse_voice_command().
    user_id  : Acting user.

    Returns
    -------
    Workflow execution result dict.
    """
    intent = command.get("intent", VoiceIntent.UNKNOWN)

    if intent in (VoiceIntent.REMIND_CLIENT, VoiceIntent.SEND_REMINDER):
        from app.services.workflow_service import evaluate_trigger, TriggerType
        entity = {
            "id": command.get("invoice_id", _new_id()),
            "client": command.get("client"),
            "due_date": command.get("due_date"),
            "source": "voice",
        }
        results = evaluate_trigger(TriggerType.INVOICE_OVERDUE, entity, user_id=user_id)
        return {"triggered": True, "workflow_results": results, "intent": intent}

    elif intent == VoiceIntent.GENERATE_REPORT:
        from app.services.report_service import build_report_by_type
        report_type = command.get("report_type", "executive")
        report = build_report_by_type(report_type, user_id)
        return {"triggered": True, "report": report, "intent": intent}

    elif intent == VoiceIntent.CREATE_WORKFLOW:
        from app.services.workflow_service import generate_workflow_from_prompt
        workflow = generate_workflow_from_prompt(command.get("raw_text", ""), user_id=user_id)
        return {"triggered": True, "workflow": workflow, "intent": intent}

    return {
        "triggered": False,
        "reason": f"No workflow handler for intent: {intent}",
        "intent": intent,
    }


# ===========================================================================
# 13. REAL-TIME STREAMING VOICE RESPONSES
# ===========================================================================

def stream_voice_response(messages: list[dict]) -> Generator[str, None, None]:
    """
    Stream an AI response token-by-token for a ChatGPT-like sidebar experience.

    Usage in an SSE route
    ---------------------
    @app.route("/api/voice/stream")
    def voice_stream():
        def generate():
            for chunk in stream_voice_response(messages):
                yield f"data: {json.dumps({'type': 'stream_chunk', 'content': chunk})}\\n\\n"
            yield f"data: {json.dumps({'type': 'stream_end'})}\\n\\n"
        return Response(generate(), mimetype="text/event-stream")

    Yields
    ------
    Text chunks (strings) from the AI response stream.
    """
    ai = _get_openai()
    sio = _get_socketio()

    if not ai:
        yield "AI assistant requires OPENAI_API_KEY to be configured."
        return

    try:
        stream = ai.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=300,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                if sio:
                    sio.emit("voice_stream_chunk", {
                        "type": "stream_chunk",
                        "content": delta,
                        "timestamp": _now().isoformat(),
                    })
                yield delta

        if sio:
            sio.emit("voice_stream_end", {"type": "stream_end"})

    except Exception as exc:
        logger.error("Voice streaming error: %s", exc)
        yield f"Error: {exc}"


# ===========================================================================
# 14. VOICE COMMAND HISTORY
# ===========================================================================

def save_voice_interaction(
    *,
    user_id: int,
    transcription: str,
    intent: str,
    confidence: float,
    response: str = "",
    created_invoice_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """
    Persist a voice interaction record for analytics and assistant memory.

    Stores: transcription, intent, confidence, response, created entity,
    language, and timestamp.

    Returns
    -------
    Serialised interaction dict.
    """
    interaction_id = _new_id()
    record = {
        "id": interaction_id,
        "user_id": user_id,
        "transcription": transcription,
        "intent": intent,
        "confidence": confidence,
        "response": response,
        "created_invoice_id": created_invoice_id,
        "language": detect_language(transcription),
        "metadata": metadata or {},
        "created_at": _now().isoformat(),
    }

    VoiceLog = _voice_log_model()
    if VoiceLog:
        try:
            db = _get_db()
            log = VoiceLog(
                id=interaction_id,
                user_id=user_id,
                transcription=transcription,
                intent=intent,
                confidence=confidence,
                response=response,
                created_invoice_id=created_invoice_id,
                language=record["language"],
                metadata=json.dumps(metadata or {}),
                created_at=_now(),
            )
            db.session.add(log)
            db.session.commit()
        except Exception as exc:
            logger.warning("Voice interaction save failed: %s", exc)

    return record


def get_voice_history(user_id: int, *, limit: int = 20) -> list[dict]:
    """Return recent voice interactions for a user (assistant memory context)."""
    VoiceLog = _voice_log_model()
    if not VoiceLog:
        return []
    try:
        logs = (
            VoiceLog.query
            .filter_by(user_id=user_id)
            .order_by(VoiceLog.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": l.id,
                "transcription": l.transcription,
                "intent": l.intent,
                "confidence": float(l.confidence),
                "response": l.response,
                "language": getattr(l, "language", "en"),
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l in logs
        ]
    except Exception as exc:
        logger.warning("Voice history retrieval failed: %s", exc)
        return []


# ===========================================================================
# 15. AI VOICE RECOMMENDATIONS
# ===========================================================================

def generate_voice_suggestions(
    *,
    user_id: int | None = None,
    context: dict | None = None,
) -> list[str]:
    """
    Generate proactive voice command suggestions based on business context.

    These surface as "You could say..." prompts in the voice sidebar.

    Examples
    --------
    "Send reminder to overdue clients?"
    "Generate monthly revenue report?"
    "Create recurring invoice for Acme?"

    Returns
    -------
    List of suggestion strings (3–5 items).
    """
    context = context or {}
    last_action = context.get("last_action", "")

    # Context-aware post-action suggestions
    post_action_map = {
        "create_invoice": [
            "Send invoice to client now?",
            "Set up automatic reminder if unpaid?",
            "Make this a recurring invoice?",
        ],
        "send_invoice": [
            "Schedule a follow-up reminder in 3 days?",
            "Generate a payment link for the client?",
        ],
        "analytics_query": [
            "Generate a full executive report?",
            "Export this data as a CSV?",
            "Email the report to your team?",
        ],
        "generate_report": [
            "Email this report to your team?",
            "Schedule weekly auto-reports?",
        ],
    }

    if last_action in post_action_map:
        return post_action_map[last_action]

    # General proactive suggestions
    suggestions = [
        "Send reminder to overdue clients?",
        "Generate this month's revenue report?",
        "Create a recurring invoice for your top client?",
        "Show me the business health score.",
        "What's my total revenue this month?",
    ]

    # AI personalisation
    ai = _get_openai()
    if ai and user_id:
        try:
            business_context = _build_business_context(user_id)
            prompt = (
                f"Based on this business context: {json.dumps(business_context)}, "
                "suggest 3 natural-language voice commands the user should try next. "
                "Make them short, conversational, and specific to their data. "
                'Return JSON: {"suggestions": ["...", "...", "..."]}'
            )
            resp = ai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.choices[0].message.content)
            ai_suggestions = data.get("suggestions", [])
            suggestions = ai_suggestions[:3] + suggestions[:2]
        except Exception as exc:
            logger.warning("AI voice suggestions failed: %s", exc)

    return suggestions[:5]


# ===========================================================================
# HELPER UTILITIES
# ===========================================================================

def _extract_client_name(text: str) -> str | None:
    """Extract a client/company name from voice text."""
    patterns = [
        r"\bfor\s+([A-Z][a-zA-Z\s&]{1,30}?)(?:\s+(?:for|due|at|of|\d|₹|\$|,|\.)|$)",
        r"\bto\s+([A-Z][a-zA-Z\s&]{1,30}?)(?:\s+(?:about|for|due|\d)|$)",
        r"\bclient\s+([A-Za-z][a-zA-Z\s&]{1,25})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return m.group(1).strip().title()
    return None


def _extract_delay(text: str) -> str | None:
    """Extract a delay or timing phrase from voice text."""
    text_lower = text.lower()
    delay_phrases = [
        "tomorrow", "in 3 days", "in 7 days", "next week", "in a week",
        "today", "now", "immediately",
    ]
    for phrase in delay_phrases:
        if phrase in text_lower:
            return phrase
    m = re.search(r"in (\d+) days?", text_lower)
    if m:
        return f"in {m.group(1)} days"
    return None


def _extract_report_type(text: str) -> str:
    """Infer report type from voice command text."""
    text_lower = text.lower()
    if "executive" in text_lower or "summary" in text_lower:
        return "executive"
    if "cashflow" in text_lower or "cash flow" in text_lower:
        return "cashflow"
    if "revenue" in text_lower:
        return "revenue"
    if "invoice" in text_lower:
        return "invoice"
    if "client" in text_lower:
        return "client"
    return "executive"
