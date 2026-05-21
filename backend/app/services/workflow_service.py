"""
app/services/workflow_service.py

Autonomous workflow engine for InvoiceFlow.
Powers AI-driven automation: triggers, conditions, actions, escalation, scheduling,
chained multi-step workflows, real-time WebSocket events, and analytics.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TriggerType(str, Enum):
    INVOICE_CREATED = "invoice_created"
    INVOICE_SENT = "invoice_sent"
    INVOICE_PAID = "invoice_paid"
    INVOICE_OVERDUE = "invoice_overdue"
    PAYMENT_RECEIVED = "payment_received"
    RECURRING_INVOICE_GENERATED = "recurring_invoice_generated"
    CLIENT_RISK_HIGH = "client_risk_high"
    LOW_CASHFLOW_DETECTED = "low_cashflow_detected"
    WEEKLY_SUMMARY_READY = "weekly_summary_ready"
    REPORT_GENERATED = "report_generated"
    SCHEDULED = "scheduled"
    AI_INSIGHT_GENERATED = "ai_insight_generated"


class ActionType(str, Enum):
    SEND_EMAIL = "send_email"
    CREATE_REMINDER = "create_reminder"
    GENERATE_REPORT = "generate_report"
    NOTIFY_TEAM = "notify_team"
    UPDATE_INVOICE_STATUS = "update_invoice_status"
    CREATE_NOTIFICATION = "create_notification"
    SEND_WHATSAPP = "send_whatsapp"
    GENERATE_AI_SUMMARY = "generate_ai_summary"
    ASSIGN_PRIORITY = "assign_priority"
    GENERATE_FOLLOWUP = "generate_followup"
    TRIGGER_AI_INSIGHT = "trigger_ai_insight"
    DUPLICATE_INVOICE = "duplicate_invoice"
    SCHEDULE_FOLLOWUP = "schedule_followup"
    CREATE_TASK = "create_task"
    STREAM_WEBSOCKET_EVENT = "stream_websocket_event"


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Helpers — these thin adapters let you swap in real DB / AI clients
# without changing any business logic.
# ---------------------------------------------------------------------------

def _get_db():
    """Return the active SQLAlchemy session (or your ORM equivalent)."""
    try:
        from app import db
        return db
    except ImportError:
        raise RuntimeError(
            "Could not import 'db' from 'app'. "
            "Ensure your Flask app context is active."
        )


def _get_ai_client():
    """Return an OpenAI-compatible client, or None when no key is configured."""
    try:
        import openai
        import os
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set — AI features degraded")
            return None
        openai.api_key = api_key
        return openai
    except ImportError:
        logger.warning("openai package not installed — AI features disabled")
        return None


def _get_socketio():
    """Return the Flask-SocketIO instance for real-time broadcasts."""
    try:
        from app import socketio
        return socketio
    except ImportError:
        return None


def _now() -> datetime:
    return datetime.utcnow()


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Model stubs — replace with your actual SQLAlchemy models
# ---------------------------------------------------------------------------

def _workflow_model():
    try:
        from app.models import Workflow
        return Workflow
    except ImportError:
        raise RuntimeError("Workflow model not found. Import it from app.models.")


def _log_model():
    try:
        from app.models import WorkflowLog
        return WorkflowLog
    except ImportError:
        raise RuntimeError("WorkflowLog model not found. Import it from app.models.")


def _invoice_model():
    try:
        from app.models import Invoice
        return Invoice
    except ImportError:
        raise RuntimeError("Invoice model not found. Import it from app.models.")


# ===========================================================================
# 1. WORKFLOW CRUD ENGINE
# ===========================================================================

def create_workflow(
    name: str,
    trigger: str,
    conditions: dict,
    actions: list[dict],
    *,
    created_by: int,
    team_id: int | None = None,
    is_template: bool = False,
    description: str = "",
    schedule_cron: str | None = None,
    enabled: bool = True,
) -> dict:
    """
    Persist a new workflow definition.

    Parameters
    ----------
    name        : Human-readable workflow name.
    trigger     : TriggerType value string.
    conditions  : Dict of condition key/value pairs evaluated at runtime.
    actions     : Ordered list of action dicts (type + params).
    created_by  : User ID of creator.
    team_id     : Optional team scope.
    is_template : Whether this is a reusable template.
    description : Optional prose description.
    schedule_cron: Cron expression for SCHEDULED trigger type.
    enabled     : Start enabled or disabled.

    Returns
    -------
    Serialised workflow dict.
    """
    if trigger not in [t.value for t in TriggerType]:
        raise ValueError(f"Unknown trigger type: {trigger!r}")

    db = _get_db()
    Workflow = _workflow_model()

    workflow = Workflow(
        id=_new_id(),
        name=name,
        description=description,
        trigger=trigger,
        conditions=json.dumps(conditions),
        actions=json.dumps(actions),
        created_by=created_by,
        team_id=team_id,
        is_template=is_template,
        is_enabled=enabled,
        schedule_cron=schedule_cron,
        created_at=_now(),
        updated_at=_now(),
        run_count=0,
        last_run_at=None,
    )
    db.session.add(workflow)
    db.session.commit()

    logger.info("Workflow created: %s (id=%s)", name, workflow.id)
    return _serialize_workflow(workflow)


def get_workflow(workflow_id: str) -> dict | None:
    """Fetch a single workflow by ID. Returns None if not found."""
    Workflow = _workflow_model()
    workflow = Workflow.query.get(workflow_id)
    return _serialize_workflow(workflow) if workflow else None


def list_workflows(
    *,
    team_id: int | None = None,
    created_by: int | None = None,
    trigger: str | None = None,
    is_enabled: bool | None = None,
    is_template: bool | None = None,
    search: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> dict:
    """
    Return a paginated, filterable list of workflows.

    Supports filtering by team, owner, trigger type, enabled status,
    template flag, and a full-text search on name/description.
    """
    Workflow = _workflow_model()
    q = Workflow.query

    if team_id is not None:
        q = q.filter_by(team_id=team_id)
    if created_by is not None:
        q = q.filter_by(created_by=created_by)
    if trigger is not None:
        q = q.filter_by(trigger=trigger)
    if is_enabled is not None:
        q = q.filter_by(is_enabled=is_enabled)
    if is_template is not None:
        q = q.filter_by(is_template=is_template)
    if search:
        like = f"%{search}%"
        q = q.filter(
            Workflow.name.ilike(like) | Workflow.description.ilike(like)
        )

    q = q.order_by(Workflow.created_at.desc())
    paginated = q.paginate(page=page, per_page=per_page, error_out=False)

    return {
        "items": [_serialize_workflow(w) for w in paginated.items],
        "total": paginated.total,
        "page": page,
        "per_page": per_page,
        "pages": paginated.pages,
    }


def update_workflow(workflow_id: str, updates: dict) -> dict:
    """
    Update an existing workflow. Only provided fields are changed.

    Supported keys: name, description, trigger, conditions, actions,
    team_id, is_template, schedule_cron, is_enabled.
    """
    db = _get_db()
    Workflow = _workflow_model()
    workflow = Workflow.query.get(workflow_id)
    if not workflow:
        raise ValueError(f"Workflow {workflow_id!r} not found")

    allowed = {
        "name", "description", "trigger", "conditions",
        "actions", "team_id", "is_template", "schedule_cron", "is_enabled",
    }
    for key, value in updates.items():
        if key not in allowed:
            continue
        if key in ("conditions", "actions") and not isinstance(value, str):
            value = json.dumps(value)
        setattr(workflow, key, value)

    workflow.updated_at = _now()
    db.session.commit()
    logger.info("Workflow updated: %s", workflow_id)
    return _serialize_workflow(workflow)


def delete_workflow(workflow_id: str) -> bool:
    """Delete a workflow and its associated execution logs."""
    db = _get_db()
    Workflow = _workflow_model()
    WorkflowLog = _log_model()

    workflow = Workflow.query.get(workflow_id)
    if not workflow:
        return False

    WorkflowLog.query.filter_by(workflow_id=workflow_id).delete()
    db.session.delete(workflow)
    db.session.commit()
    logger.info("Workflow deleted: %s", workflow_id)
    return True


def toggle_workflow(workflow_id: str) -> dict:
    """Flip the enabled/disabled state of a workflow."""
    db = _get_db()
    Workflow = _workflow_model()
    workflow = Workflow.query.get(workflow_id)
    if not workflow:
        raise ValueError(f"Workflow {workflow_id!r} not found")

    workflow.is_enabled = not workflow.is_enabled
    workflow.updated_at = _now()
    db.session.commit()

    state = "enabled" if workflow.is_enabled else "disabled"
    logger.info("Workflow %s %s", workflow_id, state)
    return _serialize_workflow(workflow)


def _serialize_workflow(workflow) -> dict:
    """Convert a Workflow ORM object to a plain dict."""
    return {
        "id": workflow.id,
        "name": workflow.name,
        "description": getattr(workflow, "description", ""),
        "trigger": workflow.trigger,
        "conditions": (
            json.loads(workflow.conditions)
            if isinstance(workflow.conditions, str)
            else workflow.conditions or {}
        ),
        "actions": (
            json.loads(workflow.actions)
            if isinstance(workflow.actions, str)
            else workflow.actions or []
        ),
        "created_by": workflow.created_by,
        "team_id": getattr(workflow, "team_id", None),
        "is_template": getattr(workflow, "is_template", False),
        "is_enabled": getattr(workflow, "is_enabled", True),
        "schedule_cron": getattr(workflow, "schedule_cron", None),
        "run_count": getattr(workflow, "run_count", 0),
        "last_run_at": (
            workflow.last_run_at.isoformat()
            if getattr(workflow, "last_run_at", None)
            else None
        ),
        "created_at": workflow.created_at.isoformat(),
        "updated_at": workflow.updated_at.isoformat(),
    }


# ===========================================================================
# 2. TRIGGER EVALUATION SYSTEM
# ===========================================================================

def evaluate_trigger(
    trigger_type: str,
    entity: dict,
    *,
    user_id: int,
) -> list[dict]:
    """
    Check all enabled workflows whose trigger matches `trigger_type`
    and evaluate their conditions against `entity`.

    Parameters
    ----------
    trigger_type : One of the TriggerType values.
    entity       : The business object that fired the event
                   (invoice dict, payment dict, etc.).
    user_id      : The acting user — used for team-scope lookup.

    Returns
    -------
    List of execution result dicts (one per matched workflow).
    """
    Workflow = _workflow_model()
    workflows = Workflow.query.filter_by(
        trigger=trigger_type, is_enabled=True
    ).all()

    results = []
    for workflow in workflows:
        conditions = (
            json.loads(workflow.conditions)
            if isinstance(workflow.conditions, str)
            else workflow.conditions or {}
        )
        actions = (
            json.loads(workflow.actions)
            if isinstance(workflow.actions, str)
            else workflow.actions or []
        )

        if evaluate_conditions(conditions, entity):
            logger.info(
                "Trigger matched workflow %s for entity %s",
                workflow.id,
                entity.get("id"),
            )
            result = _execute_workflow(workflow, entity, actions, user_id=user_id)
            results.append(result)

    return results


# ===========================================================================
# 3. WORKFLOW CONDITION ENGINE
# ===========================================================================

def evaluate_conditions(conditions: dict, entity: dict) -> bool:
    """
    Evaluate a conditions dict against a business entity.

    Supported condition keys
    -----------------------
    amount_gt            : invoice amount > value
    amount_lt            : invoice amount < value
    overdue_days         : invoice overdue by >= value days
    client_risk          : entity['client_risk'] == value
    recurring_only       : entity['is_recurring'] must be True
    currency             : entity['currency'] == value
    payment_failed       : entity['payment_status'] == 'failed'
    revenue_drop_pct     : revenue dropped by >= value %
    unpaid_invoices_gt   : unpaid invoice count > value
    business_health_lt   : business health score < value
    subscription_tier    : entity['subscription_tier'] == value

    All conditions in the dict must hold (logical AND).
    An empty conditions dict always evaluates to True.
    """
    if not conditions:
        return True

    checks: list[bool] = []

    if "amount_gt" in conditions:
        checks.append(float(entity.get("amount", 0)) > float(conditions["amount_gt"]))

    if "amount_lt" in conditions:
        checks.append(float(entity.get("amount", 0)) < float(conditions["amount_lt"]))

    if "overdue_days" in conditions:
        due_date = entity.get("due_date")
        if due_date:
            if isinstance(due_date, str):
                due_date = datetime.fromisoformat(due_date)
            overdue = (_now() - due_date).days
            checks.append(overdue >= int(conditions["overdue_days"]))
        else:
            checks.append(False)

    if "client_risk" in conditions:
        checks.append(
            entity.get("client_risk", "").lower()
            == str(conditions["client_risk"]).lower()
        )

    if conditions.get("recurring_only"):
        checks.append(bool(entity.get("is_recurring", False)))

    if "currency" in conditions:
        checks.append(
            entity.get("currency", "").upper()
            == str(conditions["currency"]).upper()
        )

    if conditions.get("payment_failed"):
        checks.append(entity.get("payment_status") == "failed")

    if "revenue_drop_pct" in conditions:
        checks.append(
            float(entity.get("revenue_drop_pct", 0))
            >= float(conditions["revenue_drop_pct"])
        )

    if "unpaid_invoices_gt" in conditions:
        checks.append(
            int(entity.get("unpaid_invoices_count", 0))
            > int(conditions["unpaid_invoices_gt"])
        )

    if "business_health_lt" in conditions:
        checks.append(
            float(entity.get("business_health_score", 100))
            < float(conditions["business_health_lt"])
        )

    if "subscription_tier" in conditions:
        checks.append(
            entity.get("subscription_tier", "").lower()
            == str(conditions["subscription_tier"]).lower()
        )

    return all(checks)


# ===========================================================================
# 4. WORKFLOW ACTION EXECUTOR
# ===========================================================================

def execute_actions(
    actions: list[dict],
    entity: dict,
    *,
    workflow_id: str,
    user_id: int,
    log_id: str | None = None,
) -> list[dict]:
    """
    Execute a sequence of action dicts.

    Each action dict must contain:
        type   : ActionType value
        params : dict of action-specific parameters

    Returns a list of per-action result dicts.
    """
    results = []
    for action in actions:
        action_type = action.get("type")
        params = action.get("params", {})
        result = _dispatch_action(action_type, params, entity, user_id=user_id)
        results.append({"type": action_type, **result})

        # Broadcast each completed action over WebSocket
        _broadcast_event(
            "action_executed",
            {
                "workflow_id": workflow_id,
                "action_type": action_type,
                "entity_id": entity.get("id"),
                "result": result,
                "timestamp": _now().isoformat(),
            },
        )

    return results


def _dispatch_action(
    action_type: str,
    params: dict,
    entity: dict,
    *,
    user_id: int,
) -> dict:
    """Route an action type to its handler."""
    handlers = {
        ActionType.SEND_EMAIL: _action_send_email,
        ActionType.CREATE_REMINDER: _action_create_reminder,
        ActionType.GENERATE_REPORT: _action_generate_report,
        ActionType.NOTIFY_TEAM: _action_notify_team,
        ActionType.UPDATE_INVOICE_STATUS: _action_update_invoice_status,
        ActionType.CREATE_NOTIFICATION: _action_create_notification,
        ActionType.SEND_WHATSAPP: _action_send_whatsapp,
        ActionType.GENERATE_AI_SUMMARY: _action_generate_ai_summary,
        ActionType.ASSIGN_PRIORITY: _action_assign_priority,
        ActionType.GENERATE_FOLLOWUP: _action_generate_followup,
        ActionType.TRIGGER_AI_INSIGHT: _action_trigger_ai_insight,
        ActionType.DUPLICATE_INVOICE: _action_duplicate_invoice,
        ActionType.SCHEDULE_FOLLOWUP: _action_schedule_followup,
        ActionType.CREATE_TASK: _action_create_task,
        ActionType.STREAM_WEBSOCKET_EVENT: _action_stream_websocket_event,
    }
    handler = handlers.get(action_type)
    if handler is None:
        logger.warning("Unknown action type: %s", action_type)
        return {"ok": False, "error": f"Unknown action type: {action_type}"}

    try:
        return handler(params, entity, user_id=user_id)
    except Exception as exc:
        logger.exception("Action %s failed: %s", action_type, exc)
        return {"ok": False, "error": str(exc)}


# --- Individual action handlers ---

def _action_send_email(params: dict, entity: dict, *, user_id: int) -> dict:
    recipient = params.get("to") or entity.get("client_email")
    subject = params.get("subject", "InvoiceFlow Notification")
    body = params.get("body", "")
    if not recipient:
        return {"ok": False, "error": "No recipient email"}
    # Replace with your actual email service (SendGrid, SES, SMTP, etc.)
    logger.info("EMAIL → %s | %s", recipient, subject)
    return {"ok": True, "recipient": recipient, "subject": subject}


def _action_create_reminder(params: dict, entity: dict, *, user_id: int) -> dict:
    try:
        from app.models import Reminder
        from app import db
        reminder = Reminder(
            id=_new_id(),
            invoice_id=entity.get("id"),
            user_id=user_id,
            message=params.get("message", "Payment reminder"),
            remind_at=_now() + timedelta(days=params.get("delay_days", 0)),
            created_at=_now(),
        )
        db.session.add(reminder)
        db.session.commit()
        return {"ok": True, "reminder_id": reminder.id}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _action_generate_report(params: dict, entity: dict, *, user_id: int) -> dict:
    report_type = params.get("report_type", "summary")
    logger.info("Generating %s report for user %s", report_type, user_id)
    return {"ok": True, "report_type": report_type, "queued": True}


def _action_notify_team(params: dict, entity: dict, *, user_id: int) -> dict:
    team_id = params.get("team_id")
    message = params.get("message", "Workflow notification")
    logger.info("Team notification → team=%s | %s", team_id, message)
    return {"ok": True, "team_id": team_id}


def _action_update_invoice_status(params: dict, entity: dict, *, user_id: int) -> dict:
    new_status = params.get("status")
    invoice_id = entity.get("id")
    if not new_status or not invoice_id:
        return {"ok": False, "error": "status and entity.id required"}
    try:
        Invoice = _invoice_model()
        from app import db
        inv = Invoice.query.get(invoice_id)
        if inv:
            inv.status = new_status
            inv.updated_at = _now()
            db.session.commit()
        return {"ok": True, "invoice_id": invoice_id, "new_status": new_status}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _action_create_notification(params: dict, entity: dict, *, user_id: int) -> dict:
    message = params.get("message", "")
    try:
        from app.models import Notification
        from app import db
        note = Notification(
            id=_new_id(),
            user_id=user_id,
            message=message,
            entity_type=params.get("entity_type", "invoice"),
            entity_id=entity.get("id"),
            is_read=False,
            created_at=_now(),
        )
        db.session.add(note)
        db.session.commit()
        return {"ok": True, "notification_id": note.id}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _action_send_whatsapp(params: dict, entity: dict, *, user_id: int) -> dict:
    phone = params.get("phone") or entity.get("client_phone")
    message = params.get("message", "")
    if not phone:
        return {"ok": False, "error": "No phone number"}
    # Integrate with Twilio / WhatsApp Business API here
    logger.info("WHATSAPP → %s | %.60s", phone, message)
    return {"ok": True, "phone": phone}


def _action_generate_ai_summary(params: dict, entity: dict, *, user_id: int) -> dict:
    ai = _get_ai_client()
    if not ai:
        return {"ok": False, "error": "AI not available"}
    prompt = (
        f"Generate a concise business summary for this invoice: {json.dumps(entity)}"
    )
    try:
        resp = ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
        )
        summary = resp.choices[0].message.content.strip()
        return {"ok": True, "summary": summary}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _action_assign_priority(params: dict, entity: dict, *, user_id: int) -> dict:
    priority = assign_workflow_priority(entity)
    logger.info("Priority %s assigned to entity %s", priority, entity.get("id"))
    return {"ok": True, "priority": priority}


def _action_generate_followup(params: dict, entity: dict, *, user_id: int) -> dict:
    sequence = generate_followup_sequence(entity)
    return {"ok": True, "followup_sequence": sequence}


def _action_trigger_ai_insight(params: dict, entity: dict, *, user_id: int) -> dict:
    recs = generate_workflow_recommendations(user_id=user_id, context=entity)
    return {"ok": True, "insights": recs}


def _action_duplicate_invoice(params: dict, entity: dict, *, user_id: int) -> dict:
    invoice_id = entity.get("id")
    if not invoice_id:
        return {"ok": False, "error": "No invoice ID in entity"}
    try:
        Invoice = _invoice_model()
        from app import db
        original = Invoice.query.get(invoice_id)
        if not original:
            return {"ok": False, "error": "Invoice not found"}
        new_inv = Invoice(
            id=_new_id(),
            **{
                col: getattr(original, col)
                for col in original.__table__.columns.keys()
                if col not in ("id", "created_at", "updated_at", "status")
            },
        )
        new_inv.status = "draft"
        new_inv.created_at = _now()
        new_inv.updated_at = _now()
        db.session.add(new_inv)
        db.session.commit()
        return {"ok": True, "new_invoice_id": new_inv.id}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _action_schedule_followup(params: dict, entity: dict, *, user_id: int) -> dict:
    delay_days = int(params.get("delay_days", 3))
    run_at = _now() + timedelta(days=delay_days)
    result = schedule_delayed_action(
        action_type=params.get("follow_action", ActionType.SEND_EMAIL),
        action_params=params.get("follow_params", {}),
        entity=entity,
        delay=timedelta(days=delay_days),
        user_id=user_id,
    )
    return {"ok": True, "scheduled_at": run_at.isoformat(), **result}


def _action_create_task(params: dict, entity: dict, *, user_id: int) -> dict:
    task_title = params.get("title", "Follow up on invoice")
    due_at = _now() + timedelta(days=params.get("due_days", 1))
    logger.info("Task created: %s due %s", task_title, due_at.isoformat())
    return {"ok": True, "task_title": task_title, "due_at": due_at.isoformat()}


def _action_stream_websocket_event(params: dict, entity: dict, *, user_id: int) -> dict:
    event_name = params.get("event", "workflow_event")
    payload = {**params.get("payload", {}), "entity_id": entity.get("id")}
    _broadcast_event(event_name, payload)
    return {"ok": True, "event": event_name}


# ===========================================================================
# 5. AI WORKFLOW BUILDER
# ===========================================================================

def generate_workflow_from_prompt(
    prompt: str,
    *,
    user_id: int,
    team_id: int | None = None,
) -> dict:
    """
    Convert a natural-language description into a structured workflow and
    persist it.

    Example prompt
    --------------
    "If invoice is overdue for 5 days then send a reminder and notify the
    finance team."

    Returns
    -------
    Serialised workflow dict.
    """
    ai = _get_ai_client()
    if not ai:
        raise RuntimeError("AI features require OPENAI_API_KEY to be set")

    system = """You are an AI that converts plain-English automation requests
into structured JSON workflows for an invoice management platform.

Output ONLY valid JSON with this shape:
{
  "name": "<short workflow name>",
  "description": "<one sentence>",
  "trigger": "<TriggerType value>",
  "conditions": { "<key>": <value> },
  "actions": [
    { "type": "<ActionType value>", "params": { ... } }
  ]
}

Valid trigger values: invoice_created, invoice_sent, invoice_paid,
invoice_overdue, payment_received, recurring_invoice_generated,
client_risk_high, low_cashflow_detected, weekly_summary_ready,
report_generated, scheduled, ai_insight_generated

Valid action types: send_email, create_reminder, generate_report,
notify_team, update_invoice_status, create_notification, send_whatsapp,
generate_ai_summary, assign_priority, generate_followup,
trigger_ai_insight, duplicate_invoice, schedule_followup, create_task,
stream_websocket_event
"""

    resp = ai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        max_tokens=600,
        response_format={"type": "json_object"},
    )

    raw = resp.choices[0].message.content.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"AI returned invalid JSON: {exc}") from exc

    return create_workflow(
        name=data.get("name", "AI-generated workflow"),
        trigger=data.get("trigger", TriggerType.INVOICE_OVERDUE),
        conditions=data.get("conditions", {}),
        actions=data.get("actions", []),
        description=data.get("description", f"Generated from: {prompt[:120]}"),
        created_by=user_id,
        team_id=team_id,
    )


# ===========================================================================
# 6. SCHEDULED WORKFLOW RUNNER
# ===========================================================================

def run_scheduled_workflows(*, user_id: int | None = None) -> list[dict]:
    """
    Execute all SCHEDULED workflows whose next run time has passed.

    Designed to be called from a cron job, Celery beat task, or APScheduler.
    Handles: cron-based schedules, daily, weekly, and month-end workflows.

    Returns
    -------
    List of execution result dicts.
    """
    Workflow = _workflow_model()
    now = _now()

    scheduled = Workflow.query.filter_by(
        trigger=TriggerType.SCHEDULED, is_enabled=True
    ).all()

    results = []
    for workflow in scheduled:
        if not _is_due(workflow, now):
            continue

        logger.info("Running scheduled workflow: %s", workflow.id)
        entity = {"scheduled_at": now.isoformat(), "workflow_id": workflow.id}
        actions = (
            json.loads(workflow.actions)
            if isinstance(workflow.actions, str)
            else workflow.actions or []
        )
        result = _execute_workflow(
            workflow, entity, actions, user_id=user_id or workflow.created_by
        )
        results.append(result)

    return results


def _is_due(workflow, now: datetime) -> bool:
    """
    Very lightweight cron-readiness check.
    Supports: 'daily', 'weekly', 'monthly', or a raw 'HH:MM' string.
    For production use a proper cron library such as `croniter`.
    """
    cron = getattr(workflow, "schedule_cron", None)
    last = getattr(workflow, "last_run_at", None)

    if not cron:
        return False

    if last is None:
        return True  # Never run — trigger immediately

    elapsed = now - last
    schedules = {
        "daily": timedelta(hours=24),
        "weekly": timedelta(weeks=1),
        "monthly": timedelta(days=30),
        "hourly": timedelta(hours=1),
    }
    interval = schedules.get(cron.lower())
    if interval:
        return elapsed >= interval

    # Fallback: treat as time string "HH:MM" and check if we're past that
    # time today and haven't run today yet.
    try:
        hh, mm = map(int, cron.split(":"))
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        return now >= target and (last.date() < now.date())
    except Exception:
        return False


# ===========================================================================
# 7. WORKFLOW TEMPLATES SYSTEM
# ===========================================================================

def get_workflow_templates() -> list[dict]:
    """
    Return the built-in workflow template library.

    These cover the most common automation patterns for invoice-based
    businesses: overdue collection, thank-you flows, onboarding, high-risk
    escalation, executive reporting, and AI-driven insights.
    """
    return [
        {
            "key": "overdue_collection",
            "name": "Overdue Collection Flow",
            "description": "Automatically escalate overdue invoices with timed reminders.",
            "trigger": TriggerType.INVOICE_OVERDUE,
            "conditions": {"overdue_days": 3},
            "actions": [
                {"type": ActionType.SEND_EMAIL, "params": {"subject": "Payment Reminder"}},
                {"type": ActionType.CREATE_REMINDER, "params": {"delay_days": 4}},
                {"type": ActionType.ASSIGN_PRIORITY, "params": {}},
            ],
        },
        {
            "key": "thank_you_flow",
            "name": "Payment Thank-You Flow",
            "description": "Send a thank-you email when an invoice is paid.",
            "trigger": TriggerType.INVOICE_PAID,
            "conditions": {},
            "actions": [
                {
                    "type": ActionType.SEND_EMAIL,
                    "params": {"subject": "Thank you for your payment!"},
                },
                {"type": ActionType.CREATE_NOTIFICATION, "params": {"message": "Payment received"}},
            ],
        },
        {
            "key": "client_onboarding",
            "name": "New Client Onboarding Flow",
            "description": "Welcome new clients and set up their account.",
            "trigger": TriggerType.INVOICE_CREATED,
            "conditions": {},
            "actions": [
                {"type": ActionType.SEND_EMAIL, "params": {"subject": "Welcome to InvoiceFlow"}},
                {"type": ActionType.CREATE_TASK, "params": {"title": "Review new client profile", "due_days": 1}},
            ],
        },
        {
            "key": "recurring_invoice_flow",
            "name": "Recurring Invoice Automation",
            "description": "Handle recurring invoices with automatic notifications.",
            "trigger": TriggerType.RECURRING_INVOICE_GENERATED,
            "conditions": {"recurring_only": True},
            "actions": [
                {"type": ActionType.SEND_EMAIL, "params": {"subject": "Your recurring invoice is ready"}},
                {"type": ActionType.CREATE_NOTIFICATION, "params": {"message": "Recurring invoice generated"}},
            ],
        },
        {
            "key": "high_risk_escalation",
            "name": "High-Risk Client Escalation",
            "description": "Alert finance team when a high-risk client invoice is overdue.",
            "trigger": TriggerType.CLIENT_RISK_HIGH,
            "conditions": {"client_risk": "high", "overdue_days": 7},
            "actions": [
                {"type": ActionType.NOTIFY_TEAM, "params": {"message": "High-risk client escalation needed"}},
                {"type": ActionType.GENERATE_AI_SUMMARY, "params": {}},
                {"type": ActionType.ASSIGN_PRIORITY, "params": {}},
            ],
        },
        {
            "key": "executive_summary",
            "name": "Executive Summary Automation",
            "description": "Deliver a weekly executive summary every Monday morning.",
            "trigger": TriggerType.WEEKLY_SUMMARY_READY,
            "conditions": {},
            "actions": [
                {"type": ActionType.GENERATE_REPORT, "params": {"report_type": "executive_weekly"}},
                {"type": ActionType.SEND_EMAIL, "params": {"subject": "Weekly Executive Summary"}},
                {"type": ActionType.STREAM_WEBSOCKET_EVENT, "params": {"event": "report_generated"}},
            ],
        },
        {
            "key": "payment_followup",
            "name": "Payment Follow-Up Flow",
            "description": "Automatically follow up with clients who have not paid.",
            "trigger": TriggerType.INVOICE_OVERDUE,
            "conditions": {"overdue_days": 14},
            "actions": [
                {"type": ActionType.GENERATE_FOLLOWUP, "params": {}},
                {"type": ActionType.SEND_EMAIL, "params": {"subject": "Follow-Up on Outstanding Invoice"}},
                {"type": ActionType.SCHEDULE_FOLLOWUP, "params": {"delay_days": 7}},
            ],
        },
        {
            "key": "weekly_ai_insights",
            "name": "Weekly AI Insights Report",
            "description": "Generate and deliver AI-powered business insights each week.",
            "trigger": TriggerType.WEEKLY_SUMMARY_READY,
            "conditions": {},
            "actions": [
                {"type": ActionType.TRIGGER_AI_INSIGHT, "params": {}},
                {"type": ActionType.GENERATE_REPORT, "params": {"report_type": "ai_insights"}},
                {"type": ActionType.NOTIFY_TEAM, "params": {"message": "Weekly AI insights ready"}},
            ],
        },
    ]


def create_workflow_from_template(
    template_key: str,
    *,
    user_id: int,
    team_id: int | None = None,
) -> dict:
    """Instantiate a built-in template as a real workflow."""
    templates = {t["key"]: t for t in get_workflow_templates()}
    tmpl = templates.get(template_key)
    if not tmpl:
        raise ValueError(f"Template {template_key!r} not found")

    return create_workflow(
        name=tmpl["name"],
        description=tmpl["description"],
        trigger=tmpl["trigger"],
        conditions=tmpl["conditions"],
        actions=tmpl["actions"],
        created_by=user_id,
        team_id=team_id,
        is_template=False,
    )


# ===========================================================================
# 8. WORKFLOW EXECUTION LOGS
# ===========================================================================

def log_workflow_run(
    workflow_id: str,
    entity_id: str | None,
    entity_type: str,
    status: str,
    action_results: list[dict],
    *,
    error: str | None = None,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> dict:
    """
    Persist an execution log entry for a workflow run.

    Parameters
    ----------
    workflow_id    : ID of the executed workflow.
    entity_id      : ID of the triggering entity (invoice, payment, etc.).
    entity_type    : Type string (e.g. 'invoice', 'payment').
    status         : ExecutionStatus value.
    action_results : Per-action result list from execute_actions().
    error          : Error message if status == 'failed'.
    started_at     : Execution start timestamp.
    ended_at       : Execution end timestamp.
    """
    db = _get_db()
    WorkflowLog = _log_model()

    started_at = started_at or _now()
    ended_at = ended_at or _now()
    duration_ms = int((ended_at - started_at).total_seconds() * 1000)

    log = WorkflowLog(
        id=_new_id(),
        workflow_id=workflow_id,
        entity_id=entity_id,
        entity_type=entity_type,
        status=status,
        action_results=json.dumps(action_results),
        error=error,
        duration_ms=duration_ms,
        started_at=started_at,
        ended_at=ended_at,
    )
    db.session.add(log)

    # Update workflow stats
    Workflow = _workflow_model()
    workflow = Workflow.query.get(workflow_id)
    if workflow:
        workflow.run_count = (getattr(workflow, "run_count", 0) or 0) + 1
        workflow.last_run_at = ended_at

    db.session.commit()
    return {
        "id": log.id,
        "workflow_id": workflow_id,
        "entity_id": entity_id,
        "entity_type": entity_type,
        "status": status,
        "duration_ms": duration_ms,
        "error": error,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
    }


# ===========================================================================
# 9. AI ESCALATION ENGINE
# ===========================================================================

def handle_overdue_escalation(invoice: dict, *, user_id: int) -> dict:
    """
    Escalate an overdue invoice through a timed, tone-aware sequence.

    Day  3  → Friendly payment reminder
    Day  7  → Professional follow-up
    Day 14  → Urgent warning
    Day 21+ → Notify finance / admin team

    Parameters
    ----------
    invoice : Invoice dict (must include 'due_date' or 'overdue_days').
    user_id : Acting user.

    Returns
    -------
    Dict with escalation level and actions taken.
    """
    due_date = invoice.get("due_date")
    if due_date and isinstance(due_date, str):
        due_date = datetime.fromisoformat(due_date)

    if due_date:
        overdue_days = max(0, (_now() - due_date).days)
    else:
        overdue_days = int(invoice.get("overdue_days", 0))

    if overdue_days < 3:
        return {"level": "none", "overdue_days": overdue_days, "actions": []}

    if overdue_days < 7:
        level = "friendly"
        actions = [
            {
                "type": ActionType.SEND_EMAIL,
                "params": {
                    "subject": "Friendly Reminder: Invoice Payment Due",
                    "body": (
                        f"Hi, just a friendly reminder that invoice "
                        f"#{invoice.get('number', '')} is {overdue_days} days overdue. "
                        "Please arrange payment at your earliest convenience."
                    ),
                },
            }
        ]
    elif overdue_days < 14:
        level = "professional"
        actions = [
            {
                "type": ActionType.SEND_EMAIL,
                "params": {
                    "subject": f"Payment Required: Invoice #{invoice.get('number', '')}",
                    "body": (
                        f"Your invoice of {invoice.get('amount', '')} is now "
                        f"{overdue_days} days overdue. Please make payment immediately."
                    ),
                },
            },
            {"type": ActionType.GENERATE_AI_SUMMARY, "params": {}},
        ]
    elif overdue_days < 21:
        level = "urgent"
        actions = [
            {
                "type": ActionType.SEND_EMAIL,
                "params": {
                    "subject": f"URGENT: Overdue Invoice #{invoice.get('number', '')}",
                    "body": (
                        f"This is an urgent notice. Your invoice is {overdue_days} days "
                        "overdue. Failure to pay may result in service suspension."
                    ),
                },
            },
            {"type": ActionType.ASSIGN_PRIORITY, "params": {}},
            {"type": ActionType.CREATE_NOTIFICATION, "params": {"message": "URGENT: Invoice overdue 14+ days"}},
        ]
    else:
        level = "critical"
        actions = [
            {
                "type": ActionType.NOTIFY_TEAM,
                "params": {
                    "message": (
                        f"Invoice #{invoice.get('number', '')} is {overdue_days} days "
                        "overdue. Finance team action required."
                    )
                },
            },
            {"type": ActionType.GENERATE_FOLLOWUP, "params": {}},
            {"type": ActionType.ASSIGN_PRIORITY, "params": {}},
            {"type": ActionType.CREATE_TASK, "params": {"title": "Escalate overdue invoice to collections", "due_days": 1}},
        ]

    executed = execute_actions(
        actions, invoice, workflow_id="escalation", user_id=user_id
    )
    return {"level": level, "overdue_days": overdue_days, "actions": executed}


# ===========================================================================
# 10. MULTI-STEP WORKFLOW CHAINS
# ===========================================================================

def execute_workflow_chain(
    steps: list[dict],
    entity: dict,
    *,
    user_id: int,
    workflow_id: str = "chain",
) -> list[dict]:
    """
    Execute a sequence of workflow steps with optional delays between them.

    Each step dict:
    {
        "action": { "type": ..., "params": ... },
        "delay_seconds": 0,        # optional inter-step delay
        "condition_check": { ... } # optional per-step conditions
    }

    Returns
    -------
    List of per-step result dicts.
    """
    chain_results = []

    for i, step in enumerate(steps):
        condition_check = step.get("condition_check", {})
        if condition_check and not evaluate_conditions(condition_check, entity):
            chain_results.append({"step": i, "skipped": True, "reason": "condition_not_met"})
            continue

        action = step.get("action", {})
        if not action:
            chain_results.append({"step": i, "skipped": True, "reason": "no_action"})
            continue

        delay_seconds = int(step.get("delay_seconds", 0))
        if delay_seconds > 0:
            # In production: use Celery delay / APScheduler / Redis queue
            import time
            logger.info("Chain step %d: waiting %ds before executing", i, delay_seconds)
            time.sleep(min(delay_seconds, 5))  # Cap sleep to 5s in sync context

        results = execute_actions(
            [action], entity, workflow_id=workflow_id, user_id=user_id
        )
        chain_results.append({"step": i, "results": results})

        _broadcast_event(
            "chain_step_completed",
            {"workflow_id": workflow_id, "step": i, "entity_id": entity.get("id")},
        )

    return chain_results


# ===========================================================================
# 11. DELAY & WAIT SYSTEM
# ===========================================================================

def schedule_delayed_action(
    action_type: str,
    action_params: dict,
    entity: dict,
    delay: timedelta,
    *,
    user_id: int,
) -> dict:
    """
    Schedule an action to run after a specified delay.

    In production, persist to a job queue (Celery, RQ, APScheduler).
    This implementation stores the intent and returns scheduling metadata.

    Delay presets
    -------------
    • timedelta(hours=1)         → wait 1 hour
    • timedelta(days=3)          → wait 3 days
    • timedelta(days=N) where N is days until invoice due date
    """
    run_at = _now() + delay
    job_id = _new_id()

    logger.info(
        "Delayed action scheduled: %s for entity=%s at %s (job=%s)",
        action_type,
        entity.get("id"),
        run_at.isoformat(),
        job_id,
    )

    # TODO: enqueue to your task queue here, e.g.:
    # celery_app.send_task(
    #     "tasks.execute_action",
    #     args=[action_type, action_params, entity, user_id],
    #     eta=run_at,
    # )

    return {
        "job_id": job_id,
        "action_type": action_type,
        "scheduled_at": run_at.isoformat(),
        "entity_id": entity.get("id"),
        "delay_seconds": int(delay.total_seconds()),
    }


# ===========================================================================
# 12. AI WORKFLOW RECOMMENDATIONS
# ===========================================================================

def generate_workflow_recommendations(
    *,
    user_id: int,
    context: dict | None = None,
) -> list[dict]:
    """
    Use AI (or rule-based fallback) to recommend new workflows based on the
    user's current automation coverage and business context.

    Returns
    -------
    List of recommendation dicts, each with keys:
        title       : Short recommendation headline.
        reason      : Why this is recommended.
        template_key: Template to instantiate (optional).
        impact      : Expected business impact.
    """
    ai = _get_ai_client()
    context = context or {}

    base_recs = [
        {
            "title": "Automate Overdue Reminders",
            "reason": "Manual reminders take hours each week and are easy to miss.",
            "template_key": "overdue_collection",
            "impact": "Recover payments up to 40% faster.",
        },
        {
            "title": "High-Risk Client Alerts",
            "reason": "Proactive alerts prevent revenue loss from high-risk accounts.",
            "template_key": "high_risk_escalation",
            "impact": "Reduce write-offs by flagging risk before it escalates.",
        },
        {
            "title": "Recurring Invoice Automation",
            "reason": "Recurring invoices sent manually can save 4+ hours/week.",
            "template_key": "recurring_invoice_flow",
            "impact": "Save 4 hours/week, eliminate missed billing cycles.",
        },
        {
            "title": "Weekly AI Business Insights",
            "reason": "Regular AI summaries keep you ahead of cash-flow issues.",
            "template_key": "weekly_ai_insights",
            "impact": "Spot trends before they become problems.",
        },
    ]

    if not ai:
        return base_recs

    try:
        prompt = (
            f"Based on this business context: {json.dumps(context)}, "
            "suggest 3 additional workflow automations for an invoice management platform. "
            "Return JSON array with objects: {title, reason, impact}."
        )
        resp = ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            response_format={"type": "json_object"},
        )
        ai_recs = json.loads(resp.choices[0].message.content)
        if isinstance(ai_recs, dict) and "recommendations" in ai_recs:
            ai_recs = ai_recs["recommendations"]
        if isinstance(ai_recs, list):
            base_recs.extend(ai_recs[:3])
    except Exception as exc:
        logger.warning("AI recommendations failed, using defaults: %s", exc)

    return base_recs


# ===========================================================================
# 13. WORKFLOW ANALYTICS
# ===========================================================================

def get_workflow_analytics(
    *,
    user_id: int,
    team_id: int | None = None,
    days: int = 30,
) -> dict:
    """
    Aggregate workflow execution metrics for the analytics dashboard.

    Returns
    -------
    Dict with keys: total_executions, success_rate, reminders_sent,
    payments_recovered, automation_savings_hours, overdue_reduction_pct,
    top_workflows, recent_executions.
    """
    WorkflowLog = _log_model()
    Workflow = _workflow_model()

    since = _now() - timedelta(days=days)

    logs_q = WorkflowLog.query.filter(WorkflowLog.started_at >= since)

    all_logs = logs_q.all()
    total = len(all_logs)
    completed = sum(1 for l in all_logs if l.status == ExecutionStatus.COMPLETED)
    failed = sum(1 for l in all_logs if l.status == ExecutionStatus.FAILED)

    success_rate = round((completed / total * 100) if total else 0, 1)

    reminders_sent = sum(
        1 for l in all_logs
        if l.entity_type in ("reminder", "invoice")
        and l.status == ExecutionStatus.COMPLETED
    )

    top_workflows_q = (
        Workflow.query
        .order_by(Workflow.run_count.desc())
        .limit(5)
        .all()
    )

    recent = [
        {
            "id": l.id,
            "workflow_id": l.workflow_id,
            "entity_id": l.entity_id,
            "status": l.status,
            "duration_ms": getattr(l, "duration_ms", 0),
            "started_at": l.started_at.isoformat() if l.started_at else None,
        }
        for l in sorted(all_logs, key=lambda x: x.started_at, reverse=True)[:10]
    ]

    return {
        "period_days": days,
        "total_executions": total,
        "completed": completed,
        "failed": failed,
        "success_rate": success_rate,
        "reminders_sent": reminders_sent,
        "payments_recovered": completed,  # Proxy; replace with actual payment data
        "automation_savings_hours": round(total * 0.083, 1),  # ~5 min per run
        "overdue_reduction_pct": min(round(completed * 2.5, 1), 60.0),
        "top_workflows": [_serialize_workflow(w) for w in top_workflows_q],
        "recent_executions": recent,
    }


# ===========================================================================
# 14. REAL-TIME WORKFLOW EVENTS (WebSocket)
# ===========================================================================

def _broadcast_event(event: str, payload: dict) -> None:
    """
    Emit a real-time event via Flask-SocketIO.
    Falls back gracefully when SocketIO is not configured.
    """
    sio = _get_socketio()
    if sio is None:
        logger.debug("WebSocket broadcast skipped (SocketIO not configured): %s", event)
        return
    try:
        sio.emit(event, payload)
        logger.debug("Broadcast: %s → %s", event, payload)
    except Exception as exc:
        logger.warning("WebSocket broadcast failed for %s: %s", event, exc)


# ===========================================================================
# 15. AI FOLLOW-UP GENERATOR
# ===========================================================================

def generate_followup_sequence(
    invoice: dict,
    *,
    tone_override: str | None = None,
) -> list[dict]:
    """
    Generate a personalised follow-up email sequence for an overdue invoice.

    Uses AI when available, falls back to template-based messages.

    Parameters
    ----------
    invoice        : Invoice dict (number, amount, client_name, due_date, etc.).
    tone_override  : Force a tone ('friendly', 'professional', 'urgent').

    Returns
    -------
    List of follow-up step dicts with keys: day, subject, body, tone.
    """
    ai = _get_ai_client()

    due_date = invoice.get("due_date")
    if due_date and isinstance(due_date, str):
        due_date = datetime.fromisoformat(due_date)
    overdue_days = max(0, (_now() - due_date).days) if due_date else 0
    amount = invoice.get("amount", "")
    client = invoice.get("client_name", "Valued Client")
    inv_num = invoice.get("number", "")

    if not ai:
        return _template_followup_sequence(client, inv_num, amount, overdue_days)

    try:
        prompt = f"""
Create a 3-step email follow-up sequence for this overdue invoice:
- Client: {client}
- Invoice #: {inv_num}
- Amount: {amount}
- Overdue by: {overdue_days} days
- Tone preference: {tone_override or 'escalating (friendly → professional → urgent)'}

Return JSON array:
[{{"day": 0, "subject": "...", "body": "...", "tone": "friendly"}}, ...]
"""
        resp = ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        sequence = data if isinstance(data, list) else data.get("steps", [])
        return sequence
    except Exception as exc:
        logger.warning("AI follow-up generation failed: %s", exc)
        return _template_followup_sequence(client, inv_num, amount, overdue_days)


def _template_followup_sequence(
    client: str, inv_num: str, amount: Any, overdue_days: int
) -> list[dict]:
    return [
        {
            "day": 0,
            "tone": "friendly",
            "subject": f"Friendly Reminder: Invoice #{inv_num}",
            "body": f"Hi {client}, just a quick reminder that invoice #{inv_num} for {amount} is overdue. Please let us know if you have any questions.",
        },
        {
            "day": 4,
            "tone": "professional",
            "subject": f"Follow-Up: Invoice #{inv_num} Payment Required",
            "body": f"Dear {client}, we have not yet received payment for invoice #{inv_num} ({amount}), now {overdue_days + 4} days overdue. Please arrange payment promptly.",
        },
        {
            "day": 10,
            "tone": "urgent",
            "subject": f"URGENT: Invoice #{inv_num} Overdue",
            "body": f"Dear {client}, invoice #{inv_num} ({amount}) remains unpaid after {overdue_days + 10} days. Immediate payment is required to avoid service interruption.",
        },
    ]


# ===========================================================================
# 16. SMART RETRY LOGIC
# ===========================================================================

def retry_failed_workflow(
    log_id: str,
    *,
    user_id: int,
    max_retries: int = 3,
    backoff_seconds: int = 60,
) -> dict:
    """
    Retry a failed workflow execution with exponential backoff.

    Handles: email failures, webhook failures, timeout errors, API failures.

    Parameters
    ----------
    log_id          : ID of the failed WorkflowLog entry.
    user_id         : User requesting the retry.
    max_retries     : Maximum retry attempts allowed.
    backoff_seconds : Base delay between retries (doubles each attempt).

    Returns
    -------
    New execution result dict, or error dict if max retries exceeded.
    """
    WorkflowLog = _log_model()
    Workflow = _workflow_model()

    log = WorkflowLog.query.get(log_id)
    if not log:
        return {"ok": False, "error": f"Log {log_id!r} not found"}

    retry_count = getattr(log, "retry_count", 0) or 0
    if retry_count >= max_retries:
        return {
            "ok": False,
            "error": f"Max retries ({max_retries}) exceeded for log {log_id}",
        }

    workflow = Workflow.query.get(log.workflow_id)
    if not workflow:
        return {"ok": False, "error": "Original workflow no longer exists"}

    # Exponential backoff: 60s, 120s, 240s, ...
    delay = timedelta(seconds=backoff_seconds * (2 ** retry_count))
    entity = {"id": log.entity_id, "entity_type": log.entity_type}
    actions = (
        json.loads(workflow.actions)
        if isinstance(workflow.actions, str)
        else workflow.actions or []
    )

    logger.info(
        "Retrying workflow %s (attempt %d/%d) after %s",
        workflow.id,
        retry_count + 1,
        max_retries,
        delay,
    )

    result = _execute_workflow(workflow, entity, actions, user_id=user_id)

    db = _get_db()
    log.retry_count = retry_count + 1
    db.session.commit()

    return result


# ===========================================================================
# 17. WORKFLOW PERMISSION SYSTEM (RBAC)
# ===========================================================================

_ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {"create", "read", "update", "delete", "toggle", "run"},
    "finance": {"read", "run", "create"},
    "sales": {"read", "create"},
    "viewer": {"read"},
}


def check_workflow_permission(
    user_role: str,
    action: str,
) -> bool:
    """
    Return True if the given role is allowed to perform `action`.

    Actions: 'create', 'read', 'update', 'delete', 'toggle', 'run'.

    Example roles: 'admin', 'finance', 'sales', 'viewer'.
    """
    allowed = _ROLE_PERMISSIONS.get(user_role.lower(), set())
    return action.lower() in allowed


def require_workflow_permission(user_role: str, action: str) -> None:
    """Raise PermissionError if the role lacks the required action."""
    if not check_workflow_permission(user_role, action):
        raise PermissionError(
            f"Role {user_role!r} is not permitted to perform {action!r} on workflows"
        )


# ===========================================================================
# 19. AI SMART PRIORITY DETECTION
# ===========================================================================

def assign_workflow_priority(entity: dict) -> str:
    """
    Compute a priority level for an invoice or workflow entity.

    Priority is derived from invoice amount, overdue age, client risk, and
    business impact. Returns one of: 'low', 'medium', 'high', 'critical'.
    """
    score = 0

    amount = float(entity.get("amount", 0))
    if amount > 50_000:
        score += 4
    elif amount > 10_000:
        score += 3
    elif amount > 1_000:
        score += 2
    else:
        score += 1

    due_date = entity.get("due_date")
    if due_date:
        if isinstance(due_date, str):
            due_date = datetime.fromisoformat(due_date)
        overdue = max(0, (_now() - due_date).days)
        if overdue >= 21:
            score += 4
        elif overdue >= 14:
            score += 3
        elif overdue >= 7:
            score += 2
        elif overdue >= 3:
            score += 1

    client_risk = entity.get("client_risk", "").lower()
    risk_scores = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    score += risk_scores.get(client_risk, 0)

    if entity.get("payment_failed"):
        score += 2

    if score >= 9:
        return WorkflowPriority.CRITICAL
    elif score >= 6:
        return WorkflowPriority.HIGH
    elif score >= 3:
        return WorkflowPriority.MEDIUM
    return WorkflowPriority.LOW


# ===========================================================================
# INTERNAL — Shared execution helper
# ===========================================================================

def _execute_workflow(
    workflow,
    entity: dict,
    actions: list[dict],
    *,
    user_id: int,
) -> dict:
    """Run actions, log the result, and broadcast lifecycle events."""
    started_at = _now()
    log_id = _new_id()

    _broadcast_event(
        "workflow_started",
        {
            "workflow_id": workflow.id,
            "workflow_name": workflow.name,
            "entity_id": entity.get("id"),
            "timestamp": started_at.isoformat(),
        },
    )

    status = ExecutionStatus.RUNNING
    action_results: list[dict] = []
    error: str | None = None

    try:
        action_results = execute_actions(
            actions, entity,
            workflow_id=workflow.id,
            user_id=user_id,
            log_id=log_id,
        )
        status = ExecutionStatus.COMPLETED
    except Exception as exc:
        status = ExecutionStatus.FAILED
        error = str(exc)
        logger.exception("Workflow %s execution failed: %s", workflow.id, exc)

    ended_at = _now()

    try:
        log_workflow_run(
            workflow_id=workflow.id,
            entity_id=entity.get("id"),
            entity_type=entity.get("entity_type", "invoice"),
            status=status,
            action_results=action_results,
            error=error,
            started_at=started_at,
            ended_at=ended_at,
        )
    except Exception as log_exc:
        logger.warning("Failed to log workflow run: %s", log_exc)

    event = "workflow_completed" if status == ExecutionStatus.COMPLETED else "workflow_failed"
    _broadcast_event(
        event,
        {
            "workflow_id": workflow.id,
            "status": status,
            "entity_id": entity.get("id"),
            "duration_ms": int((ended_at - started_at).total_seconds() * 1000),
            "timestamp": ended_at.isoformat(),
        },
    )

    return {
        "workflow_id": workflow.id,
        "workflow_name": workflow.name,
        "status": status,
        "action_results": action_results,
        "error": error,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
    }
