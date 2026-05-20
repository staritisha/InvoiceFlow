"""
app/routers/ai_assistant.py

AI Assistant Router for InvoiceFlow AI Platform.
Covers conversational AI sidebar, memory system, command center, smart search,
recommendations, action suggestions, personalized tips, onboarding, insight cards,
and real-time WebSocket AI broadcasts.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import ActivityType, NotificationType
from app.core.permissions import require_permission
from app.database import get_db
from app.models import (
    Activity,
    AIConversation,
    BusinessInsight,
    Client,
    DashboardWidget,
    Invoice,
    InvoiceStatus,
    Notification,
    Payment,
    Reminder,
    User,
    Workflow,
)
from app.schemas import (
    AICommandRequest,
    AIFilterRequest,
    AISearchRequest,
    ChatMessageCreate,
    ChatMessageOut,
)
from app.services.ai_service import AIService
from app.services.notification_service import NotificationService
from app.websocket.manager import ws_manager
from auth import get_current_user

router = APIRouter(prefix="/ai", tags=["AI Assistant"])

ai_service = AIService()
notification_service = NotificationService()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _cache_key(*parts: Any) -> str:
    raw = ":".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def _build_business_context(db: AsyncSession, user: User) -> dict:
    """Gather a lightweight business snapshot to inject as AI context."""
    today = datetime.now(timezone.utc).date()
    month_start = today.replace(day=1)

    rev_stmt = select(
        func.coalesce(func.sum(Invoice.total), 0).label("total"),
        func.coalesce(func.sum(Invoice.amount_paid), 0).label("paid"),
        func.count(Invoice.id).label("count"),
    ).where(
        Invoice.team_id == user.team_id,
        Invoice.issue_date >= month_start,
    )
    rev = (await db.execute(rev_stmt)).mappings().one()

    overdue_stmt = select(func.count(Invoice.id)).where(
        Invoice.team_id == user.team_id,
        Invoice.status == InvoiceStatus.overdue,
    )
    overdue_count = int((await db.execute(overdue_stmt)).scalar_one() or 0)

    client_count_stmt = select(func.count(Client.id)).where(
        Client.team_id == user.team_id,
        Client.is_active.is_(True),
    )
    client_count = int((await db.execute(client_count_stmt)).scalar_one() or 0)

    high_risk_stmt = select(func.count(Client.id)).where(
        Client.team_id == user.team_id,
        Client.risk_score >= 70,
    )
    high_risk_clients = int((await db.execute(high_risk_stmt)).scalar_one() or 0)

    return {
        "user_name": user.full_name or user.username,
        "business_name": user.business_name or "your business",
        "month_revenue": float(rev["total"]),
        "month_collected": float(rev["paid"]),
        "invoice_count": int(rev["count"]),
        "overdue_count": overdue_count,
        "client_count": client_count,
        "high_risk_clients": high_risk_clients,
        "today": today.isoformat(),
    }


# ---------------------------------------------------------------------------
# POST /chat  — Conversational AI Sidebar
# ---------------------------------------------------------------------------


@router.post("/chat")
async def chat(
    payload: ChatMessageCreate,
    stream: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    session_id = payload.session_id or str(uuid4())

    # Load conversation history for this session (last 20 turns)
    history_stmt = (
        select(AIConversation)
        .where(
            AIConversation.user_id == current_user.id,
            AIConversation.session_id == session_id,
        )
        .order_by(AIConversation.created_at)
        .limit(20)
    )
    history_rows = (await db.execute(history_stmt)).scalars().all()
    history = [{"role": h.role, "content": h.content} for h in history_rows]

    # Business context snapshot
    business_context = await _build_business_context(db, current_user)

    # Persist user message
    user_msg = AIConversation(
        user_id=current_user.id,
        session_id=session_id,
        role="user",
        content=payload.message,
        context=business_context,
        created_at=_utcnow(),
    )
    db.add(user_msg)
    await db.flush()

    if stream:
        # Streaming path — return SSE
        async def _event_stream() -> AsyncGenerator[str, None]:
            full_response = ""
            async for chunk in ai_service.chat_stream(
                message=payload.message,
                history=history,
                business_context=business_context,
                user_preferences=current_user.preferences or {},
            ):
                full_response += chunk
                yield f"data: {json.dumps({'chunk': chunk, 'session_id': session_id})}\n\n"

            # Persist assistant response after streaming completes
            assistant_msg = AIConversation(
                user_id=current_user.id,
                session_id=session_id,
                role="assistant",
                content=full_response,
                context=business_context,
                created_at=_utcnow(),
            )
            db.add(assistant_msg)
            await db.commit()

            # Generate follow-up suggestions
            suggestions = await ai_service.generate_followup_suggestions(
                last_message=payload.message,
                response=full_response,
                business_context=business_context,
            )
            yield f"data: {json.dumps({'done': True, 'session_id': session_id, 'suggestions': suggestions})}\n\n"

        return StreamingResponse(_event_stream(), media_type="text/event-stream")

    # Non-streaming path
    response = await ai_service.chat(
        message=payload.message,
        history=history,
        business_context=business_context,
        user_preferences=current_user.preferences or {},
    )

    assistant_msg = AIConversation(
        user_id=current_user.id,
        session_id=session_id,
        role="assistant",
        content=response.get("message", ""),
        context=business_context,
        created_at=_utcnow(),
    )
    db.add(assistant_msg)
    await db.commit()

    # Log activity
    activity = Activity(
        team_id=current_user.team_id,
        user_id=current_user.id,
        action_type=ActivityType.ai_chat,
        entity_type="ai_conversation",
        entity_id=user_msg.id,
        description="AI assistant conversation",
        metadata={"session_id": session_id},
        created_at=_utcnow(),
    )
    db.add(activity)
    await db.commit()

    return {
        "session_id": session_id,
        "message": response.get("message", ""),
        "follow_up_suggestions": response.get("suggestions", []),
        "action_buttons": response.get("action_buttons", []),
        "referenced_entities": response.get("referenced_entities", []),
        "confidence": response.get("confidence", 1.0),
    }


# ---------------------------------------------------------------------------
# GET /chat/history/{session_id}  — Conversation history
# ---------------------------------------------------------------------------


@router.get("/chat/history/{session_id}")
async def get_chat_history(
    session_id: str,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = (
        select(AIConversation)
        .where(
            AIConversation.user_id == current_user.id,
            AIConversation.session_id == session_id,
        )
        .order_by(AIConversation.created_at)
        .limit(limit)
    )
    result = await db.execute(stmt)
    messages = result.scalars().all()

    if not messages:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or no messages in this session.",
        )

    # Restore AI context from latest snapshot
    latest_context = messages[-1].context if messages else {}

    return {
        "session_id": session_id,
        "message_count": len(messages),
        "started_at": messages[0].created_at.isoformat() if messages else None,
        "last_active": messages[-1].created_at.isoformat() if messages else None,
        "business_context_snapshot": latest_context,
        "messages": [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
                "context": m.context,
            }
            for m in messages
        ],
    }


# ---------------------------------------------------------------------------
# DELETE /chat/history/{session_id}  — Clear session memory
# ---------------------------------------------------------------------------


@router.delete("/chat/history/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_history(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    stmt = select(AIConversation).where(
        AIConversation.user_id == current_user.id,
        AIConversation.session_id == session_id,
    )
    result = await db.execute(stmt)
    messages = result.scalars().all()

    if not messages:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found.",
        )

    for msg in messages:
        await db.delete(msg)

    activity = Activity(
        team_id=current_user.team_id,
        user_id=current_user.id,
        action_type=ActivityType.deleted,
        entity_type="ai_conversation",
        entity_id=current_user.id,
        description=f"AI session {session_id} cleared",
        metadata={"session_id": session_id, "messages_deleted": len(messages)},
        created_at=_utcnow(),
    )
    db.add(activity)
    await db.commit()


# ---------------------------------------------------------------------------
# POST /command  — AI Command Center
# ---------------------------------------------------------------------------


@router.post("/command")
async def ai_command(
    payload: AICommandRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Natural language command execution engine.
    Classifies intent, extracts entities, plans steps, and either executes
    the action directly or returns a confirmation plan for destructive actions.
    """
    business_context = await _build_business_context(db, current_user)

    # AI: classify command intent and extract entities
    classification = await ai_service.classify_command(
        command=payload.command,
        business_context=business_context,
        user_permissions=_user_permission_list(current_user),
    )

    intent = classification.get("intent", "unknown")
    confidence = classification.get("confidence", 0.0)
    entities = classification.get("entities", {})
    is_destructive = classification.get("is_destructive", False)

    if confidence < 0.4:
        return {
            "status": "clarification_needed",
            "message": classification.get("clarification_prompt", "Could you clarify what you'd like to do?"),
            "suggestions": classification.get("suggestions", []),
            "intent": intent,
            "confidence": confidence,
        }

    # Destructive commands require explicit confirmation
    if is_destructive and not payload.confirmed:
        return {
            "status": "confirmation_required",
            "message": classification.get("confirmation_message", "Are you sure you want to proceed?"),
            "intent": intent,
            "entities": entities,
            "impact_summary": classification.get("impact_summary", ""),
            "confidence": confidence,
        }

    # Permission check
    required_permission = _intent_permission_map().get(intent)
    if required_permission and not _has_perm(current_user, required_permission):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You don't have permission to execute '{intent}' commands.",
        )

    # Dispatch action in background for long-running commands
    result = await ai_service.execute_command(
        intent=intent,
        entities=entities,
        user_id=str(current_user.id),
        team_id=str(current_user.team_id),
        business_context=business_context,
    )

    # Activity log
    activity = Activity(
        team_id=current_user.team_id,
        user_id=current_user.id,
        action_type=ActivityType.ai_command,
        entity_type="ai_command",
        entity_id=current_user.id,
        description=f"AI command: {intent}",
        metadata={"command": payload.command, "intent": intent, "entities": entities},
        created_at=_utcnow(),
    )
    db.add(activity)
    await db.commit()

    # WebSocket notification for completed commands
    await ws_manager.broadcast_to_team(
        str(current_user.team_id),
        {
            "event": "ai_command_executed",
            "intent": intent,
            "result_summary": result.get("summary", ""),
        },
    )

    return {
        "status": "executed",
        "intent": intent,
        "confidence": confidence,
        "entities": entities,
        "result": result.get("data", {}),
        "summary": result.get("summary", ""),
        "follow_up_suggestions": result.get("follow_up", []),
        "action_buttons": result.get("action_buttons", []),
    }


def _user_permission_list(user: User) -> list[str]:
    from app.core.permissions import ROLE_PERMISSIONS
    return ROLE_PERMISSIONS.get(user.role, [])


def _has_perm(user: User, permission: str) -> bool:
    return permission in _user_permission_list(user)


def _intent_permission_map() -> dict[str, str]:
    return {
        "create_invoice": "invoices:create",
        "send_reminder": "reminders:send",
        "generate_report": "reports:create",
        "delete_invoice": "invoices:delete",
        "create_workflow": "workflows:create",
        "trigger_automation": "workflows:trigger",
    }


# ---------------------------------------------------------------------------
# POST /filter  — AI Conversational Filters
# ---------------------------------------------------------------------------


@router.post("/filter")
async def ai_filter(
    payload: AIFilterRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Convert a natural language description into structured query filters.
    The frontend applies these filters to any entity list.
    """
    parsed = await ai_service.parse_filter(
        query=payload.query,
        entity=payload.entity or "invoice",
        business_context=await _build_business_context(db, current_user),
    )

    return {
        "query": payload.query,
        "entity": payload.entity or "invoice",
        "filters": parsed.get("filters", {}),
        "sort": parsed.get("sort", {}),
        "explanation": parsed.get("explanation", ""),
        "confidence": parsed.get("confidence", 0.0),
        "ai_suggested_label": parsed.get("label", ""),
    }


# ---------------------------------------------------------------------------
# POST /search  — AI Smart Search
# ---------------------------------------------------------------------------


@router.post("/search")
async def ai_search(
    payload: AISearchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Universal semantic search across invoices, clients, payments, activities, insights."""
    query = payload.query.strip()
    if not query:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Search query cannot be empty.")

    pattern = f"%{query}%"
    team_id = current_user.team_id

    # Parallel DB lookups for all entity types
    invoice_stmt = (
        select(Invoice.id, Invoice.number, Invoice.status, Invoice.total, Invoice.due_date)
        .where(
            Invoice.team_id == team_id,
            or_(
                Invoice.number.ilike(pattern),
                Invoice.description.ilike(pattern),
                Invoice.notes.ilike(pattern),
                Invoice.ai_description.ilike(pattern),
            ),
        )
        .limit(10)
    )
    client_stmt = (
        select(Client.id, Client.name, Client.company, Client.email, Client.risk_score)
        .where(
            Client.team_id == team_id,
            Client.is_active.is_(True),
            or_(
                Client.name.ilike(pattern),
                Client.company.ilike(pattern),
                Client.email.ilike(pattern),
                Client.notes.ilike(pattern),
            ),
        )
        .limit(10)
    )
    insight_stmt = (
        select(BusinessInsight.id, BusinessInsight.title, BusinessInsight.content, BusinessInsight.category)
        .where(
            BusinessInsight.team_id == team_id,
            or_(
                BusinessInsight.title.ilike(pattern),
                BusinessInsight.content.ilike(pattern),
            ),
        )
        .limit(5)
    )
    workflow_stmt = (
        select(Workflow.id, Workflow.name, Workflow.description, Workflow.is_active)
        .where(
            Workflow.team_id == team_id,
            or_(
                Workflow.name.ilike(pattern),
                Workflow.description.ilike(pattern),
            ),
        )
        .limit(5)
    )
    activity_stmt = (
        select(Activity.id, Activity.action_type, Activity.description, Activity.created_at)
        .where(
            Activity.team_id == team_id,
            Activity.description.ilike(pattern),
        )
        .order_by(desc(Activity.created_at))
        .limit(5)
    )

    invoice_rows, client_rows, insight_rows, workflow_rows, activity_rows = (
        (await db.execute(invoice_stmt)).all(),
        (await db.execute(client_stmt)).all(),
        (await db.execute(insight_stmt)).all(),
        (await db.execute(workflow_stmt)).all(),
        (await db.execute(activity_stmt)).all(),
    )

    # AI relevance re-ranking
    raw_results = {
        "invoices": [
            {
                "id": str(r[0]), "number": r[1], "status": r[2],
                "total": float(r[3] or 0),
                "due_date": r[4].isoformat() if r[4] else None,
                "entity_type": "invoice",
            }
            for r in invoice_rows
        ],
        "clients": [
            {
                "id": str(r[0]), "name": r[1], "company": r[2],
                "email": r[3], "risk_score": r[4] or 0,
                "entity_type": "client",
            }
            for r in client_rows
        ],
        "insights": [
            {
                "id": str(r[0]), "title": r[1], "content": r[2][:200],
                "category": r[3], "entity_type": "insight",
            }
            for r in insight_rows
        ],
        "workflows": [
            {
                "id": str(r[0]), "name": r[1], "description": r[2],
                "is_active": r[3], "entity_type": "workflow",
            }
            for r in workflow_rows
        ],
        "activities": [
            {
                "id": str(r[0]), "action_type": r[1], "description": r[2],
                "created_at": r[3].isoformat() if r[3] else None,
                "entity_type": "activity",
            }
            for r in activity_rows
        ],
    }

    ranked = await ai_service.rank_search_results(
        query=query, results=raw_results
    )

    return {
        "query": query,
        "results": raw_results,
        "ranked_results": ranked.get("ranked", []),
        "total_found": sum(len(v) for v in raw_results.values()),
        "ai_summary": ranked.get("summary", ""),
        "suggested_actions": ranked.get("suggested_actions", []),
    }


# ---------------------------------------------------------------------------
# GET /recommendations  — AI Recommendations Panel
# ---------------------------------------------------------------------------


@router.get("/recommendations")
async def get_recommendations(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    business_context = await _build_business_context(db, current_user)

    # Gather extra signals for richer recommendations
    high_risk_clients_stmt = (
        select(Client.id, Client.name, Client.risk_score, Client.total_invoiced, Client.total_paid)
        .where(Client.team_id == current_user.team_id, Client.risk_score >= 60)
        .order_by(desc(Client.risk_score))
        .limit(5)
    )
    high_risk_rows = (await db.execute(high_risk_clients_stmt)).all()

    overdue_invoices_stmt = (
        select(Invoice.id, Invoice.number, Invoice.balance_due, Invoice.due_date, Invoice.client_id)
        .where(
            Invoice.team_id == current_user.team_id,
            Invoice.status == InvoiceStatus.overdue,
        )
        .order_by(desc(Invoice.balance_due))
        .limit(5)
    )
    overdue_rows = (await db.execute(overdue_invoices_stmt)).all()

    recs = await ai_service.generate_recommendations(
        business_context=business_context,
        high_risk_clients=[
            {"id": str(r[0]), "name": r[1], "risk_score": r[2],
             "total_invoiced": float(r[3] or 0), "total_paid": float(r[4] or 0)}
            for r in high_risk_rows
        ],
        overdue_invoices=[
            {"id": str(r[0]), "number": r[1], "balance": float(r[2] or 0),
             "due_date": r[3].isoformat() if r[3] else None}
            for r in overdue_rows
        ],
    )

    # Broadcast live recommendations via WebSocket
    await ws_manager.broadcast_to_team(
        str(current_user.team_id),
        {"event": "recommendations_refreshed", "count": len(recs.get("items", []))},
    )

    return {
        "recommendations": recs.get("items", []),
        "total": len(recs.get("items", [])),
        "generated_at": _utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# GET /action-suggestions  — Real-time AI Action Engine
# ---------------------------------------------------------------------------


@router.get("/action-suggestions")
async def action_suggestions(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    business_context = await _build_business_context(db, current_user)

    # Recent activity for context
    recent_activity_stmt = (
        select(Activity.action_type, Activity.description, Activity.created_at)
        .where(Activity.team_id == current_user.team_id)
        .order_by(desc(Activity.created_at))
        .limit(10)
    )
    recent = (await db.execute(recent_activity_stmt)).all()
    recent_activity = [
        {"action": r[0], "description": r[1], "at": r[2].isoformat() if r[2] else None}
        for r in recent
    ]

    suggestions = await ai_service.generate_action_suggestions(
        business_context=business_context,
        recent_activity=recent_activity,
        user_role=str(current_user.role),
    )

    return {
        "suggestions": [
            {
                "id": str(uuid4()),
                "action_type": s.get("action_type", ""),
                "title": s.get("title", ""),
                "description": s.get("description", ""),
                "priority": s.get("priority", "medium"),
                "business_impact": s.get("business_impact", ""),
                "confidence": s.get("confidence", 0.0),
                "execution_hook": s.get("execution_hook", {}),
            }
            for s in suggestions.get("items", [])
        ],
        "generated_at": _utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# GET /personalized-tips  — AI Coaching Engine
# ---------------------------------------------------------------------------


@router.get("/personalized-tips")
async def personalized_tips(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    business_context = await _build_business_context(db, current_user)

    # Usage signals
    invoice_count_stmt = select(func.count(Invoice.id)).where(
        Invoice.team_id == current_user.team_id
    )
    invoice_count = int((await db.execute(invoice_count_stmt)).scalar_one() or 0)

    client_count_stmt = select(func.count(Client.id)).where(
        Client.team_id == current_user.team_id
    )
    client_count = int((await db.execute(client_count_stmt)).scalar_one() or 0)

    user_signals = {
        "invoice_count": invoice_count,
        "client_count": client_count,
        "subscription_tier": current_user.subscription_tier,
        "preferences": current_user.preferences or {},
        "last_login": current_user.last_login.isoformat() if current_user.last_login else None,
        "role": str(current_user.role),
    }

    tips = await ai_service.generate_personalized_tips(
        business_context=business_context,
        user_signals=user_signals,
    )

    return {
        "tips": tips.get("items", []),
        "coaching_focus": tips.get("coaching_focus", "general"),
        "generated_at": _utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# GET /onboarding-steps  — AI-Powered Onboarding
# ---------------------------------------------------------------------------


@router.get("/onboarding-steps")
async def onboarding_steps(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    # Determine what the user has already done
    has_clients_stmt = select(func.count(Client.id)).where(Client.team_id == current_user.team_id)
    has_invoices_stmt = select(func.count(Invoice.id)).where(Invoice.team_id == current_user.team_id)
    has_workflows_stmt = select(func.count(Workflow.id)).where(Workflow.team_id == current_user.team_id)

    has_clients = int((await db.execute(has_clients_stmt)).scalar_one() or 0) > 0
    has_invoices = int((await db.execute(has_invoices_stmt)).scalar_one() or 0) > 0
    has_workflows = int((await db.execute(has_workflows_stmt)).scalar_one() or 0) > 0

    completion_state = {
        "profile_complete": bool(current_user.business_name and current_user.full_name),
        "first_client_added": has_clients,
        "first_invoice_created": has_invoices,
        "first_workflow_created": has_workflows,
        "ai_assistant_used": False,  # can be tracked via activity
        "payment_method_connected": False,  # Stripe integration
    }

    steps = await ai_service.generate_onboarding_steps(
        user_role=str(current_user.role),
        completion_state=completion_state,
        subscription_tier=current_user.subscription_tier,
        business_name=current_user.business_name,
    )

    progress = sum(1 for v in completion_state.values() if v)
    total = len(completion_state)

    return {
        "progress_pct": round(progress / total * 100),
        "steps_completed": progress,
        "total_steps": total,
        "completion_state": completion_state,
        "steps": steps.get("steps", []),
        "next_recommended_step": steps.get("next_step", {}),
        "ai_welcome_message": steps.get("welcome_message", f"Welcome, {current_user.full_name or 'there'}!"),
    }


# ---------------------------------------------------------------------------
# GET /memory/context  — Persistent AI Business Memory
# ---------------------------------------------------------------------------


@router.get("/memory/context")
async def memory_context(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    # Latest 5 sessions
    sessions_stmt = (
        select(AIConversation.session_id, func.count(AIConversation.id).label("msg_count"),
               func.max(AIConversation.created_at).label("last_active"))
        .where(AIConversation.user_id == current_user.id)
        .group_by(AIConversation.session_id)
        .order_by(desc("last_active"))
        .limit(5)
    )
    session_rows = (await db.execute(sessions_stmt)).all()
    recent_sessions = [
        {
            "session_id": r[0],
            "message_count": int(r[1]),
            "last_active": r[2].isoformat() if r[2] else None,
        }
        for r in session_rows
    ]

    # Most frequently used commands (from activity log)
    cmd_stmt = (
        select(Activity.action_type, func.count(Activity.id).label("count"))
        .where(
            Activity.user_id == current_user.id,
            Activity.action_type == ActivityType.ai_command,
        )
        .group_by(Activity.action_type)
        .order_by(desc("count"))
        .limit(10)
    )
    # we grab description patterns instead
    desc_stmt = (
        select(Activity.description, func.count(Activity.id).label("count"))
        .where(
            Activity.user_id == current_user.id,
            Activity.entity_type == "ai_command",
        )
        .group_by(Activity.description)
        .order_by(desc("count"))
        .limit(5)
    )
    cmd_rows = (await db.execute(desc_stmt)).all()
    frequent_commands = [{"command": r[0], "uses": int(r[1])} for r in cmd_rows]

    # Stored user preferences
    preferences = current_user.preferences or {}

    # AI memory summary
    memory_summary = await ai_service.summarize_user_memory(
        user_name=current_user.full_name or current_user.username,
        recent_sessions=recent_sessions,
        frequent_commands=frequent_commands,
        preferences=preferences,
        business_name=current_user.business_name,
    )

    return {
        "user_id": str(current_user.id),
        "recent_sessions": recent_sessions,
        "frequent_commands": frequent_commands,
        "preferences": preferences,
        "ai_memory_summary": memory_summary.get("summary", ""),
        "personalization_data": memory_summary.get("personalization", {}),
        "communication_tone": memory_summary.get("tone", "professional"),
    }


# ---------------------------------------------------------------------------
# GET /insights/cards  — Startup-Style AI Insight Cards
# ---------------------------------------------------------------------------


@router.get("/insights/cards")
async def insight_cards(
    limit: int = Query(8, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    business_context = await _build_business_context(db, current_user)

    # Fetch latest stored insights
    stored_stmt = (
        select(BusinessInsight)
        .where(
            BusinessInsight.team_id == current_user.team_id,
            BusinessInsight.ai_generated.is_(True),
        )
        .order_by(desc(BusinessInsight.id))
        .limit(limit)
    )
    stored = (await db.execute(stored_stmt)).scalars().all()

    # Generate fresh cards if fewer than requested
    fresh_cards: list[dict] = []
    if len(stored) < limit:
        generated = await ai_service.generate_insight_cards(
            business_context=business_context,
            count=limit - len(stored),
        )
        fresh_cards = generated.get("cards", [])

        for card in fresh_cards:
            insight = BusinessInsight(
                team_id=current_user.team_id,
                type=card.get("type", "general"),
                title=card.get("headline", ""),
                content=card.get("body", ""),
                severity=card.get("severity", "info"),
                category=card.get("category", "general"),
                is_read=False,
                ai_generated=True,
                metadata=card,
            )
            db.add(insight)
        await db.commit()

    stored_cards = [
        {
            "id": str(i.id),
            "headline": i.title,
            "body": i.content,
            "type": i.type,
            "severity": i.severity,
            "category": i.category,
            "is_read": i.is_read,
            "metadata": i.metadata or {},
            "animation_type": _card_animation(i.severity),
        }
        for i in stored
    ]

    all_cards = stored_cards + fresh_cards
    all_cards.sort(key=lambda c: _severity_rank(c.get("severity", "info")))

    # Broadcast new cards via WebSocket
    if fresh_cards:
        await ws_manager.broadcast_to_team(
            str(current_user.team_id),
            {"event": "insight_cards_refreshed", "new_cards": len(fresh_cards)},
        )

    return {
        "cards": all_cards[:limit],
        "total": len(all_cards),
        "generated_at": _utcnow().isoformat(),
    }


def _card_animation(severity: str) -> str:
    return {
        "critical": "pulse_red",
        "high": "slide_warning",
        "medium": "fade_blue",
        "low": "slide_green",
        "info": "fade_in",
    }.get(severity, "fade_in")


def _severity_rank(severity: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(severity, 5)
