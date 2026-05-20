"""
app/routers/workflows.py

AI-Powered Workflow Automation Router for InvoiceFlow AI Platform.
Covers workflow CRUD, AI natural-language workflow builder, manual trigger,
execution history, prebuilt templates, visual node graph support,
autonomous business operations, and real-time WebSocket events.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import ActivityType, NotificationType
from app.core.permissions import require_permission
from app.database import get_db
from app.models import (
    Activity,
    BusinessInsight,
    Client,
    Invoice,
    InvoiceStatus,
    Notification,
    User,
    Workflow,
    WorkflowRun,
)
from app.schemas import (
    WorkflowCreate,
    WorkflowOut,
    WorkflowRunOut,
    WorkflowUpdate,
)
from app.services.ai_service import AIService
from app.services.analytics_service import AnalyticsService
from app.services.notification_service import NotificationService
from app.services.reminder_service import ReminderService
from app.services.workflow_service import WorkflowService
from app.websocket.manager import ws_manager
from auth import get_current_user

router = APIRouter(prefix="/workflows", tags=["Workflows"])

ai_service = AIService()
workflow_service = WorkflowService()
notification_service = NotificationService()
analytics_service = AnalyticsService()
reminder_service = ReminderService()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_TRIGGERS = [
    "invoice_created", "invoice_sent", "invoice_paid", "invoice_overdue",
    "client_created", "high_risk_client", "low_cashflow", "recurring_invoice_due",
    "payment_received", "failed_payment", "report_generated", "weekly_summary",
    "revenue_drop_detected", "unusual_spending_detected", "ai_insight_generated",
]

SUPPORTED_CONDITIONS = [
    "days_overdue", "invoice_amount", "client_risk_score", "payment_probability",
    "payment_delay_history", "invoice_status", "business_health_score",
    "revenue_threshold", "recurring_revenue_change", "invoice_priority", "ai_urgency_score",
]

SUPPORTED_ACTIONS = [
    "send_email", "generate_ai_reminder", "create_notification", "send_whatsapp",
    "escalate_invoice", "generate_report", "generate_insight", "assign_task",
    "create_followup", "update_invoice_status", "generate_thank_you",
    "trigger_workflow", "alert_finance_team", "schedule_future_workflow",
    "generate_ai_summary", "push_dashboard_alert",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _automation_score(workflow: Workflow) -> int:
    """Heuristic: score based on action count, active status, and success rate."""
    base = 60
    actions = workflow.actions or []
    conditions = workflow.conditions or {}
    base += min(len(actions) * 5, 20)
    if conditions:
        base += 10
    if workflow.is_active:
        base += 10
    return min(base, 100)


def _estimated_time_saved(workflow: Workflow, total_runs: int) -> str:
    """Rough estimate: each automated run saves ~15 minutes of manual work."""
    minutes = total_runs * 15
    if minutes < 60:
        return f"{minutes} mins/month"
    hours = round(minutes / 60, 1)
    return f"{hours} hours/month"


def _build_node_graph(workflow: Workflow) -> dict:
    """Convert trigger + conditions + actions into a frontend-renderable node graph."""
    nodes: list[dict] = []
    edges: list[dict] = []

    # Trigger node
    trigger_id = "node_trigger"
    nodes.append({
        "id": trigger_id,
        "type": "trigger",
        "label": (workflow.trigger_type or "trigger").replace("_", " ").title(),
        "data": {"trigger_type": workflow.trigger_type},
        "position": {"x": 0, "y": 0},
        "style": {"color": "#6366f1"},
    })

    # Condition node (if any)
    prev_id = trigger_id
    conditions = workflow.conditions or {}
    if conditions:
        cond_id = "node_conditions"
        nodes.append({
            "id": cond_id,
            "type": "condition",
            "label": "Conditions",
            "data": {"conditions": conditions},
            "position": {"x": 0, "y": 120},
            "style": {"color": "#f59e0b"},
        })
        edges.append({"id": f"e_{prev_id}_{cond_id}", "source": prev_id, "target": cond_id, "animated": True})
        prev_id = cond_id

    # Action nodes
    actions = workflow.actions or []
    for idx, action in enumerate(actions):
        action_id = f"node_action_{idx}"
        label = (action if isinstance(action, str) else action.get("type", "action")).replace("_", " ").title()
        nodes.append({
            "id": action_id,
            "type": "action",
            "label": label,
            "data": {"action": action},
            "position": {"x": 0, "y": 240 + idx * 120},
            "style": {"color": "#10b981"},
        })
        edges.append({
            "id": f"e_{prev_id}_{action_id}",
            "source": prev_id,
            "target": action_id,
            "animated": True,
        })
        prev_id = action_id

    return {"nodes": nodes, "edges": edges}


async def _get_workflow_or_404(
    workflow_id: UUID,
    db: AsyncSession,
    current_user: User,
) -> Workflow:
    stmt = select(Workflow).where(
        Workflow.id == workflow_id,
        Workflow.team_id == current_user.team_id,
        Workflow.is_active.isnot(None),  # includes both active and inactive
    )
    result = await db.execute(stmt)
    wf = result.scalar_one_or_none()
    if not wf:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found.")
    return wf


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
        entity_type="workflow",
        entity_id=entity_id,
        description=description,
        metadata=metadata or {},
        created_at=_utcnow(),
    ))


def _workflow_summary(wf: Workflow, total_runs: int, success_runs: int) -> dict:
    success_rate = round(success_runs / total_runs * 100, 1) if total_runs > 0 else 0.0
    failure_rate = round(100 - success_rate, 1)
    score = _automation_score(wf)
    return {
        "id": str(wf.id),
        "name": wf.name,
        "description": wf.description,
        "trigger_type": wf.trigger_type,
        "conditions": wf.conditions,
        "actions": wf.actions,
        "is_active": wf.is_active,
        "created_by": str(wf.created_by) if wf.created_by else None,
        "team_id": str(wf.team_id),
        "total_runs": total_runs,
        "success_runs": success_runs,
        "success_rate": success_rate,
        "failure_rate": failure_rate,
        "automation_score": score,
        "estimated_time_saved": _estimated_time_saved(wf, total_runs),
        "node_graph": _build_node_graph(wf),
        "ai_summary": f"This workflow triggers on '{wf.trigger_type}' and executes "
                      f"{len(wf.actions or [])} action(s). Success rate: {success_rate}%.",
    }


# ---------------------------------------------------------------------------
# GET /  — Paginated workflow listing
# ---------------------------------------------------------------------------


@router.get("/")
async def list_workflows(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, max_length=200),
    active: Optional[bool] = Query(None),
    trigger_type: Optional[str] = Query(None),
    ai_generated: Optional[bool] = Query(None),
    sort_by: str = Query("newest", regex="^(newest|most_used|highest_success_rate)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    offset = (page - 1) * page_size

    stmt = select(Workflow).where(Workflow.team_id == current_user.team_id)

    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            or_(Workflow.name.ilike(pattern), Workflow.description.ilike(pattern))
        )
    if active is not None:
        stmt = stmt.where(Workflow.is_active.is_(active))
    if trigger_type:
        stmt = stmt.where(Workflow.trigger_type == trigger_type)

    # Sort
    if sort_by == "newest":
        stmt = stmt.order_by(desc(Workflow.id))
    # most_used and highest_success_rate handled post-query (requires run counts)

    count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = int(count_result.scalar_one() or 0)

    stmt = stmt.offset(offset).limit(page_size)
    result = await db.execute(stmt)
    workflows = result.scalars().all()

    # Fetch run stats for all workflows in one query
    wf_ids = [wf.id for wf in workflows]
    run_stats: dict[str, dict] = {}
    if wf_ids:
        runs_stmt = select(
            WorkflowRun.workflow_id,
            func.count(WorkflowRun.id).label("total"),
            func.sum((WorkflowRun.status == "completed").cast(int)).label("success"),
        ).where(WorkflowRun.workflow_id.in_(wf_ids)).group_by(WorkflowRun.workflow_id)
        for row in (await db.execute(runs_stmt)).all():
            run_stats[str(row[0])] = {"total": int(row[1] or 0), "success": int(row[2] or 0)}

    items = []
    for wf in workflows:
        stats = run_stats.get(str(wf.id), {"total": 0, "success": 0})
        item = _workflow_summary(wf, stats["total"], stats["success"])
        items.append(item)

    # Sort post-query for derived metrics
    if sort_by == "most_used":
        items.sort(key=lambda x: x["total_runs"], reverse=True)
    elif sort_by == "highest_success_rate":
        items.sort(key=lambda x: x["success_rate"], reverse=True)

    # Team-level analytics summary
    total_runs = sum(s["total_runs"] for s in items)
    success_runs = sum(s["success_runs"] for s in items)
    team_success_rate = round(success_runs / total_runs * 100, 1) if total_runs > 0 else 0.0

    # AI workflow recommendations
    ai_recs = await ai_service.get_workflow_recommendations(
        team_id=str(current_user.team_id),
        active_workflow_count=sum(1 for wf in workflows if wf.is_active),
        trigger_types=[wf.trigger_type for wf in workflows],
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": -(-total // page_size),
        "analytics_summary": {
            "total_workflows": total,
            "active_workflows": sum(1 for wf in workflows if wf.is_active),
            "total_runs": total_runs,
            "team_success_rate_pct": team_success_rate,
            "team_failure_rate_pct": round(100 - team_success_rate, 1),
            "estimated_hours_saved": round(total_runs * 0.25, 1),
        },
        "ai_recommendations": ai_recs.get("recommendations", []),
    }


# ---------------------------------------------------------------------------
# POST /  — Create workflow (manual or AI-generated)
# ---------------------------------------------------------------------------


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_workflow(
    payload: WorkflowCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    require_permission(current_user, "workflows:create")

    trigger_type = payload.trigger_type
    conditions = payload.conditions or {}
    actions = payload.actions or []
    description = payload.description or ""

    # -----------------------------------------------------------------------
    # AI WORKFLOW BUILDER — user types plain English, AI builds the config
    # -----------------------------------------------------------------------
    ai_generated = False
    ai_build_explanation: str = ""
    if payload.natural_language_prompt:
        built = await ai_service.build_workflow_from_text(
            prompt=payload.natural_language_prompt,
            supported_triggers=SUPPORTED_TRIGGERS,
            supported_conditions=SUPPORTED_CONDITIONS,
            supported_actions=SUPPORTED_ACTIONS,
            team_context={
                "business_name": current_user.business_name,
                "role": str(current_user.role),
            },
        )
        trigger_type = built.get("trigger_type", trigger_type)
        conditions = built.get("conditions", conditions)
        actions = built.get("actions", actions)
        description = built.get("description", description)
        ai_build_explanation = built.get("explanation", "")
        ai_generated = True

    # Validate trigger
    if trigger_type not in SUPPORTED_TRIGGERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported trigger '{trigger_type}'. Supported: {SUPPORTED_TRIGGERS}",
        )

    workflow = Workflow(
        name=payload.name or f"Workflow — {trigger_type.replace('_', ' ').title()}",
        description=description,
        trigger_type=trigger_type,
        conditions=conditions,
        actions=actions,
        team_id=current_user.team_id,
        is_active=True,
        created_by=current_user.id,
    )
    # Extended fields
    if hasattr(workflow, "is_ai_generated"):
        workflow.is_ai_generated = ai_generated
    if hasattr(workflow, "ai_explanation"):
        workflow.ai_explanation = ai_build_explanation
    if hasattr(workflow, "version"):
        workflow.version = 1

    db.add(workflow)
    await db.flush()

    await _log_activity(
        db,
        team_id=current_user.team_id,
        user_id=current_user.id,
        action_type=ActivityType.created,
        entity_id=workflow.id,
        description=f"Workflow '{workflow.name}' created" + (" via AI" if ai_generated else ""),
        metadata={"trigger_type": trigger_type, "ai_generated": ai_generated},
    )
    await db.commit()
    await db.refresh(workflow)

    # AI: generate optimization suggestions in background
    if ai_generated:
        background_tasks.add_task(
            _post_create_ai_analysis, workflow_id=workflow.id, team_id=current_user.team_id
        )

    await ws_manager.broadcast_to_team(
        str(current_user.team_id),
        {
            "event": "AI_WORKFLOW_GENERATED" if ai_generated else "WORKFLOW_CREATED",
            "workflow_id": str(workflow.id),
            "name": workflow.name,
            "trigger_type": trigger_type,
        },
    )

    return {
        **_workflow_summary(workflow, 0, 0),
        "ai_generated": ai_generated,
        "ai_build_explanation": ai_build_explanation,
        "ai_optimization_suggestions": await ai_service.get_workflow_optimization_tips(
            trigger_type=trigger_type,
            actions=actions,
            conditions=conditions,
        ) if not ai_generated else [],
    }


async def _post_create_ai_analysis(workflow_id: UUID, team_id: UUID) -> None:
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
        wf = result.scalar_one_or_none()
        if not wf:
            return

        tips = await ai_service.get_workflow_optimization_tips(
            trigger_type=wf.trigger_type,
            actions=wf.actions or [],
            conditions=wf.conditions or {},
        )
        if hasattr(wf, "ai_metadata"):
            wf.ai_metadata = {"optimization_tips": tips}
            await db.commit()

        insight = BusinessInsight(
            team_id=team_id,
            type="workflow_created",
            title=f"New AI Workflow: {wf.name}",
            content=f"Workflow '{wf.name}' was auto-generated. It will trigger on '{wf.trigger_type}' "
                    f"and run {len(wf.actions or [])} action(s).",
            severity="info",
            category="operations",
            is_read=False,
            ai_generated=True,
            metadata={"workflow_id": str(workflow_id)},
        )
        db.add(insight)
        await db.commit()


# ---------------------------------------------------------------------------
# GET /templates/list  — Prebuilt workflow templates
# ---------------------------------------------------------------------------


@router.get("/templates/list")
async def list_workflow_templates() -> dict:
    templates = [
        # --- Invoice Collections ---
        {
            "id": "overdue_recovery",
            "category": "Invoice Collections",
            "name": "Overdue Invoice Recovery",
            "description": "Automatically send escalating reminders for overdue invoices until paid.",
            "trigger_type": "invoice_overdue",
            "conditions": {"days_overdue": {"gte": 3}},
            "actions": [
                "generate_ai_reminder",
                "send_email",
                {"type": "schedule_future_workflow", "delay_days": 7, "action": "escalate_invoice"},
            ],
            "estimated_time_saved": "8 hours/month",
            "automation_score": 95,
            "use_case": "Reduces manual follow-up time and improves collection rate.",
            "demo_flow": [
                "Invoice overdue detected",
                "AI selects optimal reminder tone",
                "Email sent to client",
                "Follow-up scheduled in 7 days",
                "Dashboard updated live",
            ],
        },
        {
            "id": "smart_followups",
            "category": "Invoice Collections",
            "name": "Smart Multi-Channel Followup",
            "description": "Send email first, then WhatsApp if no payment after 5 days.",
            "trigger_type": "invoice_overdue",
            "conditions": {"days_overdue": {"gte": 7}},
            "actions": ["send_email", {"type": "schedule_future_workflow", "delay_days": 5, "action": "send_whatsapp"}, "escalate_invoice"],
            "estimated_time_saved": "10 hours/month",
            "automation_score": 91,
            "use_case": "Multi-channel approach increases payment probability by ~40%.",
            "demo_flow": [
                "Invoice 7+ days overdue",
                "Email reminder sent",
                "5 day timer starts",
                "WhatsApp follow-up if unpaid",
                "Invoice escalated to urgent",
            ],
        },
        {
            "id": "thank_you_sequence",
            "category": "Invoice Collections",
            "name": "Automatic Thank-You Sequence",
            "description": "Send a personalized thank-you email when payment is received.",
            "trigger_type": "payment_received",
            "conditions": {},
            "actions": ["generate_thank_you", "send_email", "create_notification", "generate_insight"],
            "estimated_time_saved": "3 hours/month",
            "automation_score": 88,
            "use_case": "Improves client retention and relationship quality.",
            "demo_flow": [
                "Payment detected",
                "AI generates personalized thank-you",
                "Email sent to client",
                "Team notified",
                "AI insight card created",
            ],
        },
        # --- AI Finance Assistant ---
        {
            "id": "low_cashflow_alert",
            "category": "AI Finance Assistant",
            "name": "Low Cash Flow Detection",
            "description": "Alert the team when predicted cash flow drops below a threshold.",
            "trigger_type": "low_cashflow",
            "conditions": {"cashflow_threshold": {"lte": 5000}},
            "actions": ["alert_finance_team", "generate_ai_summary", "push_dashboard_alert", "generate_insight"],
            "estimated_time_saved": "5 hours/month",
            "automation_score": 93,
            "use_case": "Prevents cash flow crisis with early warning system.",
            "demo_flow": [
                "Cash flow below threshold detected",
                "AI generates financial summary",
                "Finance team alerted",
                "Dashboard alert pushed",
                "AI insight card created",
            ],
        },
        {
            "id": "revenue_drop_alert",
            "category": "AI Finance Assistant",
            "name": "Revenue Drop Alert",
            "description": "Detect and alert on significant revenue drops compared to previous period.",
            "trigger_type": "revenue_drop_detected",
            "conditions": {"drop_percentage": {"gte": 20}},
            "actions": ["generate_ai_summary", "alert_finance_team", "push_dashboard_alert", "generate_report"],
            "estimated_time_saved": "4 hours/month",
            "automation_score": 89,
            "use_case": "Early warning system for revenue issues.",
            "demo_flow": [
                "Revenue drop detected (20%+)",
                "AI generates analysis",
                "Finance team notified",
                "Report generated",
                "Dashboard alert pushed",
            ],
        },
        # --- AI Risk Management ---
        {
            "id": "high_risk_escalation",
            "category": "AI Risk Management",
            "name": "High-Risk Client Escalation",
            "description": "When a client's risk score exceeds 70, escalate all their invoices and alert the team.",
            "trigger_type": "high_risk_client",
            "conditions": {"client_risk_score": {"gte": 70}},
            "actions": ["escalate_invoice", "generate_ai_reminder", "alert_finance_team", "create_notification", "generate_insight"],
            "estimated_time_saved": "6 hours/month",
            "automation_score": 97,
            "use_case": "Protects revenue from high-risk clients automatically.",
            "demo_flow": [
                "Client risk score exceeds 70",
                "All invoices escalated",
                "AI reminder generated",
                "Finance team alerted",
                "AI risk insight card created",
            ],
        },
        {
            "id": "payment_failure_recovery",
            "category": "AI Risk Management",
            "name": "Failed Payment Recovery",
            "description": "When a payment fails, retry and notify both client and team.",
            "trigger_type": "failed_payment",
            "conditions": {},
            "actions": ["create_notification", "generate_ai_reminder", "send_email", "alert_finance_team"],
            "estimated_time_saved": "4 hours/month",
            "automation_score": 90,
            "use_case": "Recover failed payments without manual intervention.",
            "demo_flow": [
                "Payment failure detected",
                "Client notified via email",
                "AI recovery reminder generated",
                "Finance team alerted",
            ],
        },
        # --- Recurring Revenue ---
        {
            "id": "subscription_renewal",
            "category": "Recurring Revenue",
            "name": "Subscription Renewal Workflow",
            "description": "Automatically process recurring invoices and notify clients before billing.",
            "trigger_type": "recurring_invoice_due",
            "conditions": {"days_before_due": {"lte": 3}},
            "actions": ["send_email", "create_notification", "generate_ai_summary"],
            "estimated_time_saved": "7 hours/month",
            "automation_score": 92,
            "use_case": "Reduces subscription churn by pre-notifying clients.",
            "demo_flow": [
                "Recurring invoice due in 3 days",
                "Client pre-notification sent",
                "Team notified",
                "AI summary generated",
            ],
        },
        # --- Revenue Intelligence ---
        {
            "id": "weekly_intelligence",
            "category": "Revenue Intelligence",
            "name": "AI Weekly Business Intelligence",
            "description": "Every week, generate an AI summary of revenue, risks, and opportunities.",
            "trigger_type": "weekly_summary",
            "conditions": {},
            "actions": ["generate_ai_summary", "generate_report", "push_dashboard_alert", "generate_insight"],
            "estimated_time_saved": "3 hours/month",
            "automation_score": 85,
            "use_case": "Keeps the team informed with zero manual reporting effort.",
            "demo_flow": [
                "Weekly trigger fires",
                "AI analyzes all business data",
                "Executive summary generated",
                "Report created",
                "Dashboard refreshed",
            ],
        },
    ]

    return {
        "templates": templates,
        "total": len(templates),
        "categories": list({t["category"] for t in templates}),
        "supported_triggers": SUPPORTED_TRIGGERS,
        "supported_conditions": SUPPORTED_CONDITIONS,
        "supported_actions": SUPPORTED_ACTIONS,
    }


# ---------------------------------------------------------------------------
# GET /{id}  — Workflow detail with node graph
# ---------------------------------------------------------------------------


@router.get("/{workflow_id}")
async def get_workflow(
    workflow_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    wf = await _get_workflow_or_404(workflow_id, db, current_user)

    # Run stats
    run_stats_stmt = select(
        func.count(WorkflowRun.id).label("total"),
        func.sum((WorkflowRun.status == "completed").cast(int)).label("success"),
        func.avg(
            func.extract("epoch", WorkflowRun.completed_at) -
            func.extract("epoch", WorkflowRun.started_at)
        ).label("avg_duration_sec"),
    ).where(WorkflowRun.workflow_id == workflow_id)
    run_stats = (await db.execute(run_stats_stmt)).mappings().one()

    total_runs = int(run_stats["total"] or 0)
    success_runs = int(run_stats["success"] or 0)
    avg_duration = float(run_stats["avg_duration_sec"] or 0)

    # Last execution
    last_run_stmt = (
        select(WorkflowRun)
        .where(WorkflowRun.workflow_id == workflow_id)
        .order_by(desc(WorkflowRun.started_at))
        .limit(1)
    )
    last_run_result = await db.execute(last_run_stmt)
    last_run = last_run_result.scalar_one_or_none()

    # AI-generated explanation and optimization suggestions
    ai_explanation = await ai_service.explain_workflow(
        name=wf.name,
        trigger_type=wf.trigger_type,
        conditions=wf.conditions or {},
        actions=wf.actions or [],
    )
    optimization_suggestions = await ai_service.get_workflow_optimization_tips(
        trigger_type=wf.trigger_type,
        actions=wf.actions or [],
        conditions=wf.conditions or {},
        success_rate=round(success_runs / total_runs * 100, 1) if total_runs > 0 else 0.0,
    )

    # Next execution prediction
    next_execution = None
    if wf.is_active and wf.trigger_type in ("weekly_summary", "recurring_invoice_due"):
        next_execution = (_utcnow() + timedelta(days=7)).isoformat()

    return {
        **_workflow_summary(wf, total_runs, success_runs),
        "avg_execution_duration_sec": round(avg_duration, 2),
        "last_execution": {
            "id": str(last_run.id),
            "status": last_run.status,
            "started_at": last_run.started_at.isoformat() if last_run.started_at else None,
            "completed_at": last_run.completed_at.isoformat() if last_run.completed_at else None,
            "triggered_by": last_run.triggered_by,
            "log": last_run.log,
        } if last_run else None,
        "next_execution_prediction": next_execution,
        "ai_explanation": ai_explanation.get("explanation", ""),
        "recommended_improvements": optimization_suggestions,
        "performance_metrics": {
            "total_runs": total_runs,
            "success_runs": success_runs,
            "failure_runs": total_runs - success_runs,
            "success_rate_pct": round(success_runs / total_runs * 100, 1) if total_runs > 0 else 0.0,
            "avg_duration_sec": round(avg_duration, 2),
            "estimated_hours_saved": round(total_runs * 0.25, 1),
        },
    }


# ---------------------------------------------------------------------------
# PUT /{id}  — Update workflow
# ---------------------------------------------------------------------------


@router.put("/{workflow_id}")
async def update_workflow(
    workflow_id: UUID,
    payload: WorkflowUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    require_permission(current_user, "workflows:update")
    wf = await _get_workflow_or_404(workflow_id, db, current_user)

    update_data = payload.model_dump(exclude_unset=True)

    # Validate trigger if being changed
    new_trigger = update_data.get("trigger_type", wf.trigger_type)
    if new_trigger not in SUPPORTED_TRIGGERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported trigger '{new_trigger}'.",
        )

    # Version bump
    if hasattr(wf, "version") and wf.version is not None:
        update_data["version"] = wf.version + 1

    changed_fields = list(update_data.keys())
    for field, value in update_data.items():
        setattr(wf, field, value)

    # AI optimization suggestions on update
    optimization_suggestions = await ai_service.get_workflow_optimization_tips(
        trigger_type=wf.trigger_type,
        actions=wf.actions or [],
        conditions=wf.conditions or {},
    )

    await _log_activity(
        db,
        team_id=current_user.team_id,
        user_id=current_user.id,
        action_type=ActivityType.updated,
        entity_id=wf.id,
        description=f"Workflow '{wf.name}' updated",
        metadata={"changed_fields": changed_fields},
    )
    await db.commit()
    await db.refresh(wf)

    await ws_manager.broadcast_to_team(
        str(current_user.team_id),
        {"event": "WORKFLOW_UPDATED", "workflow_id": str(wf.id), "name": wf.name},
    )

    return {
        **_workflow_summary(wf, 0, 0),
        "ai_optimization_suggestions": optimization_suggestions,
    }


# ---------------------------------------------------------------------------
# DELETE /{id}  — Soft delete / archive
# ---------------------------------------------------------------------------


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: UUID,
    reason: Optional[str] = Query(None, max_length=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    require_permission(current_user, "workflows:delete")
    wf = await _get_workflow_or_404(workflow_id, db, current_user)

    # Soft delete: disable instead of hard-delete
    wf.is_active = False
    if hasattr(wf, "deleted_at"):
        wf.deleted_at = _utcnow()
    if hasattr(wf, "delete_reason"):
        wf.delete_reason = reason

    await _log_activity(
        db,
        team_id=current_user.team_id,
        user_id=current_user.id,
        action_type=ActivityType.deleted,
        entity_id=wf.id,
        description=f"Workflow '{wf.name}' archived",
        metadata={"reason": reason},
    )
    await db.commit()


# ---------------------------------------------------------------------------
# POST /{id}/run  — Manual trigger (with dry-run mode)
# ---------------------------------------------------------------------------


@router.post("/{workflow_id}/run")
async def run_workflow(
    workflow_id: UUID,
    dry_run: bool = Query(False),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    require_permission(current_user, "workflows:trigger")
    wf = await _get_workflow_or_404(workflow_id, db, current_user)

    if not wf.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot run an inactive workflow. Enable it first.",
        )

    if dry_run:
        # Simulated execution — no side effects, return preview
        simulation = await workflow_service.simulate_run(
            trigger_type=wf.trigger_type,
            conditions=wf.conditions or {},
            actions=wf.actions or [],
            team_id=current_user.team_id,
        )
        return {
            "mode": "dry_run",
            "workflow_id": str(wf.id),
            "name": wf.name,
            "simulated_outcome": simulation.get("outcome", {}),
            "estimated_entities_affected": simulation.get("entities_affected", 0),
            "estimated_emails_sent": simulation.get("emails_sent", 0),
            "estimated_notifications": simulation.get("notifications", 0),
            "node_execution_preview": simulation.get("node_preview", []),
            "warnings": simulation.get("warnings", []),
        }

    # Real execution — create a run record and execute in background
    run = WorkflowRun(
        id=uuid4(),
        workflow_id=wf.id,
        status="running",
        started_at=_utcnow(),
        triggered_by=str(current_user.id),
        log=[],
    )
    db.add(run)
    await db.flush()
    run_id = run.id
    await db.commit()

    await ws_manager.broadcast_to_team(
        str(current_user.team_id),
        {"event": "WORKFLOW_STARTED", "workflow_id": str(wf.id), "run_id": str(run_id), "name": wf.name},
    )

    background_tasks.add_task(
        _execute_workflow_bg,
        workflow_id=wf.id,
        run_id=run_id,
        team_id=current_user.team_id,
        triggered_by=str(current_user.id),
    )

    return {
        "mode": "live",
        "workflow_id": str(wf.id),
        "run_id": str(run_id),
        "name": wf.name,
        "status": "running",
        "started_at": _utcnow().isoformat(),
        "message": "Workflow is running. Watch for live events via WebSocket.",
    }


async def _execute_workflow_bg(
    workflow_id: UUID,
    run_id: UUID,
    team_id: UUID,
    triggered_by: str,
) -> None:
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        wf_result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
        wf = wf_result.scalar_one_or_none()
        if not wf:
            return

        log: list[dict] = []
        success = True
        retry_count = 0
        max_retries = 2

        try:
            execution_result = await workflow_service.execute(
                trigger_type=wf.trigger_type,
                conditions=wf.conditions or {},
                actions=wf.actions or [],
                team_id=team_id,
                log=log,
            )
            log.append({"step": "completed", "result": execution_result, "at": _utcnow().isoformat()})
        except Exception as exc:
            success = False
            error_msg = str(exc)
            log.append({"step": "error", "error": error_msg, "at": _utcnow().isoformat()})

            # Auto-retry
            while retry_count < max_retries:
                retry_count += 1
                await ws_manager.broadcast_to_team(
                    str(team_id),
                    {"event": "WORKFLOW_RETRY", "workflow_id": str(workflow_id), "attempt": retry_count},
                )
                try:
                    await workflow_service.execute(
                        trigger_type=wf.trigger_type,
                        conditions=wf.conditions or {},
                        actions=wf.actions or [],
                        team_id=team_id,
                        log=log,
                    )
                    success = True
                    log.append({"step": "retry_success", "attempt": retry_count, "at": _utcnow().isoformat()})
                    break
                except Exception as retry_exc:
                    log.append({"step": "retry_failed", "attempt": retry_count, "error": str(retry_exc)})

            if not success:
                # Notify admin of permanent failure
                notif = Notification(
                    user_id=UUID(triggered_by),
                    type=NotificationType.workflow_failed,
                    title=f"Workflow Failed: {wf.name}",
                    message=f"Workflow '{wf.name}' failed after {max_retries} retries. Error: {error_msg}",
                    read=False,
                    created_at=_utcnow(),
                )
                db.add(notif)

        # Update run record
        await db.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == run_id)
            .values(
                status="completed" if success else "failed",
                completed_at=_utcnow(),
                log=log,
            )
        )
        await db.commit()

        event = "WORKFLOW_COMPLETED" if success else "WORKFLOW_FAILED"
        await ws_manager.broadcast_to_team(
            str(team_id),
            {
                "event": event,
                "workflow_id": str(workflow_id),
                "run_id": str(run_id),
                "name": wf.name,
                "success": success,
                "retries": retry_count,
            },
        )


# ---------------------------------------------------------------------------
# GET /{id}/runs  — Execution history
# ---------------------------------------------------------------------------


@router.get("/{workflow_id}/runs")
async def get_workflow_runs(
    workflow_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    wf = await _get_workflow_or_404(workflow_id, db, current_user)
    offset = (page - 1) * page_size

    count_stmt = select(func.count(WorkflowRun.id)).where(WorkflowRun.workflow_id == workflow_id)
    total = int((await db.execute(count_stmt)).scalar_one() or 0)

    runs_stmt = (
        select(WorkflowRun)
        .where(WorkflowRun.workflow_id == workflow_id)
        .order_by(desc(WorkflowRun.started_at))
        .offset(offset)
        .limit(page_size)
    )
    runs = (await db.execute(runs_stmt)).scalars().all()

    # Performance analytics
    perf_stmt = select(
        func.count(WorkflowRun.id).label("total"),
        func.sum((WorkflowRun.status == "completed").cast(int)).label("success"),
        func.avg(
            func.extract("epoch", WorkflowRun.completed_at) -
            func.extract("epoch", WorkflowRun.started_at)
        ).label("avg_duration"),
    ).where(WorkflowRun.workflow_id == workflow_id)
    perf = (await db.execute(perf_stmt)).mappings().one()
    total_all = int(perf["total"] or 0)
    success_all = int(perf["success"] or 0)

    return {
        "workflow_id": str(workflow_id),
        "workflow_name": wf.name,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": -(-total // page_size),
        "runs": [
            {
                "id": str(r.id),
                "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "duration_sec": round(
                    (r.completed_at - r.started_at).total_seconds(), 2
                ) if r.completed_at and r.started_at else None,
                "triggered_by": r.triggered_by,
                "log": r.log or [],
            }
            for r in runs
        ],
        "performance_analytics": {
            "total_runs": total_all,
            "success_runs": success_all,
            "failure_runs": total_all - success_all,
            "success_rate_pct": round(success_all / total_all * 100, 1) if total_all > 0 else 0.0,
            "avg_duration_sec": round(float(perf["avg_duration"] or 0), 2),
            "estimated_hours_saved": round(total_all * 0.25, 1),
        },
    }
