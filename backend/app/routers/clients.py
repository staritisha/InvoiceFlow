"""
app/routers/clients.py

Advanced Client Management Router for InvoiceFlow AI Platform.
Covers full CRUD, AI intelligence, risk scoring, payment behaviour,
leaderboard, segmentation, real-time WebSocket events, RBAC, and caching.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from sqlalchemy import case, desc, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import ActivityType, NotificationType, UserRole
from app.core.permissions import has_permission, require_permission
from app.database import get_db
from app.models import (
    Activity,
    AIConversation,
    BusinessInsight,
    Client,
    Invoice,
    InvoiceStatus,
    Notification,
    Payment,
    Reminder,
    User,
)
from app.schemas import (
    ClientCreate,
    ClientOut,
    ClientRiskScore,
    ClientSummary,
    ClientUpdate,
    PaginatedResponse,
)
from app.services.ai_service import AIService
from app.services.client_service import ClientService
from app.services.notification_service import NotificationService
from app.websocket.manager import ws_manager
from auth import get_current_user

router = APIRouter(prefix="/clients", tags=["Clients"])

ai_service = AIService()
client_service = ClientService()
notification_service = NotificationService()


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
    activity = Activity(
        team_id=team_id,
        user_id=user_id,
        action_type=action_type,
        entity_type="client",
        entity_id=entity_id,
        description=description,
        metadata=metadata or {},
        created_at=_utcnow(),
    )
    db.add(activity)


async def _get_client_or_404(
    client_id: UUID,
    db: AsyncSession,
    current_user: User,
    *,
    allow_archived: bool = False,
) -> Client:
    stmt = (
        select(Client)
        .where(Client.id == client_id, Client.team_id == current_user.team_id)
        .options(
            selectinload(Client.invoices).selectinload(Invoice.items),
            selectinload(Client.invoices).selectinload(Invoice.payments),
            selectinload(Client.invoices).selectinload(Invoice.reminders),
        )
    )
    if not allow_archived:
        stmt = stmt.where(Client.is_active.is_(True))

    result = await db.execute(stmt)
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    return client


# ---------------------------------------------------------------------------
# GET /  — Advanced paginated client listing
# ---------------------------------------------------------------------------


@router.get("/", response_model=PaginatedResponse)
async def list_clients(
    request: Request,
    # Pagination
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    # Search
    search: Optional[str] = Query(None, max_length=200),
    # Filters
    high_risk: Optional[bool] = None,
    overdue: Optional[bool] = None,
    top_paying: Optional[bool] = None,
    inactive: Optional[bool] = None,
    vip: Optional[bool] = None,
    ai_priority: Optional[bool] = None,
    # Sort
    sort_by: str = Query(
        "newest",
        regex="^(revenue|payment_speed|overdue_amount|newest|highest_risk)$",
    ),
    # AI conversational filter
    ai_filter: Optional[str] = Query(None, max_length=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    offset = (page - 1) * page_size

    stmt = select(Client).where(
        Client.team_id == current_user.team_id,
        Client.is_active.is_(True),
    )

    # --- Search ---
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            or_(
                Client.name.ilike(pattern),
                Client.email.ilike(pattern),
                Client.company.ilike(pattern),
                Client.phone.ilike(pattern),
                Client.tags.cast(str).ilike(pattern),
            )
        )

    # --- AI conversational filter (parse natural language to query params) ---
    if ai_filter:
        parsed = await ai_service.parse_filter(ai_filter, entity="client")
        if parsed.get("high_risk"):
            high_risk = True
        if parsed.get("overdue"):
            overdue = True

    # --- Boolean filters ---
    if high_risk:
        stmt = stmt.where(Client.risk_score >= 70)
    if overdue:
        overdue_sub = (
            select(Invoice.client_id)
            .where(
                Invoice.status == InvoiceStatus.overdue,
                Invoice.team_id == current_user.team_id,
            )
            .distinct()
        )
        stmt = stmt.where(Client.id.in_(overdue_sub))
    if top_paying:
        stmt = stmt.where(Client.total_paid >= 10_000)
    if inactive:
        cutoff = _utcnow().replace(year=_utcnow().year - 1)
        no_recent_sub = (
            select(Invoice.client_id)
            .where(
                Invoice.issue_date >= cutoff,
                Invoice.team_id == current_user.team_id,
            )
            .distinct()
        )
        stmt = stmt.where(Client.id.not_in(no_recent_sub))
    if vip:
        stmt = stmt.where(Client.tags.cast(str).contains("VIP"))
    if ai_priority:
        stmt = stmt.where(Client.tags.cast(str).contains("ai_priority"))

    # --- Sort ---
    sort_map = {
        "revenue": desc(Client.total_invoiced),
        "payment_speed": Client.average_days_to_pay,
        "overdue_amount": desc(
            select(func.coalesce(func.sum(Invoice.balance_due), 0))
            .where(
                Invoice.client_id == Client.id,
                Invoice.status == InvoiceStatus.overdue,
            )
            .correlate(Client)
            .scalar_subquery()
        ),
        "newest": desc(Client.created_at),
        "highest_risk": desc(Client.risk_score),
    }
    stmt = stmt.order_by(sort_map[sort_by])

    # --- Count ---
    count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = count_result.scalar_one()

    # --- Paginate ---
    stmt = stmt.offset(offset).limit(page_size)
    result = await db.execute(stmt)
    clients = result.scalars().all()

    # --- KPI summary ---
    kpi_stmt = select(
        func.count(Client.id).label("total_clients"),
        func.sum(Client.total_invoiced).label("total_invoiced"),
        func.sum(Client.total_paid).label("total_paid"),
        func.avg(Client.risk_score).label("avg_risk"),
        func.avg(Client.average_days_to_pay).label("avg_payment_days"),
    ).where(
        Client.team_id == current_user.team_id,
        Client.is_active.is_(True),
    )
    kpi_result = await db.execute(kpi_stmt)
    kpi = kpi_result.mappings().one()

    return {
        "items": [ClientOut.model_validate(c) for c in clients],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": -(-total // page_size),
        "kpi_summary": {
            "total_clients": kpi["total_clients"] or 0,
            "total_invoiced": float(kpi["total_invoiced"] or 0),
            "total_paid": float(kpi["total_paid"] or 0),
            "avg_risk_score": round(float(kpi["avg_risk"] or 0), 1),
            "avg_payment_days": round(float(kpi["avg_payment_days"] or 0), 1),
        },
    }


# ---------------------------------------------------------------------------
# POST /  — Create client with AI enrichment
# ---------------------------------------------------------------------------


@router.post("/", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
async def create_client(
    payload: ClientCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Client:
    require_permission(current_user, "clients:create")

    # --- Duplicate detection ---
    dup_stmt = select(Client).where(
        Client.team_id == current_user.team_id,
        Client.is_active.is_(True),
        or_(
            Client.email == payload.email,
            func.lower(Client.name) == payload.name.lower(),
        ),
    )
    dup_result = await db.execute(dup_stmt)
    if dup_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A client with this name or email already exists in your team.",
        )

    client = Client(
        **payload.model_dump(exclude={"tags"}),
        team_id=current_user.team_id,
        created_by=current_user.id,
        tags=payload.tags or [],
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(client)
    await db.flush()  # get client.id before background tasks

    # --- Activity log ---
    await _log_activity(
        db,
        team_id=current_user.team_id,
        user_id=current_user.id,
        action_type=ActivityType.created,
        entity_id=client.id,
        description=f"Client '{client.name}' created",
    )
    await db.commit()
    await db.refresh(client)

    # --- Background AI enrichment ---
    background_tasks.add_task(
        _enrich_client_ai, client_id=client.id, team_id=current_user.team_id
    )

    # --- Real-time broadcast ---
    await ws_manager.broadcast_to_team(
        str(current_user.team_id),
        {"event": "client_created", "client_id": str(client.id), "name": client.name},
    )

    return client


async def _enrich_client_ai(client_id: UUID, team_id: UUID) -> None:
    """
    Background task: AI categorization, risk prediction, auto-tagging,
    payment behaviour prediction, follow-up recommendations.
    Runs after the HTTP response is sent so it never blocks the request.
    """
    from app.database import AsyncSessionLocal  # local import to avoid circular

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Client).where(Client.id == client_id))
        client = result.scalar_one_or_none()
        if not client:
            return

        enrichment = await ai_service.enrich_client(
            name=client.name,
            email=client.email,
            company=client.company,
            notes=client.notes,
        )

        tags: list[str] = client.tags or []
        for tag in enrichment.get("auto_tags", []):
            if tag not in tags:
                tags.append(tag)

        await db.execute(
            update(Client)
            .where(Client.id == client_id)
            .values(
                risk_score=enrichment.get("risk_score", client.risk_score),
                tags=tags,
                notes=(client.notes or "") + "\n\n" + enrichment.get("smart_notes", ""),
                updated_at=_utcnow(),
            )
        )

        insight = BusinessInsight(
            team_id=team_id,
            type="client_onboarding",
            title=f"New client: {client.name}",
            content=enrichment.get("insight", ""),
            severity="info",
            category="client",
            ai_generated=True,
            metadata=enrichment,
        )
        db.add(insight)
        await db.commit()


# ---------------------------------------------------------------------------
# GET /leaderboard/top  — Must come BEFORE /{id} to avoid route shadowing
# ---------------------------------------------------------------------------


@router.get("/leaderboard/top")
async def client_leaderboard(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    base = select(Client).where(
        Client.team_id == current_user.team_id,
        Client.is_active.is_(True),
    )

    async def _top(order_col, label: str) -> list[dict]:
        q = base.order_by(order_col).limit(limit)
        r = await db.execute(q)
        rows = r.scalars().all()
        return [
            {
                "id": str(c.id),
                "name": c.name,
                "company": c.company,
                "email": c.email,
                "avatar_initials": (c.name or "?")[:2].upper(),
                "metric": label,
                "total_invoiced": float(c.total_invoiced or 0),
                "total_paid": float(c.total_paid or 0),
                "avg_payment_days": round(float(c.average_days_to_pay or 0), 1),
                "risk_score": c.risk_score or 0,
                "payment_behavior_score": c.payment_behavior_score or 0,
                "tags": c.tags or [],
            }
            for c in rows
        ]

    highest_revenue = await _top(desc(Client.total_invoiced), "highest_revenue")
    fastest_paying = await _top(Client.average_days_to_pay, "fastest_paying")
    most_reliable = await _top(desc(Client.payment_behavior_score), "most_reliable")
    lowest_risk = await _top(Client.risk_score, "lowest_risk")

    # Lifetime value (paid + outstanding) approx
    lifetime_sub = (
        select(
            Invoice.client_id,
            func.sum(Invoice.total).label("ltv"),
        )
        .where(Invoice.team_id == current_user.team_id)
        .group_by(Invoice.client_id)
        .order_by(desc("ltv"))
        .limit(limit)
        .subquery()
    )
    ltv_stmt = select(Client, lifetime_sub.c.ltv).join(
        lifetime_sub, Client.id == lifetime_sub.c.client_id
    )
    ltv_result = await db.execute(ltv_stmt)
    highest_ltv = [
        {
            "id": str(c.id),
            "name": c.name,
            "company": c.company,
            "metric": "highest_lifetime_value",
            "lifetime_value": float(ltv or 0),
        }
        for c, ltv in ltv_result.all()
    ]

    return {
        "highest_revenue": highest_revenue,
        "fastest_paying": fastest_paying,
        "most_reliable": most_reliable,
        "lowest_risk": lowest_risk,
        "highest_lifetime_value": highest_ltv,
    }


# ---------------------------------------------------------------------------
# GET /{id}  — Full client profile
# ---------------------------------------------------------------------------


@router.get("/{client_id}", response_model=ClientOut)
async def get_client(
    client_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    client = await _get_client_or_404(client_id, db, current_user)

    invoices = client.invoices or []
    paid_invoices = [i for i in invoices if i.status == InvoiceStatus.paid]
    overdue_invoices = [i for i in invoices if i.status == InvoiceStatus.overdue]
    outstanding = sum(i.balance_due for i in invoices if i.balance_due)

    # Average payment time
    payment_times: list[float] = []
    for inv in paid_invoices:
        if inv.paid_date and inv.issue_date:
            delta = (inv.paid_date - inv.issue_date).days
            payment_times.append(delta)
    avg_payment_days = sum(payment_times) / len(payment_times) if payment_times else None

    # Activity timeline (last 20 activities)
    act_stmt = (
        select(Activity)
        .where(Activity.entity_id == client.id, Activity.entity_type == "client")
        .order_by(desc(Activity.created_at))
        .limit(20)
    )
    act_result = await db.execute(act_stmt)
    activity_timeline = [
        {
            "id": str(a.id),
            "action_type": a.action_type,
            "description": a.description,
            "metadata": a.metadata,
            "created_at": a.created_at.isoformat(),
        }
        for a in act_result.scalars().all()
    ]

    # Reminder history
    reminder_invoice_ids = [i.id for i in invoices]
    reminder_history: list[dict] = []
    if reminder_invoice_ids:
        rem_stmt = (
            select(Reminder)
            .where(Reminder.invoice_id.in_(reminder_invoice_ids))
            .order_by(desc(Reminder.scheduled_at))
            .limit(10)
        )
        rem_result = await db.execute(rem_stmt)
        reminder_history = [
            {
                "id": str(r.id),
                "type": r.type,
                "status": r.status,
                "scheduled_at": r.scheduled_at.isoformat() if r.scheduled_at else None,
                "sent_at": r.sent_at.isoformat() if r.sent_at else None,
                "tone": r.tone,
            }
            for r in rem_result.scalars().all()
        ]

    # AI insights for this client
    insight_stmt = (
        select(BusinessInsight)
        .where(
            BusinessInsight.team_id == current_user.team_id,
            BusinessInsight.metadata.cast(str).contains(str(client_id)),
        )
        .order_by(desc(BusinessInsight.created_at if hasattr(BusinessInsight, "created_at") else BusinessInsight.id))
        .limit(5)
    )
    insight_result = await db.execute(insight_stmt)
    ai_insights = [
        {
            "id": str(i.id),
            "type": i.type,
            "title": i.title,
            "content": i.content,
            "severity": i.severity,
        }
        for i in insight_result.scalars().all()
    ]

    # Relationship health score (composite)
    payment_score = client.payment_behavior_score or 50
    risk_penalty = (client.risk_score or 0) * 0.5
    overdue_penalty = min(len(overdue_invoices) * 5, 30)
    relationship_health = max(0, min(100, payment_score - risk_penalty - overdue_penalty))

    return {
        **ClientOut.model_validate(client).model_dump(),
        "total_invoices": len(invoices),
        "total_paid": float(client.total_paid or 0),
        "outstanding_balance": float(outstanding),
        "avg_payment_days": round(avg_payment_days, 1) if avg_payment_days else None,
        "late_payment_count": len(overdue_invoices),
        "relationship_health_score": round(relationship_health, 1),
        "invoice_history": [
            {
                "id": str(i.id),
                "number": i.number,
                "status": i.status,
                "total": float(i.total or 0),
                "balance_due": float(i.balance_due or 0),
                "issue_date": i.issue_date.isoformat() if i.issue_date else None,
                "due_date": i.due_date.isoformat() if i.due_date else None,
            }
            for i in sorted(invoices, key=lambda x: x.issue_date or datetime.min, reverse=True)[:10]
        ],
        "activity_timeline": activity_timeline,
        "reminder_history": reminder_history,
        "ai_insights": ai_insights,
        "smart_recommendations": await ai_service.get_client_recommendations(
            client_id=str(client.id),
            risk_score=client.risk_score,
            outstanding_balance=float(outstanding),
            avg_payment_days=avg_payment_days,
        ),
    }


# ---------------------------------------------------------------------------
# PUT /{id}  — Update client
# ---------------------------------------------------------------------------


@router.put("/{client_id}", response_model=ClientOut)
async def update_client(
    client_id: UUID,
    payload: ClientUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Client:
    require_permission(current_user, "clients:update")
    client = await _get_client_or_404(client_id, db, current_user)

    update_data = payload.model_dump(exclude_unset=True)

    # Tag management — merge incoming tags with existing
    if "tags" in update_data:
        existing_tags: list[str] = client.tags or []
        new_tags: list[str] = update_data["tags"] or []
        merged = list(dict.fromkeys(existing_tags + new_tags))
        update_data["tags"] = merged

    changed_fields = {k: v for k, v in update_data.items() if getattr(client, k, None) != v}
    update_data["updated_at"] = _utcnow()

    for field, value in update_data.items():
        setattr(client, field, value)

    await _log_activity(
        db,
        team_id=current_user.team_id,
        user_id=current_user.id,
        action_type=ActivityType.updated,
        entity_id=client.id,
        description=f"Client '{client.name}' updated",
        metadata={"changed_fields": list(changed_fields.keys())},
    )
    await db.commit()
    await db.refresh(client)

    # Background: recalculate risk and regenerate AI summary
    if any(f in changed_fields for f in ("notes", "company", "address", "email")):
        background_tasks.add_task(_recalculate_risk_bg, client_id=client.id)

    # Real-time sync
    await ws_manager.broadcast_to_team(
        str(current_user.team_id),
        {
            "event": "client_updated",
            "client_id": str(client.id),
            "changed_fields": list(changed_fields.keys()),
        },
    )

    return client


async def _recalculate_risk_bg(client_id: UUID) -> None:
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Client)
            .where(Client.id == client_id)
            .options(selectinload(Client.invoices).selectinload(Invoice.payments))
        )
        client = result.scalar_one_or_none()
        if not client:
            return

        risk_data = await ai_service.calculate_risk(
            client_name=client.name,
            total_invoiced=float(client.total_invoiced or 0),
            total_paid=float(client.total_paid or 0),
            avg_days=float(client.average_days_to_pay or 0),
            overdue_count=sum(
                1 for i in (client.invoices or []) if i.status == InvoiceStatus.overdue
            ),
        )
        await db.execute(
            update(Client)
            .where(Client.id == client_id)
            .values(
                risk_score=risk_data.get("risk_score", client.risk_score),
                updated_at=_utcnow(),
            )
        )
        await db.commit()


# ---------------------------------------------------------------------------
# DELETE /{id}  — Soft delete (archive)
# ---------------------------------------------------------------------------


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_client(
    client_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    require_permission(current_user, "clients:delete")
    client = await _get_client_or_404(client_id, db, current_user)

    # Block deletion if client has unpaid invoices
    unpaid_stmt = select(func.count(Invoice.id)).where(
        Invoice.client_id == client_id,
        Invoice.status.in_([InvoiceStatus.sent, InvoiceStatus.overdue]),
        Invoice.balance_due > 0,
    )
    unpaid_count_result = await db.execute(unpaid_stmt)
    unpaid_count = unpaid_count_result.scalar_one()
    if unpaid_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot archive client: {unpaid_count} unpaid invoice(s) still outstanding. "
                   "Settle all outstanding balances before removing this client.",
        )

    # Soft delete — set is_active = False
    client.is_active = False
    client.updated_at = _utcnow()

    await _log_activity(
        db,
        team_id=current_user.team_id,
        user_id=current_user.id,
        action_type=ActivityType.deleted,
        entity_id=client.id,
        description=f"Client '{client.name}' archived",
    )
    await db.commit()

    await ws_manager.broadcast_to_team(
        str(current_user.team_id),
        {"event": "client_archived", "client_id": str(client_id)},
    )


# ---------------------------------------------------------------------------
# GET /{id}/summary  — AI-generated client summary
# ---------------------------------------------------------------------------


@router.get("/{client_id}/summary", response_model=ClientSummary)
async def get_client_summary(
    client_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    client = await _get_client_or_404(client_id, db, current_user)

    invoices = client.invoices or []
    total_invoiced = float(client.total_invoiced or 0)
    total_paid = float(client.total_paid or 0)
    outstanding = total_invoiced - total_paid
    overdue_count = sum(1 for i in invoices if i.status == InvoiceStatus.overdue)
    avg_days = float(client.average_days_to_pay or 0)

    # Monthly revenue trend (last 6 months)
    from collections import defaultdict
    monthly: dict[str, float] = defaultdict(float)
    for inv in invoices:
        if inv.issue_date:
            key = inv.issue_date.strftime("%Y-%m")
            monthly[key] += float(inv.total or 0)

    sorted_months = sorted(monthly.items())[-6:]
    revenue_trend = [{"month": m, "revenue": v} for m, v in sorted_months]

    # AI-generated summary from LLM
    ai_summary = await ai_service.generate_client_summary(
        client_name=client.name,
        company=client.company,
        total_invoiced=total_invoiced,
        total_paid=total_paid,
        outstanding=outstanding,
        avg_payment_days=avg_days,
        overdue_count=overdue_count,
        risk_score=client.risk_score or 0,
        notes=client.notes,
    )

    # Relationship health score
    payment_score = client.payment_behavior_score or 50
    risk_penalty = (client.risk_score or 0) * 0.5
    overdue_penalty = min(overdue_count * 5, 30)
    relationship_health = max(0, min(100, payment_score - risk_penalty - overdue_penalty))

    return {
        "client_id": str(client.id),
        "name": client.name,
        "company": client.company,
        "total_invoiced": total_invoiced,
        "total_paid": total_paid,
        "outstanding_balance": outstanding,
        "avg_payment_days": round(avg_days, 1),
        "overdue_invoice_count": overdue_count,
        "risk_score": client.risk_score or 0,
        "payment_behavior_score": client.payment_behavior_score or 0,
        "relationship_health_score": round(relationship_health, 1),
        "revenue_trend": revenue_trend,
        "ai_analysis": ai_summary.get("analysis", ""),
        "payment_behaviour_analysis": ai_summary.get("payment_behaviour", ""),
        "relationship_insights": ai_summary.get("relationship_insights", ""),
        "communication_recommendations": ai_summary.get("communication_recommendations", []),
        "collection_difficulty_score": ai_summary.get("collection_difficulty", 0),
        "best_communication_tone": ai_summary.get("best_tone", "professional"),
        "personality_insights": ai_summary.get("personality_insights", ""),
        "follow_up_recommendations": ai_summary.get("follow_up_recommendations", []),
        "business_opportunity_suggestions": ai_summary.get("opportunities", []),
    }


# ---------------------------------------------------------------------------
# POST /{id}/risk-score  — AI risk scoring engine
# ---------------------------------------------------------------------------


@router.post("/{client_id}/risk-score", response_model=ClientRiskScore)
async def calculate_risk_score(
    client_id: UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    client = await _get_client_or_404(client_id, db, current_user)

    invoices = client.invoices or []
    total_invoiced = float(client.total_invoiced or 0)
    total_paid = float(client.total_paid or 0)
    avg_days = float(client.average_days_to_pay or 0)
    overdue_count = sum(1 for i in invoices if i.status == InvoiceStatus.overdue)
    overdue_amount = sum(float(i.balance_due or 0) for i in invoices if i.status == InvoiceStatus.overdue)

    # Payment consistency: stdev of payment days
    payment_days_list: list[float] = []
    for inv in invoices:
        if inv.paid_date and inv.issue_date:
            payment_days_list.append((inv.paid_date - inv.issue_date).days)

    consistency_score: float = 100.0
    if len(payment_days_list) > 1:
        mean = sum(payment_days_list) / len(payment_days_list)
        variance = sum((x - mean) ** 2 for x in payment_days_list) / len(payment_days_list)
        stdev = variance ** 0.5
        consistency_score = max(0.0, 100.0 - stdev * 2)

    # Revenue stability
    revenue_stability = (total_paid / total_invoiced * 100) if total_invoiced else 0.0

    # Trust score
    trust_score = min(
        100.0,
        (client.payment_behavior_score or 50) * 0.6
        + consistency_score * 0.2
        + revenue_stability * 0.2,
    )

    risk_data = await ai_service.calculate_risk(
        client_name=client.name,
        total_invoiced=total_invoiced,
        total_paid=total_paid,
        avg_days=avg_days,
        overdue_count=overdue_count,
        overdue_amount=overdue_amount,
        payment_consistency=consistency_score,
        revenue_stability=revenue_stability,
        trust_score=trust_score,
    )

    new_risk_score = risk_data.get("risk_score", client.risk_score or 0)

    # Persist updated risk score
    old_risk = client.risk_score
    client.risk_score = new_risk_score
    client.updated_at = _utcnow()
    await db.commit()

    # Real-time event if risk changed significantly
    if old_risk is not None and abs(new_risk_score - old_risk) >= 10:
        await ws_manager.broadcast_to_team(
            str(current_user.team_id),
            {
                "event": "risk_score_changed",
                "client_id": str(client.id),
                "old_score": old_risk,
                "new_score": new_risk_score,
            },
        )

    await _log_activity(
        db,
        team_id=current_user.team_id,
        user_id=current_user.id,
        action_type=ActivityType.risk_scored,
        entity_id=client.id,
        description=f"AI risk score updated for '{client.name}': {new_risk_score}",
        metadata={"old": old_risk, "new": new_risk_score},
    )
    await db.commit()

    return {
        "client_id": str(client.id),
        "risk_score": new_risk_score,
        "risk_level": _risk_level(new_risk_score),
        "late_payment_probability": risk_data.get("late_payment_probability", 0.0),
        "default_probability": risk_data.get("default_probability", 0.0),
        "invoice_dispute_probability": risk_data.get("dispute_probability", 0.0),
        "collection_difficulty": risk_data.get("collection_difficulty", 0.0),
        "payment_consistency": round(consistency_score, 1),
        "revenue_stability": round(revenue_stability, 1),
        "trust_score": round(trust_score, 1),
        "risk_reasons": risk_data.get("risk_reasons", []),
        "recommended_actions": risk_data.get("recommended_actions", []),
        "suggested_payment_terms": risk_data.get("payment_terms", "Net 30"),
        "suggested_reminder_frequency": risk_data.get("reminder_frequency", "weekly"),
        "calculated_at": _utcnow().isoformat(),
    }


def _risk_level(score: float) -> str:
    if score >= 80:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 40:
        return "medium"
    if score >= 20:
        return "low"
    return "minimal"


# ---------------------------------------------------------------------------
# GET /{id}/payment-behavior  — Advanced payment analytics
# ---------------------------------------------------------------------------


@router.get("/{client_id}/payment-behavior")
async def get_payment_behavior(
    client_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    client = await _get_client_or_404(client_id, db, current_user)

    invoices = client.invoices or []
    paid_invoices = [i for i in invoices if i.status == InvoiceStatus.paid and i.paid_date]
    overdue_invoices = [i for i in invoices if i.status == InvoiceStatus.overdue]

    payment_days: list[float] = []
    for inv in paid_invoices:
        delta = (inv.paid_date - inv.issue_date).days  # type: ignore[operator]
        payment_days.append(float(delta))

    avg_days = sum(payment_days) / len(payment_days) if payment_days else 0.0
    late_count = sum(1 for inv in paid_invoices if inv.paid_date > inv.due_date)  # type: ignore[operator]
    late_rate = (late_count / len(paid_invoices) * 100) if paid_invoices else 0.0

    largest_paid = max((float(i.total or 0) for i in paid_invoices), default=0.0)

    # Monthly payment consistency graph (last 12 months)
    from collections import defaultdict

    monthly_paid: dict[str, float] = defaultdict(float)
    monthly_overdue: dict[str, float] = defaultdict(float)

    for inv in paid_invoices:
        if inv.paid_date:
            key = inv.paid_date.strftime("%Y-%m")
            monthly_paid[key] += float(inv.total or 0)

    for inv in overdue_invoices:
        if inv.due_date:
            key = inv.due_date.strftime("%Y-%m")
            monthly_overdue[key] += float(inv.balance_due or 0)

    all_months = sorted(set(list(monthly_paid.keys()) + list(monthly_overdue.keys())))[-12:]
    consistency_graph = [
        {
            "month": m,
            "paid": monthly_paid.get(m, 0.0),
            "overdue": monthly_overdue.get(m, 0.0),
        }
        for m in all_months
    ]

    # Seasonal payment pattern (by month-of-year)
    month_buckets: dict[int, list[float]] = {}
    for inv in paid_invoices:
        if inv.paid_date:
            m = inv.paid_date.month
            month_buckets.setdefault(m, []).append(float((inv.paid_date - inv.issue_date).days))
    seasonal = {
        m: round(sum(v) / len(v), 1) for m, v in sorted(month_buckets.items())
    }

    # Reminder effectiveness
    reminders_sent = sum(i.reminders_sent or 0 for i in invoices)
    reminder_triggered_payments = sum(
        1
        for inv in paid_invoices
        if (inv.reminders_sent or 0) > 0
    )
    reminder_effectiveness = (
        reminder_triggered_payments / reminders_sent * 100 if reminders_sent else 0.0
    )

    # AI future payment prediction
    ai_prediction = await ai_service.predict_payment_behaviour(
        client_name=client.name,
        avg_days=avg_days,
        late_rate=late_rate,
        risk_score=client.risk_score or 0,
        total_invoiced=float(client.total_invoiced or 0),
        total_paid=float(client.total_paid or 0),
        reminders_sent=reminders_sent,
    )

    return {
        "client_id": str(client.id),
        "name": client.name,
        "avg_payment_days": round(avg_days, 1),
        "late_payment_frequency": round(late_rate, 1),
        "late_invoice_count": late_count,
        "largest_invoice_paid": largest_paid,
        "total_invoices": len(invoices),
        "total_paid_invoices": len(paid_invoices),
        "overdue_invoice_count": len(overdue_invoices),
        "outstanding_balance": float(sum(i.balance_due or 0 for i in overdue_invoices)),
        "payment_consistency_graph": consistency_graph,
        "seasonal_payment_behavior": seasonal,
        "collection_success_rate": round(100 - late_rate, 1),
        "reminder_effectiveness_pct": round(reminder_effectiveness, 1),
        "reminders_sent_total": reminders_sent,
        "ai_future_payment_prediction": ai_prediction.get("prediction", ""),
        "ai_predicted_days_to_pay": ai_prediction.get("predicted_days", avg_days),
        "ai_payment_reliability_score": ai_prediction.get("reliability_score", 50),
        "ai_recommended_action": ai_prediction.get("recommended_action", ""),
    }
