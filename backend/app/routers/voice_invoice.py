"""
app/routers/voice_invoice.py

Voice AI Invoice Router for InvoiceFlow AI Platform.
Covers speech-to-text transcription, AI entity extraction, voice command invoice
creation, conversational business commands, AI memory, multilingual support,
and real-time WebSocket events.

"Talk to your finance system" — the biggest demo-winning feature.
"""

from __future__ import annotations

import io
import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import ActivityType, NotificationType
from app.database import get_db
from app.models import (
    Activity,
    AIConversation,
    BusinessInsight,
    Client,
    Invoice,
    InvoiceItem,
    InvoiceStatus,
    Notification,
    User,
    Workflow,
)
from app.services.ai_service import AIService
from app.services.analytics_service import AnalyticsService
from app.services.invoice_service import InvoiceService
from app.services.notification_service import NotificationService
from app.services.voice_service import VoiceService
from app.services.workflow_service import WorkflowService
from app.websocket.manager import ws_manager
from auth import get_current_user

router = APIRouter(prefix="/voice", tags=["Voice AI"])

ai_service = AIService()
voice_service = VoiceService()
invoice_service = InvoiceService()
workflow_service = WorkflowService()
notification_service = NotificationService()
analytics_service = AnalyticsService()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_AUDIO_FORMATS = {"mp3", "wav", "m4a", "webm", "ogg", "flac"}
MAX_AUDIO_MB = 25
MAX_AUDIO_BYTES = MAX_AUDIO_MB * 1024 * 1024

SUPPORTED_LANGUAGES = {
    "en": "English",
    "hi": "Hindi",
    "hi-en": "Hinglish (Hindi + English)",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "ar": "Arabic",
    "pt": "Portuguese",
    "zh": "Chinese (Simplified)",
    "ja": "Japanese",
}

# Voice command intent categories
INTENT_CATEGORIES = {
    "create_invoice": ["create invoice", "make invoice", "new invoice", "generate invoice",
                       "invoice banao", "invoice bana"],
    "send_reminder": ["send reminder", "remind client", "follow up", "overdue reminder"],
    "analytics_query": ["revenue", "how much", "show stats", "cash flow", "monthly",
                        "performance", "kpi", "earnings"],
    "client_lookup": ["which clients", "risky clients", "top clients", "show clients"],
    "workflow_trigger": ["automatically", "auto", "schedule", "recurring", "every week"],
    "report_generation": ["generate report", "export", "download report", "pdf report"],
    "duplicate_invoice": ["repeat", "same as", "duplicate", "copy last", "repeat last"],
    "send_invoice": ["send invoice", "email invoice", "deliver invoice"],
    "mark_paid": ["mark as paid", "record payment", "client paid"],
    "dashboard_query": ["dashboard", "overview", "summary", "status"],
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _validate_audio_upload(file: UploadFile) -> str:
    """Validate content type and return file extension."""
    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in SUPPORTED_AUDIO_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported audio format '{ext}'. Supported: {', '.join(SUPPORTED_AUDIO_FORMATS)}",
        )
    return ext


def _classify_intent(text: str) -> tuple[str, float]:
    """Fast local intent classification before sending to AI."""
    text_lower = text.lower()
    best_intent, best_score = "unknown", 0.0
    for intent, keywords in INTENT_CATEGORIES.items():
        matches = sum(1 for kw in keywords if kw in text_lower)
        if matches > best_score:
            best_score = float(matches)
            best_intent = intent
    confidence = min(best_score / 3, 1.0) if best_score > 0 else 0.0
    return best_intent, confidence


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
        entity_type="voice",
        entity_id=entity_id,
        description=description,
        metadata=metadata or {},
        created_at=_utcnow(),
    ))


def _nudge_due_date(base_days: int) -> date:
    """Return a due date N days from today, skipping weekends."""
    d = date.today()
    added = 0
    while added < base_days:
        d += timedelta(days=1)
        if d.weekday() < 5:  # Mon–Fri
            added += 1
    return d


# ---------------------------------------------------------------------------
# POST /transcribe  — Speech-to-text + AI entity extraction
# ---------------------------------------------------------------------------


@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    language: str = Form("en"),
    session_id: Optional[str] = Form(None),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Upload an audio file and receive:
    - Clean AI-corrected transcript
    - Detected intent + confidence score
    - Parsed invoice / business entities
    - AI suggestions and recommended actions
    """
    ext = _validate_audio_upload(file)

    audio_bytes = await file.read()
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Audio file too large. Maximum size is {MAX_AUDIO_MB} MB.",
        )

    session_id = session_id or str(uuid4())

    await ws_manager.broadcast_to_team(
        str(current_user.team_id),
        {"event": "VOICE_TRANSCRIPTION_STARTED", "session_id": session_id},
    )

    # --- Step 1: Speech-to-text ---
    raw_transcript = await voice_service.transcribe(
        audio_bytes=audio_bytes,
        file_format=ext,
        language=language,
    )

    # --- Step 2: AI transcript cleanup (noise, punctuation, business names) ---
    cleaned = await ai_service.clean_transcript(
        raw_transcript=raw_transcript.get("text", ""),
        language=language,
        business_context={
            "business_name": current_user.business_name,
            "team_id": str(current_user.team_id),
        },
    )
    transcript_text: str = cleaned.get("text", raw_transcript.get("text", ""))

    # --- Step 3: Intent classification (local fast pass first) ---
    local_intent, local_confidence = _classify_intent(transcript_text)

    # --- Step 4: AI deep entity extraction ---
    entities = await ai_service.extract_voice_entities(
        transcript=transcript_text,
        language=language,
        intent_hint=local_intent,
    )

    # --- Step 5: AI suggestions while the user decides what to do next ---
    ai_suggestions = await ai_service.get_voice_suggestions(
        transcript=transcript_text,
        entities=entities,
        intent=entities.get("intent", local_intent),
    )

    # Persist transcript in conversation memory
    convo = AIConversation(
        user_id=current_user.id,
        session_id=session_id,
        role="user",
        content=transcript_text,
        context={
            "source": "voice",
            "language": language,
            "entities": entities,
            "raw_confidence": raw_transcript.get("confidence", 0),
        },
        created_at=_utcnow(),
    )
    db.add(convo)
    await db.commit()

    await ws_manager.broadcast_to_team(
        str(current_user.team_id),
        {
            "event": "VOICE_TRANSCRIPTION_COMPLETED",
            "session_id": session_id,
            "transcript": transcript_text[:200],
            "intent": entities.get("intent", local_intent),
        },
    )

    background_tasks.add_task(
        _log_activity,
        db,
        team_id=current_user.team_id,
        user_id=current_user.id,
        action_type=ActivityType.voice_transcription,
        entity_id=convo.id,
        description=f"Voice transcription: '{transcript_text[:80]}...'",
        metadata={"language": language, "intent": entities.get("intent")},
    )

    return {
        "session_id": session_id,
        "transcript": transcript_text,
        "raw_transcript": raw_transcript.get("text", ""),
        "language_detected": cleaned.get("detected_language", language),
        "confidence": raw_transcript.get("confidence", entities.get("confidence", 0.0)),
        "duration_seconds": raw_transcript.get("duration", None),
        "timestamps": raw_transcript.get("timestamps", []),
        "intent": entities.get("intent", local_intent),
        "intent_confidence": entities.get("intent_confidence", local_confidence),
        "parsed_entities": entities.get("entities", {}),
        "ai_corrections": cleaned.get("corrections", []),
        "ai_suggestions": ai_suggestions.get("suggestions", []),
        "recommended_actions": ai_suggestions.get("actions", []),
        "multilingual_notes": cleaned.get("multilingual_notes", ""),
    }


# ---------------------------------------------------------------------------
# POST /create  — Voice command invoice creation
# ---------------------------------------------------------------------------


@router.post("/create")
async def voice_create_invoice(
    transcript: str = Form(..., max_length=2000),
    session_id: Optional[str] = Form(None),
    language: str = Form("en"),
    auto_send: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Create a fully-formed invoice from a voice transcript.

    Example input:
      "Create an invoice for Acme Corp for 5 design hours at 200 dollars each due next Friday"

    The AI auto-fills: invoice number, dates, tax, due date, description,
    priority, risk score, currency, and payment terms.
    """
    session_id = session_id or str(uuid4())

    # --- AI entity extraction ---
    entities = await ai_service.extract_voice_entities(
        transcript=transcript,
        language=language,
        intent_hint="create_invoice",
    )
    parsed: dict = entities.get("entities", {})

    # --- Resolve client from name ---
    client: Optional[Client] = None
    client_name: str = parsed.get("client_name", "") or parsed.get("client", "")
    if client_name:
        client_stmt = select(Client).where(
            Client.team_id == current_user.team_id,
            Client.name.ilike(f"%{client_name}%"),
            Client.is_active.is_(True),
        ).limit(1)
        client_result = await db.execute(client_stmt)
        client = client_result.scalar_one_or_none()

    # --- AI: smart due date recommendation ---
    avg_pay_days = float(client.average_days_to_pay or 14) if client else 14.0
    ai_due_date_rec = await ai_service.recommend_due_date(
        client_name=client.name if client else client_name,
        avg_payment_days=avg_pay_days,
        invoice_amount=parsed.get("total") or sum(
            (i.get("quantity", 1) * i.get("rate", 0)) for i in parsed.get("items", [])
        ),
    )

    # Due date: parsed from voice → AI recommendation → default 14 days
    raw_due = parsed.get("due_date")
    if raw_due and isinstance(raw_due, str):
        try:
            due_date = date.fromisoformat(raw_due)
        except ValueError:
            due_date = _nudge_due_date(ai_due_date_rec.get("recommended_days", 14))
    elif raw_due and isinstance(raw_due, date):
        due_date = raw_due
    else:
        due_date = _nudge_due_date(ai_due_date_rec.get("recommended_days", 14))

    # --- AI: invoice number ---
    last_invoice_stmt = (
        select(Invoice.number)
        .where(Invoice.team_id == current_user.team_id)
        .order_by(desc(Invoice.id))
        .limit(1)
    )
    last_number = (await db.execute(last_invoice_stmt)).scalar_one_or_none()
    invoice_number = _next_invoice_number(last_number)

    # --- AI: enhanced description ---
    raw_description = parsed.get("description", transcript[:200])
    ai_description = await ai_service.enhance_invoice_description(
        raw_description=raw_description,
        items=parsed.get("items", []),
        client_name=client.name if client else client_name,
        month=date.today().strftime("%B %Y"),
    )

    # --- AI: priority and risk ---
    ai_priority_data = await ai_service.set_invoice_priority(
        client_risk_score=float(client.risk_score or 0) if client else 0.0,
        amount=float(parsed.get("total") or 0),
        due_date=due_date.isoformat(),
    )

    # --- Build invoice items ---
    raw_items: list[dict] = parsed.get("items", [])
    if not raw_items:
        # Fallback single item from parsed totals
        raw_items = [{
            "description": ai_description.get("enhanced", raw_description),
            "quantity": parsed.get("quantity", 1),
            "rate": parsed.get("rate") or parsed.get("amount") or parsed.get("total") or 0,
        }]

    # Calculate totals
    subtotal = sum(float(i.get("quantity", 1)) * float(i.get("rate", 0)) for i in raw_items)
    tax_rate = float(parsed.get("tax_rate", 0))
    tax_amount = round(subtotal * tax_rate / 100, 2)
    discount = float(parsed.get("discount", 0))
    total = round(subtotal + tax_amount - discount, 2)
    currency = (parsed.get("currency") or "USD").upper()

    # --- Create Invoice ---
    invoice = Invoice(
        number=invoice_number,
        client_id=client.id if client else None,
        user_id=current_user.id,
        team_id=current_user.team_id,
        status=InvoiceStatus.draft,
        currency=currency,
        subtotal=subtotal,
        tax_rate=tax_rate,
        tax_amount=tax_amount,
        discount=discount,
        total=total,
        amount_paid=0,
        balance_due=total,
        issue_date=date.today(),
        due_date=due_date,
        description=ai_description.get("enhanced", raw_description),
        ai_description=ai_description.get("enhanced"),
        ai_priority=ai_priority_data.get("priority", "normal"),
        notes=parsed.get("notes", ""),
        terms=parsed.get("payment_terms", "Net 14"),
        is_recurring=parsed.get("is_recurring", False),
        recurring_config=parsed.get("recurring_config"),
        source="voice",
        metadata={
            "session_id": session_id,
            "voice_transcript": transcript[:500],
            "language": language,
            "ai_confidence": entities.get("intent_confidence", 0),
        },
    )
    db.add(invoice)
    await db.flush()

    # Add invoice items
    for item_data in raw_items:
        qty = float(item_data.get("quantity", 1))
        rate = float(item_data.get("rate", 0))
        db.add(InvoiceItem(
            invoice_id=invoice.id,
            description=item_data.get("description", "Service"),
            quantity=qty,
            rate=rate,
            amount=round(qty * rate, 2),
        ))

    await _log_activity(
        db,
        team_id=current_user.team_id,
        user_id=current_user.id,
        action_type=ActivityType.created,
        entity_id=invoice.id,
        description=f"Voice invoice {invoice_number} created for {client.name if client else client_name}",
        metadata={"source": "voice", "session_id": session_id, "language": language},
    )
    await db.commit()
    await db.refresh(invoice)

    # Auto-send if requested
    sent = False
    if auto_send and client and client.email:
        await invoice_service.mark_sent(invoice_id=invoice.id, db=db)
        sent = True

    # Notifications + WebSocket
    notif = Notification(
        user_id=current_user.id,
        type=NotificationType.invoice_created,
        title=f"Voice invoice created: {invoice_number}",
        message=f"Invoice {invoice_number} for {client.name if client else client_name} "
                f"({currency} {total:,.2f}) created via voice.",
        read=False,
        created_at=_utcnow(),
    )
    db.add(notif)
    await db.commit()

    await ws_manager.broadcast_to_team(
        str(current_user.team_id),
        {
            "event": "VOICE_INVOICE_CREATED",
            "invoice_id": str(invoice.id),
            "invoice_number": invoice_number,
            "client": client.name if client else client_name,
            "total": total,
            "currency": currency,
            "auto_sent": sent,
        },
    )

    return {
        "session_id": session_id,
        "invoice": {
            "id": str(invoice.id),
            "number": invoice_number,
            "client_name": client.name if client else client_name,
            "client_id": str(client.id) if client else None,
            "status": "sent" if sent else "draft",
            "currency": currency,
            "subtotal": subtotal,
            "tax_rate": tax_rate,
            "tax_amount": tax_amount,
            "discount": discount,
            "total": total,
            "issue_date": date.today().isoformat(),
            "due_date": due_date.isoformat(),
            "description": ai_description.get("enhanced", raw_description),
            "items": [
                {
                    "description": i.get("description"),
                    "quantity": i.get("quantity"),
                    "rate": i.get("rate"),
                    "amount": round(float(i.get("quantity", 1)) * float(i.get("rate", 0)), 2),
                }
                for i in raw_items
            ],
            "is_recurring": parsed.get("is_recurring", False),
            "payment_terms": parsed.get("payment_terms", "Net 14"),
            "source": "voice",
        },
        "ai": {
            "description_enhanced": ai_description.get("enhanced", ""),
            "description_reasoning": ai_description.get("reasoning", ""),
            "due_date_recommendation": ai_due_date_rec,
            "priority": ai_priority_data.get("priority", "normal"),
            "risk_score": ai_priority_data.get("risk_score", 0),
            "follow_up_schedule": ai_priority_data.get("follow_up_schedule", []),
        },
        "transcript": transcript,
        "parsed_entities": parsed,
        "intent_confidence": entities.get("intent_confidence", 0),
        "auto_sent": sent,
        "suggested_next_actions": [
            "Send invoice to client",
            "Set up recurring schedule",
            "Add payment reminder",
        ] if not sent else ["Track payment", "Add reminder", "Duplicate for next month"],
    }


def _next_invoice_number(last: Optional[str]) -> str:
    """Auto-increment invoice number: INV-0001 → INV-0002."""
    if not last:
        return "INV-0001"
    parts = last.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return f"{parts[0]}-{int(parts[1]) + 1:04d}"
    return f"INV-{uuid4().hex[:4].upper()}"


# ---------------------------------------------------------------------------
# POST /command  — Conversational business command center
# ---------------------------------------------------------------------------


@router.post("/command")
async def voice_command(
    command: str = Form(..., max_length=1000),
    session_id: Optional[str] = Form(None),
    language: str = Form("en"),
    confirmed: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Execute a spoken/typed business command in natural language.

    Examples:
      "Show overdue invoices"
      "Which clients are risky?"
      "Send reminders to all overdue clients"
      "How much revenue this month?"
      "Automatically remind overdue clients weekly"
      "Repeat last invoice for Acme Corp"
    """
    session_id = session_id or str(uuid4())

    # Load last 5 voice commands from memory for context
    memory_stmt = (
        select(AIConversation)
        .where(
            AIConversation.user_id == current_user.id,
            AIConversation.session_id == session_id,
        )
        .order_by(AIConversation.created_at)
        .limit(10)
    )
    memory_rows = (await db.execute(memory_stmt)).scalars().all()
    conversation_history = [{"role": m.role, "content": m.content} for m in memory_rows]

    # Local fast intent pass
    local_intent, local_confidence = _classify_intent(command)

    # Business context
    today = date.today()
    month_start = today.replace(day=1)
    rev_stmt = select(
        func.coalesce(func.sum(Invoice.total), 0).label("total"),
        func.coalesce(func.sum(Invoice.amount_paid), 0).label("paid"),
    ).where(Invoice.team_id == current_user.team_id, Invoice.issue_date >= month_start)
    rev = (await db.execute(rev_stmt)).mappings().one()

    overdue_count_stmt = select(func.count(Invoice.id)).where(
        Invoice.team_id == current_user.team_id, Invoice.status == InvoiceStatus.overdue
    )
    overdue_count = int((await db.execute(overdue_count_stmt)).scalar_one() or 0)
    high_risk_stmt = select(func.count(Client.id)).where(
        Client.team_id == current_user.team_id, Client.risk_score >= 70
    )
    high_risk = int((await db.execute(high_risk_stmt)).scalar_one() or 0)

    business_snapshot = {
        "month_revenue": float(rev["total"]),
        "month_collected": float(rev["paid"]),
        "overdue_count": overdue_count,
        "high_risk_clients": high_risk,
        "today": today.isoformat(),
        "business_name": current_user.business_name,
    }

    # --- AI deep command classification ---
    classification = await ai_service.classify_voice_command(
        command=command,
        language=language,
        conversation_history=conversation_history,
        business_snapshot=business_snapshot,
    )

    intent = classification.get("intent", local_intent)
    confidence = classification.get("confidence", local_confidence)
    entities = classification.get("entities", {})
    is_multi_step = classification.get("is_multi_step", False)
    requires_confirmation = classification.get("requires_confirmation", False)

    # Confirmation gate for destructive / high-impact commands
    if requires_confirmation and not confirmed:
        return {
            "session_id": session_id,
            "status": "confirmation_required",
            "intent": intent,
            "message": classification.get("confirmation_message", "Please confirm this action."),
            "impact_preview": classification.get("impact_preview", ""),
            "confidence": confidence,
            "entities": entities,
        }

    # --- Execute command ---
    result = await _execute_voice_command(
        intent=intent,
        entities=entities,
        command=command,
        user=current_user,
        db=db,
        is_multi_step=is_multi_step,
        classification=classification,
    )

    # Persist to memory
    user_msg = AIConversation(
        user_id=current_user.id,
        session_id=session_id,
        role="user",
        content=command,
        context={"intent": intent, "entities": entities, "source": "voice_command"},
        created_at=_utcnow(),
    )
    assistant_msg = AIConversation(
        user_id=current_user.id,
        session_id=session_id,
        role="assistant",
        content=result.get("ai_response", ""),
        context={"intent": intent, "result_summary": result.get("summary", "")},
        created_at=_utcnow(),
    )
    db.add(user_msg)
    db.add(assistant_msg)

    await _log_activity(
        db,
        team_id=current_user.team_id,
        user_id=current_user.id,
        action_type=ActivityType.voice_command,
        entity_id=user_msg.id,
        description=f"Voice command: {intent}",
        metadata={"command": command[:200], "language": language, "intent": intent},
    )
    await db.commit()

    await ws_manager.broadcast_to_team(
        str(current_user.team_id),
        {
            "event": "VOICE_COMMAND_PROCESSED",
            "intent": intent,
            "session_id": session_id,
            "result_summary": result.get("summary", ""),
        },
    )

    return {
        "session_id": session_id,
        "status": "executed",
        "command": command,
        "intent": intent,
        "confidence": confidence,
        "entities": entities,
        "is_multi_step": is_multi_step,
        "ai_response": result.get("ai_response", ""),
        "summary": result.get("summary", ""),
        "data": result.get("data", {}),
        "actions_taken": result.get("actions_taken", []),
        "follow_up_suggestions": result.get("follow_up_suggestions", []),
        "recommended_actions": result.get("recommended_actions", []),
        "ai_suggestions": classification.get("suggestions", []),
    }


async def _execute_voice_command(
    intent: str,
    entities: dict,
    command: str,
    user: User,
    db: AsyncSession,
    is_multi_step: bool,
    classification: dict,
) -> dict:
    """Route intent to the appropriate handler and return unified result dict."""

    # --- Analytics queries ---
    if intent == "analytics_query":
        answer = await ai_service.answer_analytics_question(
            question=command,
            team_id=str(user.team_id),
        )
        return {
            "ai_response": answer.get("answer", ""),
            "summary": answer.get("summary", ""),
            "data": answer.get("data", {}),
            "actions_taken": ["Queried analytics data"],
            "follow_up_suggestions": answer.get("follow_up", []),
            "recommended_actions": [],
        }

    # --- Client lookup ---
    if intent == "client_lookup":
        risk_threshold = entities.get("risk_threshold", 60)
        clients_stmt = (
            select(Client.id, Client.name, Client.risk_score, Client.total_invoiced, Client.total_paid)
            .where(Client.team_id == user.team_id, Client.risk_score >= risk_threshold, Client.is_active.is_(True))
            .order_by(desc(Client.risk_score))
            .limit(10)
        )
        rows = (await db.execute(clients_stmt)).all()
        data = [
            {"id": str(r[0]), "name": r[1], "risk_score": r[2],
             "total_invoiced": float(r[3] or 0), "total_paid": float(r[4] or 0)}
            for r in rows
        ]
        return {
            "ai_response": f"Found {len(data)} high-risk client{'s' if len(data) != 1 else ''}.",
            "summary": f"{len(data)} risky clients returned",
            "data": {"clients": data},
            "actions_taken": ["Queried high-risk clients"],
            "follow_up_suggestions": ["Send reminders to risky clients", "Review payment terms"],
            "recommended_actions": ["trigger_reminder_workflow"],
        }

    # --- Send reminder ---
    if intent == "send_reminder":
        overdue_stmt = (
            select(Invoice.id, Invoice.number, Invoice.client_id, Invoice.balance_due)
            .where(Invoice.team_id == user.team_id, Invoice.status == InvoiceStatus.overdue)
            .limit(20)
        )
        overdue_rows = (await db.execute(overdue_stmt)).all()
        reminder_result = await ai_service.bulk_generate_reminders(
            invoices=[
                {"id": str(r[0]), "number": r[1], "client_id": str(r[2]), "balance": float(r[3] or 0)}
                for r in overdue_rows
            ],
            team_id=str(user.team_id),
        )
        return {
            "ai_response": f"Reminders queued for {len(overdue_rows)} overdue invoice{'s' if len(overdue_rows) != 1 else ''}.",
            "summary": f"{len(overdue_rows)} reminders queued",
            "data": {"reminders_queued": len(overdue_rows), "result": reminder_result},
            "actions_taken": [f"Queued reminder for invoice {r[1]}" for r in overdue_rows[:5]],
            "follow_up_suggestions": ["Track reminder open rates", "Escalate if no response in 5 days"],
            "recommended_actions": [],
        }

    # --- Workflow trigger ---
    if intent == "workflow_trigger":
        built = await ai_service.build_workflow_from_text(
            prompt=command,
            supported_triggers=[
                "invoice_overdue", "invoice_paid", "client_created",
                "high_risk_client", "payment_received", "weekly_summary",
            ],
            supported_conditions=["days_overdue", "client_risk_score", "invoice_amount"],
            supported_actions=["generate_ai_reminder", "send_email", "alert_finance_team", "escalate_invoice"],
            team_context={"business_name": user.business_name, "role": str(user.role)},
        )
        wf = Workflow(
            name=built.get("name", f"Voice Workflow — {date.today().isoformat()}"),
            description=built.get("description", command[:200]),
            trigger_type=built.get("trigger_type", "invoice_overdue"),
            conditions=built.get("conditions", {}),
            actions=built.get("actions", []),
            team_id=user.team_id,
            is_active=True,
            created_by=user.id,
        )
        if hasattr(wf, "is_ai_generated"):
            wf.is_ai_generated = True
        db.add(wf)
        await db.flush()
        await db.commit()
        await ws_manager.broadcast_to_team(
            str(user.team_id),
            {"event": "AI_COMMAND_EXECUTED", "intent": "workflow_trigger", "workflow_id": str(wf.id)},
        )
        return {
            "ai_response": f"Workflow '{wf.name}' created and activated.",
            "summary": f"Workflow auto-generated from voice command",
            "data": {"workflow_id": str(wf.id), "workflow_name": wf.name},
            "actions_taken": ["AI built workflow", "Workflow activated"],
            "follow_up_suggestions": ["Review workflow settings", "Test workflow with dry run"],
            "recommended_actions": [],
        }

    # --- Dashboard query ---
    if intent == "dashboard_query":
        rev_stmt2 = select(
            func.coalesce(func.sum(Invoice.total), 0).label("total"),
            func.coalesce(func.sum(Invoice.amount_paid), 0).label("paid"),
            func.count(Invoice.id).label("count"),
        ).where(
            Invoice.team_id == user.team_id,
            Invoice.issue_date >= date.today().replace(day=1),
        )
        rev2 = (await db.execute(rev_stmt2)).mappings().one()
        return {
            "ai_response": f"This month: ${float(rev2['total']):,.2f} invoiced, "
                           f"${float(rev2['paid']):,.2f} collected across {int(rev2['count'])} invoices.",
            "summary": "Dashboard snapshot returned",
            "data": {
                "month_revenue": float(rev2["total"]),
                "month_collected": float(rev2["paid"]),
                "invoice_count": int(rev2["count"]),
            },
            "actions_taken": ["Queried dashboard KPIs"],
            "follow_up_suggestions": ["View full analytics", "Generate weekly report"],
            "recommended_actions": [],
        }

    # --- Fallback: let AI handle unrecognized intent ---
    fallback = await ai_service.handle_unknown_voice_command(command=command, intent=intent)
    return {
        "ai_response": fallback.get("response", "I couldn't fully understand that command. Try rephrasing."),
        "summary": "Command processed by AI fallback",
        "data": {},
        "actions_taken": [],
        "follow_up_suggestions": fallback.get("suggestions", []),
        "recommended_actions": [],
    }


# ---------------------------------------------------------------------------
# GET /commands/help  — Categorised command guide
# ---------------------------------------------------------------------------


@router.get("/commands/help")
async def commands_help(
    mode: str = Query("beginner", regex="^(beginner|advanced)$"),
    language: str = Query("en"),
) -> dict:
    base_commands = {
        "invoice_commands": {
            "label": "Invoice Commands",
            "icon": "file-invoice",
            "examples": [
                {
                    "command": "Create an invoice for Acme Corp for 5 design hours at $200 each",
                    "intent": "create_invoice",
                    "description": "Instantly creates a formatted invoice via voice",
                    "difficulty": "beginner",
                },
                {
                    "command": "Duplicate last invoice for Acme Corp",
                    "intent": "duplicate_invoice",
                    "description": "Copies your most recent invoice",
                    "difficulty": "beginner",
                },
                {
                    "command": "Create a recurring monthly invoice for TechCorp for $1500",
                    "intent": "create_invoice",
                    "description": "Sets up automatic recurring billing",
                    "difficulty": "advanced",
                },
                {
                    "command": "Send the invoice to Acme Corp",
                    "intent": "send_invoice",
                    "description": "Marks an invoice as sent and emails it",
                    "difficulty": "beginner",
                },
                {
                    "command": "Mark the Acme Corp invoice as paid",
                    "intent": "mark_paid",
                    "description": "Records a payment against an invoice",
                    "difficulty": "beginner",
                },
            ],
        },
        "reminder_commands": {
            "label": "Reminder Commands",
            "icon": "bell",
            "examples": [
                {
                    "command": "Send overdue reminders to all clients",
                    "intent": "send_reminder",
                    "description": "Triggers reminders for all overdue invoices",
                    "difficulty": "beginner",
                },
                {
                    "command": "Send a firm reminder to Acme Corp",
                    "intent": "send_reminder",
                    "description": "Sends a specific-tone reminder to one client",
                    "difficulty": "beginner",
                },
                {
                    "command": "Automatically remind overdue clients every week",
                    "intent": "workflow_trigger",
                    "description": "Creates a recurring reminder workflow",
                    "difficulty": "advanced",
                },
            ],
        },
        "analytics_commands": {
            "label": "Analytics Commands",
            "icon": "chart-bar",
            "examples": [
                {
                    "command": "How much revenue did we make this month?",
                    "intent": "analytics_query",
                    "description": "Returns current month revenue summary",
                    "difficulty": "beginner",
                },
                {
                    "command": "Predict next month's cash flow",
                    "intent": "analytics_query",
                    "description": "AI forecasts upcoming revenue",
                    "difficulty": "advanced",
                },
                {
                    "command": "Why is revenue lower this month?",
                    "intent": "analytics_query",
                    "description": "AI analyses revenue drop with recommendations",
                    "difficulty": "advanced",
                },
                {
                    "command": "Show me the top paying clients",
                    "intent": "client_lookup",
                    "description": "Returns clients ranked by payment amount",
                    "difficulty": "beginner",
                },
            ],
        },
        "ai_commands": {
            "label": "AI Commands",
            "icon": "sparkles",
            "examples": [
                {
                    "command": "Which clients are risky?",
                    "intent": "client_lookup",
                    "description": "Lists clients with high risk scores",
                    "difficulty": "beginner",
                },
                {
                    "command": "Generate business insights",
                    "intent": "analytics_query",
                    "description": "AI analyses your business and generates insight cards",
                    "difficulty": "beginner",
                },
                {
                    "command": "Handle all overdue invoices automatically",
                    "intent": "workflow_trigger",
                    "description": "Creates a full autonomous collection workflow",
                    "difficulty": "advanced",
                },
            ],
        },
        "workflow_commands": {
            "label": "Workflow Commands",
            "icon": "git-branch",
            "examples": [
                {
                    "command": "Automatically escalate invoices overdue by 30 days",
                    "intent": "workflow_trigger",
                    "description": "Creates an escalation automation workflow",
                    "difficulty": "advanced",
                },
                {
                    "command": "Send thank-you emails when clients pay",
                    "intent": "workflow_trigger",
                    "description": "Auto-generates thank-you on payment received",
                    "difficulty": "advanced",
                },
            ],
        },
    }

    # Filter by difficulty in beginner mode
    if mode == "beginner":
        for cat in base_commands.values():
            cat["examples"] = [e for e in cat["examples"] if e["difficulty"] == "beginner"]

    # Multilingual examples
    multilingual_examples: dict[str, list[dict]] = {}
    if language == "hi" or language == "hi-en":
        multilingual_examples["hindi_hinglish"] = [
            {"command": "Acme Corp ke liye invoice banao", "translation": "Create invoice for Acme Corp", "intent": "create_invoice"},
            {"command": "Overdue clients ko reminder bhejo", "translation": "Send reminders to overdue clients", "intent": "send_reminder"},
            {"command": "Is mahine ka revenue kitna hai?", "translation": "What's this month's revenue?", "intent": "analytics_query"},
            {"command": "Risky clients kaun hain?", "translation": "Which clients are risky?", "intent": "client_lookup"},
            {"command": "5000 ka invoice banao Rahul ke liye", "translation": "Create a ₹5000 invoice for Rahul", "intent": "create_invoice"},
        ]

    return {
        "mode": mode,
        "language": language,
        "supported_languages": SUPPORTED_LANGUAGES,
        "command_categories": base_commands,
        "multilingual_examples": multilingual_examples,
        "tips": [
            "Speak naturally — AI understands conversational language",
            "You can say 'same client as before' to reuse context",
            "Combine commands: 'Create and send an invoice for...'",
            "Say 'automatically' to trigger workflow creation",
            "Ask analytics questions like 'Why did revenue drop?'",
        ],
        "supported_audio_formats": list(SUPPORTED_AUDIO_FORMATS),
        "max_audio_size_mb": MAX_AUDIO_MB,
        "ws_events": [
            "VOICE_TRANSCRIPTION_STARTED",
            "VOICE_TRANSCRIPTION_COMPLETED",
            "VOICE_COMMAND_PROCESSED",
            "VOICE_INVOICE_CREATED",
            "AI_COMMAND_EXECUTED",
        ],
    }
