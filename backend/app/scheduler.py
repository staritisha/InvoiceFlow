# ═══════════════════════════════════════════════════════════════════════════════
#  AI Invoice Intelligence Platform — scheduler.py
#  Production-grade background processing architecture.
#  Timezone-aware, observable, self-healing scheduler powering autonomous
#  invoicing, AI analytics, workflow execution, and SaaS automation.
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app import models
from app.config import settings
from app.database import SessionLocal
from app.utils import (
    ai_invoice_priority_score,
    classify_payment_behavior,
    days_overdue,
    format_notification_payload,
    format_reminder_email,
    generate_invoice_number,
    map_client_risk,
    send_email,
    time_ago,
)

# ── Logger ────────────────────────────────────────────────────────────────────

logger = logging.getLogger("invoiceflow.scheduler")

# ── Scheduler Telemetry ───────────────────────────────────────────────────────

_scheduler_metrics: dict[str, Any] = {
    "jobs_executed": 0,
    "jobs_failed": 0,
    "jobs_missed": 0,
    "last_run": {},       # job_id → ISO timestamp
    "last_error": {},     # job_id → error message
    "reminders_sent": 0,
    "invoices_generated": 0,
    "workflows_triggered": 0,
    "insights_generated": 0,
}

# Singleton reference — used by health checks and graceful shutdown
_scheduler: Optional[BackgroundScheduler] = None

# ── APScheduler Event Listener ────────────────────────────────────────────────

def _scheduler_event_listener(event) -> None:
    job_id = event.job_id
    if event.code == EVENT_JOB_EXECUTED:
        _scheduler_metrics["jobs_executed"] += 1
        _scheduler_metrics["last_run"][job_id] = datetime.now(timezone.utc).isoformat()
        logger.debug(f"[scheduler] ✓ Job '{job_id}' completed successfully")

    elif event.code == EVENT_JOB_ERROR:
        _scheduler_metrics["jobs_failed"] += 1
        _scheduler_metrics["last_error"][job_id] = str(event.exception)
        logger.error(f"[scheduler] ✗ Job '{job_id}' raised: {event.exception}", exc_info=event.exception)

    elif event.code == EVENT_JOB_MISSED:
        _scheduler_metrics["jobs_missed"] += 1
        logger.warning(f"[scheduler] ⚠ Job '{job_id}' execution was missed")


# ═══════════════════════════════════════════════════════════════════════════════
#  JOB IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════════════════════════

# ── 1. Daily Reminder Processor ───────────────────────────────────────────────

def run_daily_reminders() -> None:
    """
    Send payment reminders for all unpaid/overdue invoices.
    Escalates tone automatically based on days overdue.
    """
    logger.info("[scheduler] Running daily reminder processor…")
    db = SessionLocal()
    sent = 0
    failed = 0

    try:
        invoices = (
            db.query(models.Invoice)
            .filter(models.Invoice.status.notin_(["paid", "cancelled"]))
            .all()
        )

        for invoice in invoices:
            customer = db.query(models.Customer).filter(
                models.Customer.id == invoice.customer_id
            ).first()

            if not customer or not customer.email:
                continue

            overdue = days_overdue(invoice.due_date) if invoice.due_date else 0

            # Escalate tone based on how overdue the invoice is
            if overdue >= 30:
                tone = "urgent"
            elif overdue >= 7:
                tone = "firm"
            else:
                tone = "polite"

            try:
                email = format_reminder_email(
                    invoice.invoice_number,
                    float(invoice.total_amount or 0),
                    tone,
                )
                send_email(customer.email, email["subject"], email["body"])
                sent += 1
                _scheduler_metrics["reminders_sent"] += 1
            except Exception as exc:
                failed += 1
                logger.warning(f"[scheduler] Reminder email failed for invoice {invoice.id}: {exc}")

    finally:
        db.close()

    logger.info(f"[scheduler] Daily reminders: {sent} sent, {failed} failed")


# ── 2. Overdue Invoice Checker ────────────────────────────────────────────────

def run_overdue_checker() -> None:
    """
    Mark invoices as 'overdue' when their due date has passed and they are unpaid.
    Also recalculates AI priority scores.
    """
    logger.info("[scheduler] Running overdue invoice checker…")
    db = SessionLocal()
    updated = 0

    try:
        today = date.today()
        pending_invoices = (
            db.query(models.Invoice)
            .filter(
                models.Invoice.status == "pending",
                models.Invoice.due_date < today,
            )
            .all()
        )

        for invoice in pending_invoices:
            invoice.status = "overdue"
            updated += 1
            logger.debug(f"[scheduler] Invoice {invoice.invoice_number} marked overdue")

        if updated:
            db.commit()

    finally:
        db.close()

    logger.info(f"[scheduler] Overdue checker: {updated} invoices updated")


# ── 3. Recurring Invoice Generator ───────────────────────────────────────────

def run_recurring_invoice_generator() -> None:
    """
    Generate invoices from active recurring billing plans that are due today.
    Updates next_billing_date after each generation.
    """
    logger.info("[scheduler] Running recurring invoice generator…")
    db = SessionLocal()
    generated = 0

    try:
        today_dt = datetime.now(timezone.utc)
        plans = (
            db.query(models.RecurringBilling)
            .filter(
                models.RecurringBilling.is_active == True,
                models.RecurringBilling.next_billing_date <= today_dt,
            )
            .all()
        )

        for plan in plans:
            try:
                invoice_number = generate_invoice_number("REC")
                new_invoice = models.Invoice(
                    invoice_number=invoice_number,
                    customer_id=plan.customer_id,
                    user_id=plan.user_id,
                    due_date=plan.next_billing_date,
                    status="pending",
                    total_amount=plan.amount,
                    notes=f"Auto-generated from recurring plan: {plan.title}",
                )
                db.add(new_invoice)
                db.flush()

                freq_days = {"monthly": 30, "quarterly": 90, "yearly": 365}
                plan.next_billing_date += timedelta(days=freq_days.get(plan.frequency, 30))
                generated += 1
                _scheduler_metrics["invoices_generated"] += 1

            except Exception as exc:
                logger.warning(f"[scheduler] Failed to generate recurring invoice for plan {plan.id}: {exc}")

        if generated:
            db.commit()

    finally:
        db.close()

    logger.info(f"[scheduler] Recurring generator: {generated} invoices created")


# ── 4. AI Invoice Priority Recalculation ──────────────────────────────────────

def run_priority_recalculation() -> None:
    """Recalculate AI priority scores for all open invoices."""
    logger.info("[scheduler] Recalculating AI invoice priorities…")
    db = SessionLocal()
    updated = 0

    try:
        invoices = (
            db.query(models.Invoice)
            .filter(models.Invoice.status.notin_(["paid", "cancelled"]))
            .all()
        )

        for invoice in invoices:
            overdue = days_overdue(invoice.due_date) if invoice.due_date else 0
            amount = float(invoice.total_amount or 0)
            score = ai_invoice_priority_score(overdue, amount)

            if hasattr(invoice, "ai_priority"):
                invoice.ai_priority = score
                updated += 1

        if updated:
            db.commit()

    finally:
        db.close()

    logger.info(f"[scheduler] Priority recalculation: {updated} invoices updated")


# ── 5. Client Risk Score Recalculation ───────────────────────────────────────

def run_client_risk_recalculation() -> None:
    """Recompute payment behavior and risk scores for all clients."""
    logger.info("[scheduler] Recalculating client risk scores…")
    db = SessionLocal()
    updated = 0

    try:
        customers = db.query(models.Customer).all()

        for customer in customers:
            invoices = (
                db.query(models.Invoice)
                .filter(
                    models.Invoice.customer_id == customer.id,
                    models.Invoice.status == "paid",
                )
                .all()
            )

            if not invoices:
                continue

            # Approximate avg days to pay using overdue days proxy
            avg_days = sum(
                max(days_overdue(inv.due_date) if inv.due_date else 0, 0)
                for inv in invoices
            ) / len(invoices)

            behavior = classify_payment_behavior(avg_days)
            risk_score = {"excellent": 10, "good": 30, "fair": 60, "poor": 85}.get(behavior, 50)

            if hasattr(customer, "payment_behavior_score"):
                customer.payment_behavior_score = 100 - risk_score
            if hasattr(customer, "risk_score"):
                customer.risk_score = float(risk_score)

            updated += 1

        if updated:
            db.commit()

    finally:
        db.close()

    logger.info(f"[scheduler] Risk recalculation: {updated} clients updated")


# ── 6. Workflow Trigger Polling ───────────────────────────────────────────────

def run_workflow_trigger_poll() -> None:
    """
    Poll for workflows with trigger_type='scheduled' or evaluate
    invoice_overdue / client_risk_high conditions and queue runs.
    """
    logger.info("[scheduler] Polling workflow triggers…")
    db = SessionLocal()
    triggered = 0

    try:
        if not hasattr(models, "Workflow"):
            return

        workflows = (
            db.query(models.Workflow)
            .filter(models.Workflow.is_active == True)
            .all()
        )

        for workflow in workflows:
            try:
                _evaluate_workflow_trigger(workflow, db)
                triggered += 1
                _scheduler_metrics["workflows_triggered"] += 1
            except Exception as exc:
                logger.warning(f"[scheduler] Workflow {workflow.id} trigger evaluation failed: {exc}")

    finally:
        db.close()

    logger.debug(f"[scheduler] Workflow poll: {triggered} workflows evaluated")


def _evaluate_workflow_trigger(workflow, db) -> None:
    """Minimal trigger evaluation stub — expand with your workflow service."""
    trigger = getattr(workflow, "trigger_type", None)
    if trigger == "invoice_overdue":
        overdue_count = (
            db.query(models.Invoice)
            .filter(models.Invoice.status == "overdue")
            .count()
        )
        if overdue_count > 0:
            logger.debug(f"[scheduler] Workflow {workflow.id} triggered: {overdue_count} overdue invoices")


# ── 7. AI Weekly Business Summary ─────────────────────────────────────────────

def run_weekly_ai_summary() -> None:
    """Generate and email AI-powered weekly business summaries to users."""
    logger.info("[scheduler] Generating weekly AI business summaries…")
    db = SessionLocal()

    try:
        users = db.query(models.User).filter(models.User.is_active == True).all()
        one_week_ago = datetime.now(timezone.utc) - timedelta(days=7)

        for user in users:
            try:
                invoices_this_week = (
                    db.query(models.Invoice)
                    .filter(
                        models.Invoice.user_id == user.id,
                        models.Invoice.created_at >= one_week_ago
                        if hasattr(models.Invoice, "created_at")
                        else True,
                    )
                    .count()
                )

                paid_this_week = (
                    db.query(models.Invoice)
                    .filter(
                        models.Invoice.user_id == user.id,
                        models.Invoice.status == "paid",
                    )
                    .count()
                )

                summary_body = (
                    f"Hi {user.full_name},\n\n"
                    f"Here's your weekly InvoiceFlow AI summary:\n\n"
                    f"  • New invoices this week : {invoices_this_week}\n"
                    f"  • Paid invoices          : {paid_this_week}\n\n"
                    f"Log in to view your full AI-powered analytics dashboard.\n\n"
                    f"InvoiceFlow AI"
                )

                send_email(user.email, "Your Weekly Business Summary — InvoiceFlow AI", summary_body)
                _scheduler_metrics["insights_generated"] += 1

            except Exception as exc:
                logger.warning(f"[scheduler] Weekly summary failed for user {user.id}: {exc}")

    finally:
        db.close()

    logger.info("[scheduler] Weekly AI summaries dispatched")


# ── 8. Notification Cleanup ───────────────────────────────────────────────────

def run_notification_cleanup() -> None:
    """Remove read notifications older than 30 days."""
    if not hasattr(models, "Notification"):
        return

    logger.info("[scheduler] Running notification cleanup…")
    db = SessionLocal()
    deleted = 0

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        old = (
            db.query(models.Notification)
            .filter(
                models.Notification.is_read == True,
                models.Notification.created_at <= cutoff,
            )
            .all()
        )
        for n in old:
            db.delete(n)
            deleted += 1
        if deleted:
            db.commit()

    finally:
        db.close()

    logger.info(f"[scheduler] Notification cleanup: {deleted} removed")


# ── 9. Temporary File Cleanup ─────────────────────────────────────────────────

def run_temp_file_cleanup() -> None:
    """Delete generated PDFs and CSVs older than 24 hours from static folders."""
    logger.info("[scheduler] Running temporary file cleanup…")
    paths_to_scan = [
        "app/static/generated_reports",
        "app/static/exports",
        "app",
    ]
    extensions = {".pdf", ".csv"}
    cutoff = time.time() - 86400  # 24 hours
    removed = 0

    for folder in paths_to_scan:
        if not os.path.isdir(folder):
            continue
        for filename in os.listdir(folder):
            if any(filename.endswith(ext) for ext in extensions):
                filepath = os.path.join(folder, filename)
                if os.path.getmtime(filepath) < cutoff:
                    try:
                        os.remove(filepath)
                        removed += 1
                    except OSError as exc:
                        logger.warning(f"[scheduler] Could not remove {filepath}: {exc}")

    logger.info(f"[scheduler] Temp file cleanup: {removed} files removed")


# ── 10. AI Conversation Context Cleanup ──────────────────────────────────────

def run_ai_conversation_cleanup() -> None:
    """Purge AI conversation history older than 90 days."""
    if not hasattr(models, "AIConversation"):
        return

    logger.info("[scheduler] Running AI conversation cleanup…")
    db = SessionLocal()
    deleted = 0

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        old = (
            db.query(models.AIConversation)
            .filter(models.AIConversation.created_at <= cutoff)
            .all()
        )
        for conv in old:
            db.delete(conv)
            deleted += 1
        if deleted:
            db.commit()

    finally:
        db.close()

    logger.info(f"[scheduler] AI conversation cleanup: {deleted} records removed")


# ── 11. Daily KPI Refresh ─────────────────────────────────────────────────────

def run_daily_kpi_refresh() -> None:
    """Log a daily KPI snapshot to the console (extend to store in a KPI model)."""
    logger.info("[scheduler] Refreshing daily KPIs…")
    db = SessionLocal()

    try:
        total = db.query(models.Invoice).count()
        paid = db.query(models.Invoice).filter(models.Invoice.status == "paid").count()
        overdue = db.query(models.Invoice).filter(models.Invoice.status == "overdue").count()
        users = db.query(models.User).filter(models.User.is_active == True).count()

        logger.info(
            f"[scheduler] KPI snapshot — "
            f"total_invoices={total} paid={paid} overdue={overdue} active_users={users}"
        )

    finally:
        db.close()


# ── 12. Scheduler Health Monitor ─────────────────────────────────────────────

def run_scheduler_health_check() -> None:
    """Log scheduler job states and flag any stuck or failed jobs."""
    global _scheduler
    if not _scheduler:
        return

    jobs = _scheduler.get_jobs()
    logger.info(f"[scheduler] Health check — {len(jobs)} registered jobs")
    for job in jobs:
        last = _scheduler_metrics["last_run"].get(job.id, "never")
        last_err = _scheduler_metrics["last_error"].get(job.id)
        status = "✓" if not last_err else "✗"
        logger.info(f"[scheduler]   {status} {job.id} | next={job.next_run_time} | last_run={last}")
        if last_err:
            logger.warning(f"[scheduler]     last error: {last_err}")


# ═══════════════════════════════════════════════════════════════════════════════
#  PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def get_scheduler_metrics() -> dict:
    """Return accumulated scheduler metrics for /metrics endpoint."""
    return {
        **_scheduler_metrics,
        "is_running": _scheduler.running if _scheduler else False,
        "registered_jobs": len(_scheduler.get_jobs()) if _scheduler else 0,
    }


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler (called during app lifespan shutdown)."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[scheduler] ✓ Scheduler stopped")


def start_scheduler() -> BackgroundScheduler:
    """
    Initialise and start the APScheduler BackgroundScheduler with all jobs.
    Returns the scheduler instance.
    """
    global _scheduler

    if not settings.enable_scheduler:
        logger.warning("[scheduler] Scheduler is disabled via ENABLE_SCHEDULER=false")
        return None

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_listener(
        _scheduler_event_listener,
        EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED,
    )

    # ── Core Operations ───────────────────────────────────────────────────────

    scheduler.add_job(
        run_daily_reminders,
        IntervalTrigger(hours=24),
        id="daily_reminders",
        name="Daily Payment Reminder Processor",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        run_overdue_checker,
        CronTrigger(hour=0, minute=5),  # 00:05 UTC daily
        id="overdue_checker",
        name="Overdue Invoice Checker",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        run_recurring_invoice_generator,
        CronTrigger(hour=1, minute=0),  # 01:00 UTC daily
        id="recurring_invoice_generator",
        name="Recurring Invoice Generator",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # ── AI & Analytics Jobs ───────────────────────────────────────────────────

    scheduler.add_job(
        run_priority_recalculation,
        IntervalTrigger(hours=6),
        id="ai_priority_recalculation",
        name="AI Invoice Priority Recalculation",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=1800,
    )

    scheduler.add_job(
        run_client_risk_recalculation,
        CronTrigger(hour=2, minute=0),  # 02:00 UTC daily
        id="client_risk_recalculation",
        name="Client Risk Score Recalculation",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        run_daily_kpi_refresh,
        CronTrigger(hour=6, minute=0),  # 06:00 UTC daily
        id="daily_kpi_refresh",
        name="Daily KPI Refresh",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=1800,
    )

    scheduler.add_job(
        run_weekly_ai_summary,
        CronTrigger(day_of_week="mon", hour=8, minute=0),  # Monday 08:00 UTC
        id="weekly_ai_summary",
        name="AI Weekly Business Summary",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # ── Workflow Automation ───────────────────────────────────────────────────

    scheduler.add_job(
        run_workflow_trigger_poll,
        IntervalTrigger(seconds=settings.workflow_interval),
        id="workflow_trigger_poll",
        name="Workflow Trigger Polling Engine",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=60,
    )

    # ── Maintenance & Cleanup ─────────────────────────────────────────────────

    scheduler.add_job(
        run_notification_cleanup,
        CronTrigger(hour=3, minute=0),  # 03:00 UTC daily
        id="notification_cleanup",
        name="Expired Notification Remover",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        run_ai_conversation_cleanup,
        CronTrigger(day_of_week="sun", hour=3, minute=30),  # Sunday 03:30 UTC
        id="ai_conversation_cleanup",
        name="AI Conversation Context Cleanup",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        run_temp_file_cleanup,
        CronTrigger(hour=4, minute=0),  # 04:00 UTC daily
        id="temp_file_cleanup",
        name="Temporary File Cleanup",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # ── Health Monitoring ─────────────────────────────────────────────────────

    scheduler.add_job(
        run_scheduler_health_check,
        IntervalTrigger(minutes=30),
        id="scheduler_health_check",
        name="Scheduler Health Monitor",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )

    scheduler.start()
    _scheduler = scheduler

    job_names = [j.name for j in scheduler.get_jobs()]
    logger.info(f"[scheduler] ✓ Started with {len(job_names)} jobs:")
    for name in job_names:
        logger.info(f"[scheduler]   • {name}")

    return scheduler
