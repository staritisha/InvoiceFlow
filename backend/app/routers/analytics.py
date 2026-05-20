"""
app/routers/analytics.py

Advanced Analytics Router for InvoiceFlow AI Platform.
Covers revenue analytics, late payments, recurring revenue, health scores,
weekly summaries, cash flow forecasting, KPIs, insights, heatmaps,
financial trend detection, and real-time WebSocket updates.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy import Integer, cast, desc, extract, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    ActivityType,
    InsightType,
    NotificationType,
    ReportFormat,
    ReportType,
)
from app.core.permissions import require_permission
from app.database import get_db
from app.models import (
    Activity,
    BusinessInsight,
    Client,
    Expense,
    Invoice,
    InvoiceStatus,
    Payment,
    RecurringInvoice,
    Reminder,
    User,
)
from app.services.ai_service import AIService
from app.services.analytics_service import AnalyticsService
from app.services.notification_service import NotificationService
from app.websocket.manager import ws_manager
from auth import get_current_user

router = APIRouter(prefix="/analytics", tags=["Analytics"])

ai_service = AIService()
analytics_service = AnalyticsService()
notification_service = NotificationService()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _date_range(period: str) -> tuple[date, date]:
    """Return (start, end) dates for common period strings."""
    today = date.today()
    if period == "this_month":
        start = today.replace(day=1)
        return start, today
    if period == "last_month":
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return last_prev.replace(day=1), last_prev
    if period == "this_quarter":
        quarter_start_month = ((today.month - 1) // 3) * 3 + 1
        return today.replace(month=quarter_start_month, day=1), today
    if period == "this_year":
        return today.replace(month=1, day=1), today
    if period == "last_30_days":
        return today - timedelta(days=30), today
    if period == "last_90_days":
        return today - timedelta(days=90), today
    if period == "last_12_months":
        return today - timedelta(days=365), today
    # default: last 30 days
    return today - timedelta(days=30), today


def _growth_pct(current: float, previous: float) -> float:
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round((current - previous) / previous * 100, 2)


# ---------------------------------------------------------------------------
# GET /revenue  — Main revenue analytics dashboard
# ---------------------------------------------------------------------------


@router.get("/revenue")
async def revenue_analytics(
    period: str = Query("last_30_days", regex="^(this_month|last_month|this_quarter|this_year|last_30_days|last_90_days|last_12_months)$"),
    compare_period: Optional[str] = Query(None),
    currency: Optional[str] = Query(None, max_length=3),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    start, end = _date_range(period)

    base_filter = [
        Invoice.team_id == current_user.team_id,
        Invoice.issue_date >= start,
        Invoice.issue_date <= end,
    ]
    if currency:
        base_filter.append(Invoice.currency == currency.upper())

    # --- Core revenue aggregates ---
    agg_stmt = select(
        func.coalesce(func.sum(Invoice.total), 0).label("total_revenue"),
        func.coalesce(func.sum(Invoice.amount_paid), 0).label("collected"),
        func.coalesce(func.sum(Invoice.balance_due), 0).label("outstanding"),
        func.count(Invoice.id).label("invoice_count"),
    ).where(*base_filter)
    agg = (await db.execute(agg_stmt)).mappings().one()

    overdue_stmt = select(
        func.coalesce(func.sum(Invoice.balance_due), 0).label("overdue_revenue"),
        func.count(Invoice.id).label("overdue_count"),
    ).where(
        Invoice.team_id == current_user.team_id,
        Invoice.status == InvoiceStatus.overdue,
        Invoice.issue_date >= start,
        Invoice.issue_date <= end,
    )
    overdue = (await db.execute(overdue_stmt)).mappings().one()

    # --- Compare period ---
    growth_pct: Optional[float] = None
    if compare_period:
        cstart, cend = _date_range(compare_period)
        prev_stmt = select(
            func.coalesce(func.sum(Invoice.total), 0).label("total")
        ).where(
            Invoice.team_id == current_user.team_id,
            Invoice.issue_date >= cstart,
            Invoice.issue_date <= cend,
        )
        prev_total = (await db.execute(prev_stmt)).scalar_one()
        growth_pct = _growth_pct(float(agg["total_revenue"]), float(prev_total))

    # --- Monthly revenue trend (last 12 months for charts) ---
    monthly_stmt = (
        select(
            extract("year", Invoice.issue_date).label("year"),
            extract("month", Invoice.issue_date).label("month"),
            func.sum(Invoice.total).label("revenue"),
            func.sum(Invoice.amount_paid).label("collected"),
            func.count(Invoice.id).label("count"),
        )
        .where(
            Invoice.team_id == current_user.team_id,
            Invoice.issue_date >= date.today() - timedelta(days=365),
        )
        .group_by("year", "month")
        .order_by("year", "month")
    )
    monthly_rows = (await db.execute(monthly_stmt)).mappings().all()
    monthly_trend = [
        {
            "month": f"{int(r['year'])}-{int(r['month']):02d}",
            "revenue": float(r["revenue"] or 0),
            "collected": float(r["collected"] or 0),
            "invoice_count": int(r["count"]),
        }
        for r in monthly_rows
    ]

    # --- Revenue by client (top 10) ---
    by_client_stmt = (
        select(
            Client.id,
            Client.name,
            Client.company,
            func.sum(Invoice.total).label("revenue"),
            func.count(Invoice.id).label("invoice_count"),
        )
        .join(Invoice, Invoice.client_id == Client.id)
        .where(*base_filter)
        .group_by(Client.id, Client.name, Client.company)
        .order_by(desc("revenue"))
        .limit(10)
    )
    by_client_rows = (await db.execute(by_client_stmt)).mappings().all()
    revenue_by_client = [
        {
            "client_id": str(r["id"]),
            "name": r["name"],
            "company": r["company"],
            "revenue": float(r["revenue"] or 0),
            "invoice_count": int(r["invoice_count"]),
        }
        for r in by_client_rows
    ]

    # --- Revenue by currency ---
    by_currency_stmt = (
        select(
            Invoice.currency,
            func.sum(Invoice.total).label("revenue"),
            func.count(Invoice.id).label("count"),
        )
        .where(Invoice.team_id == current_user.team_id, Invoice.issue_date >= start)
        .group_by(Invoice.currency)
        .order_by(desc("revenue"))
    )
    by_currency_rows = (await db.execute(by_currency_stmt)).mappings().all()
    revenue_by_currency = [
        {"currency": r["currency"], "revenue": float(r["revenue"] or 0), "count": int(r["count"])}
        for r in by_currency_rows
    ]

    # --- Revenue by source (manual / voice / ai) ---
    by_source_stmt = (
        select(
            Invoice.source,
            func.sum(Invoice.total).label("revenue"),
            func.count(Invoice.id).label("count"),
        )
        .where(*base_filter)
        .group_by(Invoice.source)
    )
    by_source_rows = (await db.execute(by_source_stmt)).mappings().all()
    revenue_by_source = [
        {"source": r["source"] or "manual", "revenue": float(r["revenue"] or 0), "count": int(r["count"])}
        for r in by_source_rows
    ]

    # --- AI-generated revenue insights ---
    ai_insights = await ai_service.generate_revenue_insights(
        total_revenue=float(agg["total_revenue"]),
        collected=float(agg["collected"]),
        outstanding=float(agg["outstanding"]),
        overdue=float(overdue["overdue_revenue"]),
        growth_pct=growth_pct,
        monthly_trend=monthly_trend,
    )

    return {
        "period": period,
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "totals": {
            "total_revenue": float(agg["total_revenue"]),
            "collected_revenue": float(agg["collected"]),
            "outstanding_revenue": float(agg["outstanding"]),
            "overdue_revenue": float(overdue["overdue_revenue"]),
            "overdue_invoice_count": int(overdue["overdue_count"]),
            "total_invoice_count": int(agg["invoice_count"]),
            "collection_rate_pct": round(
                float(agg["collected"]) / float(agg["total_revenue"]) * 100, 2
            ) if float(agg["total_revenue"]) > 0 else 0.0,
            "revenue_growth_pct": growth_pct,
        },
        "charts": {
            "monthly_trend": monthly_trend,
            "by_client": revenue_by_client,
            "by_currency": revenue_by_currency,
            "by_source": revenue_by_source,
        },
        "ai_insights": ai_insights,
    }


# ---------------------------------------------------------------------------
# GET /late-payments  — Late payment analytics
# ---------------------------------------------------------------------------


@router.get("/late-payments")
async def late_payment_analytics(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    today = date.today()

    # All overdue invoices with client info
    overdue_stmt = (
        select(Invoice, Client.name.label("client_name"), Client.email.label("client_email"))
        .join(Client, Invoice.client_id == Client.id)
        .where(
            Invoice.team_id == current_user.team_id,
            Invoice.status == InvoiceStatus.overdue,
        )
        .order_by(Invoice.due_date)
    )
    overdue_rows = (await db.execute(overdue_stmt)).all()
    overdue_invoices = [row[0] for row in overdue_rows]

    days_late_list: list[int] = []
    client_overdue: dict[str, dict] = {}
    for row in overdue_rows:
        inv, cname, cemail = row
        if inv.due_date:
            days = (today - inv.due_date).days
            days_late_list.append(max(0, days))
        cid = str(inv.client_id)
        if cid not in client_overdue:
            client_overdue[cid] = {
                "client_id": cid,
                "name": cname,
                "email": cemail,
                "overdue_count": 0,
                "overdue_amount": 0.0,
                "max_days_late": 0,
            }
        client_overdue[cid]["overdue_count"] += 1
        client_overdue[cid]["overdue_amount"] += float(inv.balance_due or 0)
        if inv.due_date:
            days = (today - inv.due_date).days
            client_overdue[cid]["max_days_late"] = max(
                client_overdue[cid]["max_days_late"], max(0, days)
            )

    avg_days_late = sum(days_late_list) / len(days_late_list) if days_late_list else 0.0

    # Late payment trends — last 6 months
    monthly_late: dict[str, int] = defaultdict(int)
    for inv in overdue_invoices:
        if inv.due_date:
            key = inv.due_date.strftime("%Y-%m")
            monthly_late[key] += 1
    late_trend = [{"month": k, "overdue_count": v} for k, v in sorted(monthly_late.items())[-6:]]

    # Reminder effectiveness
    sent_stmt = select(func.sum(Invoice.reminders_sent)).where(
        Invoice.team_id == current_user.team_id
    )
    total_reminders = float((await db.execute(sent_stmt)).scalar_one() or 0)
    paid_after_reminder_stmt = select(func.count(Invoice.id)).where(
        Invoice.team_id == current_user.team_id,
        Invoice.status == InvoiceStatus.paid,
        Invoice.reminders_sent > 0,
    )
    paid_after_reminder = int((await db.execute(paid_after_reminder_stmt)).scalar_one() or 0)
    reminder_effectiveness = (
        round(paid_after_reminder / total_reminders * 100, 1) if total_reminders > 0 else 0.0
    )

    # AI outputs
    ai_output = await ai_service.analyze_late_payments(
        avg_days_late=avg_days_late,
        overdue_clients=list(client_overdue.values()),
        total_overdue=sum(d["overdue_amount"] for d in client_overdue.values()),
        late_trend=late_trend,
        reminder_effectiveness=reminder_effectiveness,
    )

    return {
        "summary": {
            "total_overdue_invoices": len(overdue_invoices),
            "total_overdue_amount": sum(float(i.balance_due or 0) for i in overdue_invoices),
            "avg_days_late": round(avg_days_late, 1),
            "reminder_effectiveness_pct": reminder_effectiveness,
        },
        "most_overdue_clients": sorted(
            client_overdue.values(), key=lambda x: x["overdue_amount"], reverse=True
        )[:10],
        "late_payment_trend": late_trend,
        "ai": {
            "clients_likely_to_delay": ai_output.get("at_risk_clients", []),
            "best_reminder_timing": ai_output.get("best_reminder_timing", ""),
            "recommended_collection_actions": ai_output.get("collection_actions", []),
            "high_risk_invoice_alerts": ai_output.get("high_risk_alerts", []),
            "collection_difficulty_score": ai_output.get("difficulty_score", 0),
            "escalation_recommendations": ai_output.get("escalation_recs", []),
        },
    }


# ---------------------------------------------------------------------------
# GET /recurring-revenue  — MRR / ARR analytics
# ---------------------------------------------------------------------------


@router.get("/recurring-revenue")
async def recurring_revenue_analytics(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    today = date.today()

    # Active recurring invoices
    rec_stmt = select(RecurringInvoice).where(
        RecurringInvoice.is_active.is_(True),
        RecurringInvoice.created_by == current_user.id,
    )
    rec_result = await db.execute(rec_stmt)
    recurring = rec_result.scalars().all()

    # MRR — sum monthly recurring amounts
    # Get template invoices to read totals
    template_ids = [r.template_invoice_id for r in recurring]
    mrr: float = 0.0
    if template_ids:
        tpl_stmt = select(Invoice.id, Invoice.total, Invoice.is_recurring).where(
            Invoice.id.in_(template_ids)
        )
        tpl_rows = (await db.execute(tpl_stmt)).all()
        tpl_map = {str(r[0]): float(r[1] or 0) for r in tpl_rows}
        for rec in recurring:
            amount = tpl_map.get(str(rec.template_invoice_id), 0.0)
            freq = (rec.frequency or "monthly").lower()
            if freq == "weekly":
                mrr += amount * 4.33
            elif freq == "biweekly":
                mrr += amount * 2.17
            elif freq == "monthly":
                mrr += amount
            elif freq == "quarterly":
                mrr += amount / 3
            elif freq == "yearly":
                mrr += amount / 12

    arr = mrr * 12

    # Churn: inactive recurring invoices stopped in last 90 days
    churn_stmt = select(func.count(RecurringInvoice.id)).where(
        RecurringInvoice.is_active.is_(False),
        RecurringInvoice.created_by == current_user.id,
    )
    churned_count = int((await db.execute(churn_stmt)).scalar_one() or 0)
    total_count = len(recurring) + churned_count
    churn_rate = round(churned_count / total_count * 100, 2) if total_count > 0 else 0.0

    # Recurring invoice success rate (last 90 days)
    success_stmt = select(func.count(Invoice.id)).where(
        Invoice.team_id == current_user.team_id,
        Invoice.is_recurring.is_(True),
        Invoice.status == InvoiceStatus.paid,
        Invoice.issue_date >= today - timedelta(days=90),
    )
    failed_stmt = select(func.count(Invoice.id)).where(
        Invoice.team_id == current_user.team_id,
        Invoice.is_recurring.is_(True),
        Invoice.status == InvoiceStatus.overdue,
        Invoice.issue_date >= today - timedelta(days=90),
    )
    success_count = int((await db.execute(success_stmt)).scalar_one() or 0)
    failed_count = int((await db.execute(failed_stmt)).scalar_one() or 0)
    total_rec = success_count + failed_count
    success_rate = round(success_count / total_rec * 100, 2) if total_rec > 0 else 100.0

    # AI churn prediction
    ai_output = await ai_service.predict_churn(
        mrr=mrr,
        arr=arr,
        churn_rate=churn_rate,
        success_rate=success_rate,
        active_recurring=len(recurring),
    )

    return {
        "mrr": round(mrr, 2),
        "arr": round(arr, 2),
        "churn_rate_pct": churn_rate,
        "active_recurring_subscriptions": len(recurring),
        "churned_subscriptions": churned_count,
        "recurring_success_rate_pct": success_rate,
        "failed_recurring_count": failed_count,
        "revenue_stability_score": round(100 - churn_rate, 1),
        "ai": {
            "churn_prediction": ai_output.get("churn_prediction", ""),
            "forecasted_mrr_next_month": ai_output.get("forecasted_mrr", mrr),
            "growth_recommendation": ai_output.get("growth_recommendation", ""),
            "at_risk_subscriptions": ai_output.get("at_risk", []),
        },
        "charts": {
            "growth_trend": ai_output.get("growth_trend", []),
            "forecast_curve": ai_output.get("forecast_curve", []),
        },
    }


# ---------------------------------------------------------------------------
# GET /health-score  — AI business health score
# ---------------------------------------------------------------------------


@router.get("/health-score")
async def business_health_score(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    today = date.today()
    period_start = today - timedelta(days=90)

    # Revenue metrics
    rev_stmt = select(
        func.coalesce(func.sum(Invoice.total), 0).label("total"),
        func.coalesce(func.sum(Invoice.amount_paid), 0).label("paid"),
        func.coalesce(func.sum(Invoice.balance_due), 0).label("outstanding"),
        func.count(Invoice.id).label("count"),
    ).where(
        Invoice.team_id == current_user.team_id,
        Invoice.issue_date >= period_start,
    )
    rev = (await db.execute(rev_stmt)).mappings().one()

    # Overdue ratio
    overdue_count_stmt = select(func.count(Invoice.id)).where(
        Invoice.team_id == current_user.team_id,
        Invoice.status == InvoiceStatus.overdue,
    )
    overdue_count = int((await db.execute(overdue_count_stmt)).scalar_one() or 0)
    overdue_ratio = overdue_count / max(int(rev["count"]), 1) * 100

    # Client reliability: avg payment behavior score
    client_rel_stmt = select(func.avg(Client.payment_behavior_score)).where(
        Client.team_id == current_user.team_id,
        Client.is_active.is_(True),
    )
    client_reliability = float((await db.execute(client_rel_stmt)).scalar_one() or 50)

    # Collection efficiency
    collection_rate = (
        float(rev["paid"]) / float(rev["total"]) * 100 if float(rev["total"]) > 0 else 0.0
    )

    # DSO (Days Sales Outstanding)
    avg_days_stmt = select(func.avg(Client.average_days_to_pay)).where(
        Client.team_id == current_user.team_id
    )
    dso = float((await db.execute(avg_days_stmt)).scalar_one() or 30)

    inputs = {
        "total_revenue": float(rev["total"]),
        "collection_rate": collection_rate,
        "overdue_ratio": overdue_ratio,
        "client_reliability": client_reliability,
        "dso": dso,
        "outstanding": float(rev["outstanding"]),
    }

    ai_output = await ai_service.calculate_health_score(inputs)

    return {
        "health_score": ai_output.get("health_score", 0),
        "risk_level": ai_output.get("risk_level", "medium"),
        "growth_potential": ai_output.get("growth_potential", "moderate"),
        "stability_analysis": ai_output.get("stability_analysis", ""),
        "weakest_areas": ai_output.get("weakest_areas", []),
        "recommended_improvements": ai_output.get("improvements", []),
        "optimization_tips": ai_output.get("optimization_tips", []),
        "metrics_used": {
            "collection_rate_pct": round(collection_rate, 1),
            "overdue_ratio_pct": round(overdue_ratio, 1),
            "client_reliability_score": round(client_reliability, 1),
            "days_sales_outstanding": round(dso, 1),
        },
    }


# ---------------------------------------------------------------------------
# GET /weekly-summary  — AI-generated executive weekly summary
# ---------------------------------------------------------------------------


@router.get("/weekly-summary")
async def weekly_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    today = date.today()
    week_start = today - timedelta(days=7)

    # Revenue this week
    rev_stmt = select(
        func.coalesce(func.sum(Invoice.total), 0),
        func.coalesce(func.sum(Invoice.amount_paid), 0),
        func.count(Invoice.id),
    ).where(
        Invoice.team_id == current_user.team_id,
        Invoice.issue_date >= week_start,
    )
    rev_row = (await db.execute(rev_stmt)).one()
    weekly_revenue, weekly_collected, weekly_invoice_count = (
        float(rev_row[0]), float(rev_row[1]), int(rev_row[2])
    )

    # New clients
    new_clients_stmt = select(func.count(Client.id)).where(
        Client.team_id == current_user.team_id,
        Client.created_at >= week_start,
    )
    new_clients = int((await db.execute(new_clients_stmt)).scalar_one() or 0)

    # Payments received
    payments_stmt = select(
        func.count(Payment.id), func.coalesce(func.sum(Payment.amount), 0)
    ).join(Invoice, Payment.invoice_id == Invoice.id).where(
        Invoice.team_id == current_user.team_id,
        Payment.paid_at >= week_start,
    )
    pay_row = (await db.execute(payments_stmt)).one()
    payment_count, payment_total = int(pay_row[0]), float(pay_row[1])

    # Overdue invoices
    overdue_stmt = select(func.count(Invoice.id), func.coalesce(func.sum(Invoice.balance_due), 0)).where(
        Invoice.team_id == current_user.team_id,
        Invoice.status == InvoiceStatus.overdue,
    )
    ov_row = (await db.execute(overdue_stmt)).one()
    overdue_count, overdue_amount = int(ov_row[0]), float(ov_row[1])

    # Top performing clients this week
    top_clients_stmt = (
        select(Client.name, func.sum(Invoice.amount_paid).label("paid"))
        .join(Invoice, Invoice.client_id == Client.id)
        .where(
            Invoice.team_id == current_user.team_id,
            Invoice.issue_date >= week_start,
        )
        .group_by(Client.name)
        .order_by(desc("paid"))
        .limit(5)
    )
    top_clients_rows = (await db.execute(top_clients_stmt)).all()
    top_clients = [{"name": r[0], "paid": float(r[1] or 0)} for r in top_clients_rows]

    summary_data = {
        "weekly_revenue": weekly_revenue,
        "weekly_collected": weekly_collected,
        "weekly_invoice_count": weekly_invoice_count,
        "new_clients": new_clients,
        "payment_count": payment_count,
        "payment_total": payment_total,
        "overdue_count": overdue_count,
        "overdue_amount": overdue_amount,
        "top_clients": top_clients,
    }

    ai_summary = await ai_service.generate_weekly_summary(summary_data)

    return {
        "period": {"start": week_start.isoformat(), "end": today.isoformat()},
        "metrics": summary_data,
        "ai": {
            "ceo_summary": ai_summary.get("ceo_summary", ""),
            "action_items": ai_summary.get("action_items", []),
            "growth_suggestions": ai_summary.get("growth_suggestions", []),
            "follow_up_priorities": ai_summary.get("follow_up_priorities", []),
            "risks_detected": ai_summary.get("risks", []),
            "opportunities": ai_summary.get("opportunities", []),
            "insights": ai_summary.get("insights", []),
        },
    }


# ---------------------------------------------------------------------------
# GET /cash-flow-forecast  — Predictive cash flow
# ---------------------------------------------------------------------------


@router.get("/cash-flow-forecast")
async def cash_flow_forecast(
    months_ahead: int = Query(3, ge=1, le=12),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    today = date.today()

    # Upcoming expected payments from open invoices
    open_stmt = (
        select(Invoice.due_date, Invoice.balance_due, Invoice.client_id)
        .where(
            Invoice.team_id == current_user.team_id,
            Invoice.status.in_([InvoiceStatus.sent, InvoiceStatus.overdue]),
            Invoice.balance_due > 0,
        )
        .order_by(Invoice.due_date)
    )
    open_rows = (await db.execute(open_stmt)).all()

    # Historical monthly cash in (last 12 months)
    hist_stmt = (
        select(
            extract("year", Invoice.issue_date).label("y"),
            extract("month", Invoice.issue_date).label("m"),
            func.sum(Invoice.amount_paid).label("cash_in"),
            func.sum(Invoice.total).label("invoiced"),
        )
        .where(
            Invoice.team_id == current_user.team_id,
            Invoice.issue_date >= today - timedelta(days=365),
        )
        .group_by("y", "m")
        .order_by("y", "m")
    )
    hist_rows = (await db.execute(hist_stmt)).mappings().all()
    historical = [
        {
            "month": f"{int(r['y'])}-{int(r['m']):02d}",
            "cash_in": float(r["cash_in"] or 0),
            "invoiced": float(r["invoiced"] or 0),
        }
        for r in hist_rows
    ]

    # Group upcoming by month
    upcoming_by_month: dict[str, float] = defaultdict(float)
    for due_date, balance, _ in open_rows:
        if due_date:
            key = due_date.strftime("%Y-%m")
            upcoming_by_month[key] += float(balance or 0)

    ai_forecast = await ai_service.forecast_cash_flow(
        historical=historical,
        upcoming_by_month=dict(upcoming_by_month),
        months_ahead=months_ahead,
    )

    return {
        "historical_cash_flow": historical,
        "upcoming_expected": [
            {"month": k, "expected": v} for k, v in sorted(upcoming_by_month.items())
        ],
        "forecast": {
            "months": ai_forecast.get("monthly_projections", []),
            "best_case_total": ai_forecast.get("best_case", 0),
            "worst_case_total": ai_forecast.get("worst_case", 0),
            "expected_total": ai_forecast.get("expected_total", 0),
            "confidence_intervals": ai_forecast.get("confidence_intervals", []),
            "financial_runway_days": ai_forecast.get("runway_days", None),
        },
        "ai": {
            "seasonal_pattern": ai_forecast.get("seasonal_pattern", ""),
            "risk_factors": ai_forecast.get("risk_factors", []),
            "optimization_suggestions": ai_forecast.get("suggestions", []),
        },
    }


# ---------------------------------------------------------------------------
# GET /kpis  — Real-time KPI tracking
# ---------------------------------------------------------------------------


@router.get("/kpis")
async def get_kpis(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    today = date.today()
    this_month_start = today.replace(day=1)
    last_month_end = this_month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    async def _rev(start: date, end: date) -> dict:
        r = (
            await db.execute(
                select(
                    func.coalesce(func.sum(Invoice.total), 0).label("total"),
                    func.coalesce(func.sum(Invoice.amount_paid), 0).label("paid"),
                    func.count(Invoice.id).label("count"),
                ).where(
                    Invoice.team_id == current_user.team_id,
                    Invoice.issue_date >= start,
                    Invoice.issue_date <= end,
                )
            )
        ).mappings().one()
        return {"total": float(r["total"]), "paid": float(r["paid"]), "count": int(r["count"])}

    this_month = await _rev(this_month_start, today)
    last_month = await _rev(last_month_start, last_month_end)

    # DSO
    dso_stmt = select(func.avg(Client.average_days_to_pay)).where(
        Client.team_id == current_user.team_id
    )
    dso = float((await db.execute(dso_stmt)).scalar_one() or 0)

    # Active clients
    active_clients_stmt = select(func.count(Client.id)).where(
        Client.team_id == current_user.team_id,
        Client.is_active.is_(True),
    )
    active_clients = int((await db.execute(active_clients_stmt)).scalar_one() or 0)

    # Outstanding total
    outstanding_stmt = select(func.coalesce(func.sum(Invoice.balance_due), 0)).where(
        Invoice.team_id == current_user.team_id,
        Invoice.status.in_([InvoiceStatus.sent, InvoiceStatus.overdue]),
    )
    outstanding = float((await db.execute(outstanding_stmt)).scalar_one() or 0)

    # Overdue count
    overdue_count_stmt = select(func.count(Invoice.id)).where(
        Invoice.team_id == current_user.team_id,
        Invoice.status == InvoiceStatus.overdue,
    )
    overdue_count = int((await db.execute(overdue_count_stmt)).scalar_one() or 0)

    # Average invoice value
    avg_val_stmt = select(func.avg(Invoice.total)).where(
        Invoice.team_id == current_user.team_id
    )
    avg_invoice_value = float((await db.execute(avg_val_stmt)).scalar_one() or 0)

    collection_rate = (
        this_month["paid"] / this_month["total"] * 100 if this_month["total"] > 0 else 0.0
    )
    revenue_growth = _growth_pct(this_month["total"], last_month["total"])

    kpis = {
        "total_invoices": this_month["count"],
        "paid_invoices": int(
            (
                await db.execute(
                    select(func.count(Invoice.id)).where(
                        Invoice.team_id == current_user.team_id,
                        Invoice.status == InvoiceStatus.paid,
                        Invoice.issue_date >= this_month_start,
                    )
                )
            ).scalar_one()
            or 0
        ),
        "outstanding_balance": outstanding,
        "collection_rate_pct": round(collection_rate, 2),
        "days_sales_outstanding": round(dso, 1),
        "avg_invoice_value": round(avg_invoice_value, 2),
        "revenue_growth_pct": revenue_growth,
        "active_clients": active_clients,
        "overdue_invoice_count": overdue_count,
        "this_month_revenue": this_month["total"],
        "last_month_revenue": last_month["total"],
        "this_month_collected": this_month["paid"],
        "refreshed_at": _utcnow().isoformat(),
    }

    # Broadcast KPI refresh via WebSocket
    await ws_manager.broadcast_to_team(
        str(current_user.team_id),
        {"event": "kpi_refreshed", "kpis": kpis},
    )

    return kpis


# ---------------------------------------------------------------------------
# GET /top-clients  — Top clients analytics
# ---------------------------------------------------------------------------


@router.get("/top-clients")
async def top_clients_analytics(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    # Highest revenue clients
    revenue_stmt = (
        select(
            Client.id,
            Client.name,
            Client.company,
            Client.risk_score,
            Client.payment_behavior_score,
            Client.average_days_to_pay,
            Client.total_invoiced,
            Client.total_paid,
            func.count(Invoice.id).label("invoice_count"),
        )
        .join(Invoice, Invoice.client_id == Client.id, isouter=True)
        .where(Client.team_id == current_user.team_id, Client.is_active.is_(True))
        .group_by(
            Client.id, Client.name, Client.company,
            Client.risk_score, Client.payment_behavior_score,
            Client.average_days_to_pay, Client.total_invoiced, Client.total_paid,
        )
        .order_by(desc(Client.total_invoiced))
        .limit(limit)
    )
    rows = (await db.execute(revenue_stmt)).mappings().all()

    def _row(r: Any) -> dict:
        ltv = float(r["total_invoiced"] or 0)
        return {
            "client_id": str(r["id"]),
            "name": r["name"],
            "company": r["company"],
            "total_revenue": float(r["total_invoiced"] or 0),
            "total_paid": float(r["total_paid"] or 0),
            "outstanding": float((r["total_invoiced"] or 0) - (r["total_paid"] or 0)),
            "invoice_count": int(r["invoice_count"]),
            "avg_payment_days": round(float(r["average_days_to_pay"] or 0), 1),
            "risk_score": r["risk_score"] or 0,
            "payment_reliability": r["payment_behavior_score"] or 50,
            "lifetime_value": ltv,
        }

    by_revenue = [_row(r) for r in rows]

    # Fastest paying
    fast_stmt = (
        select(
            Client.id, Client.name, Client.company,
            Client.average_days_to_pay, Client.total_invoiced,
            Client.total_paid, Client.risk_score,
            Client.payment_behavior_score,
            func.count(Invoice.id).label("invoice_count"),
        )
        .join(Invoice, Invoice.client_id == Client.id, isouter=True)
        .where(
            Client.team_id == current_user.team_id,
            Client.is_active.is_(True),
            Client.average_days_to_pay.isnot(None),
        )
        .group_by(
            Client.id, Client.name, Client.company,
            Client.average_days_to_pay, Client.total_invoiced,
            Client.total_paid, Client.risk_score, Client.payment_behavior_score,
        )
        .order_by(Client.average_days_to_pay)
        .limit(limit)
    )
    fast_rows = (await db.execute(fast_stmt)).mappings().all()
    by_speed = [_row(r) for r in fast_rows]

    return {
        "by_revenue": by_revenue,
        "by_payment_speed": by_speed,
        "by_reliability": sorted(by_revenue, key=lambda x: x["payment_reliability"], reverse=True),
        "by_lifetime_value": sorted(by_revenue, key=lambda x: x["lifetime_value"], reverse=True),
        "by_risk": sorted(by_revenue, key=lambda x: x["risk_score"], reverse=True),
    }


# ---------------------------------------------------------------------------
# GET /insights  — AI business insights feed
# ---------------------------------------------------------------------------


@router.get("/insights")
async def get_insights(
    category: Optional[str] = Query(None),
    unread_only: bool = Query(False),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = (
        select(BusinessInsight)
        .where(BusinessInsight.team_id == current_user.team_id)
        .order_by(desc(BusinessInsight.id))
        .limit(limit)
    )
    if category:
        stmt = stmt.where(BusinessInsight.category == category)
    if unread_only:
        stmt = stmt.where(BusinessInsight.is_read.is_(False))

    result = await db.execute(stmt)
    insights = result.scalars().all()

    return {
        "insights": [
            {
                "id": str(i.id),
                "type": i.type,
                "title": i.title,
                "content": i.content,
                "severity": i.severity,
                "category": i.category,
                "is_read": i.is_read,
                "ai_generated": i.ai_generated,
                "metadata": i.metadata,
            }
            for i in insights
        ],
        "unread_count": sum(1 for i in insights if not i.is_read),
        "total": len(insights),
        "categories": list({i.category for i in insights if i.category}),
    }


# ---------------------------------------------------------------------------
# POST /insights/generate  — Force-generate fresh AI insights
# ---------------------------------------------------------------------------


@router.post("/insights/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate_insights(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    require_permission(current_user, "analytics:insights:generate")

    background_tasks.add_task(
        _generate_insights_bg,
        team_id=current_user.team_id,
        user_id=current_user.id,
    )
    return {"status": "generating", "message": "AI is analyzing your business data. Insights will appear shortly."}


async def _generate_insights_bg(team_id: UUID, user_id: UUID) -> None:
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        # Gather business context
        rev_stmt = select(
            func.coalesce(func.sum(Invoice.total), 0).label("total"),
            func.coalesce(func.sum(Invoice.amount_paid), 0).label("paid"),
            func.coalesce(func.sum(Invoice.balance_due), 0).label("outstanding"),
            func.count(Invoice.id).label("count"),
        ).where(
            Invoice.team_id == team_id,
            Invoice.issue_date >= date.today() - timedelta(days=90),
        )
        rev = (await db.execute(rev_stmt)).mappings().one()

        overdue_count_q = select(func.count(Invoice.id)).where(
            Invoice.team_id == team_id,
            Invoice.status == InvoiceStatus.overdue,
        )
        overdue_count = int((await db.execute(overdue_count_q)).scalar_one() or 0)

        client_count_q = select(func.count(Client.id)).where(
            Client.team_id == team_id, Client.is_active.is_(True)
        )
        client_count = int((await db.execute(client_count_q)).scalar_one() or 0)

        context = {
            "total_revenue": float(rev["total"]),
            "collected": float(rev["paid"]),
            "outstanding": float(rev["outstanding"]),
            "invoice_count": int(rev["count"]),
            "overdue_count": overdue_count,
            "client_count": client_count,
        }

        generated = await ai_service.generate_business_insights(context)

        for item in generated:
            insight = BusinessInsight(
                team_id=team_id,
                type=item.get("type", "general"),
                title=item.get("title", ""),
                content=item.get("content", ""),
                severity=item.get("severity", "info"),
                category=item.get("category", "general"),
                is_read=False,
                ai_generated=True,
                metadata=item,
            )
            db.add(insight)

        await db.commit()

        await ws_manager.broadcast_to_team(
            str(team_id),
            {"event": "insights_generated", "count": len(generated)},
        )


# ---------------------------------------------------------------------------
# GET /revenue-forecast  — Advanced AI revenue forecasting
# ---------------------------------------------------------------------------


@router.get("/revenue-forecast")
async def revenue_forecast(
    horizon: str = Query("quarterly", regex="^(monthly|quarterly|yearly)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    # Historical monthly revenue (last 18 months)
    hist_stmt = (
        select(
            extract("year", Invoice.issue_date).label("y"),
            extract("month", Invoice.issue_date).label("m"),
            func.sum(Invoice.total).label("revenue"),
        )
        .where(
            Invoice.team_id == current_user.team_id,
            Invoice.issue_date >= date.today() - timedelta(days=548),
        )
        .group_by("y", "m")
        .order_by("y", "m")
    )
    hist_rows = (await db.execute(hist_stmt)).mappings().all()
    historical = [
        {"month": f"{int(r['y'])}-{int(r['m']):02d}", "revenue": float(r["revenue"] or 0)}
        for r in hist_rows
    ]

    periods = {"monthly": 1, "quarterly": 3, "yearly": 12}
    months_ahead = periods[horizon]

    forecast = await ai_service.forecast_revenue(
        historical=historical,
        months_ahead=months_ahead,
        horizon=horizon,
    )

    return {
        "horizon": horizon,
        "historical": historical,
        "forecast": {
            "trajectory": forecast.get("trajectory", []),
            "growth_probability_pct": forecast.get("growth_probability", 0),
            "confidence_score": forecast.get("confidence", 0),
            "expected_revenue": forecast.get("expected_revenue", 0),
            "best_case": forecast.get("best_case", 0),
            "worst_case": forecast.get("worst_case", 0),
        },
        "ai": {
            "trend_summary": forecast.get("trend_summary", ""),
            "seasonal_pattern": forecast.get("seasonal_pattern", ""),
            "financial_risk_warnings": forecast.get("risk_warnings", []),
            "scaling_recommendations": forecast.get("scaling_recs", []),
        },
    }


# ---------------------------------------------------------------------------
# GET /heatmaps/payments  — Payment activity heatmaps
# ---------------------------------------------------------------------------


@router.get("/heatmaps/payments")
async def payment_heatmaps(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    # Payments grouped by day-of-week and day-of-month (last 12 months)
    pay_stmt = (
        select(Payment.paid_at, Payment.amount)
        .join(Invoice, Payment.invoice_id == Invoice.id)
        .where(
            Invoice.team_id == current_user.team_id,
            Payment.paid_at >= _utcnow() - timedelta(days=365),
        )
    )
    pay_rows = (await db.execute(pay_stmt)).all()

    by_weekday: dict[int, float] = defaultdict(float)
    by_day_of_month: dict[int, float] = defaultdict(float)
    by_month: dict[str, float] = defaultdict(float)
    by_date: dict[str, float] = defaultdict(float)

    for paid_at, amount in pay_rows:
        if paid_at:
            by_weekday[paid_at.weekday()] += float(amount or 0)
            by_day_of_month[paid_at.day] += float(amount or 0)
            by_month[paid_at.strftime("%Y-%m")] += float(amount or 0)
            by_date[paid_at.strftime("%Y-%m-%d")] += float(amount or 0)

    weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    return {
        "by_weekday": [
            {"day": weekday_names[d], "day_index": d, "total_payments": round(v, 2)}
            for d, v in sorted(by_weekday.items())
        ],
        "by_day_of_month": [
            {"day": d, "total_payments": round(v, 2)}
            for d, v in sorted(by_day_of_month.items())
        ],
        "by_month": [
            {"month": m, "total_payments": round(v, 2)}
            for m, v in sorted(by_month.items())
        ],
        "calendar_heatmap": [
            {"date": d, "value": round(v, 2)}
            for d, v in sorted(by_date.items())
        ],
        "best_payment_day": max(by_weekday, key=by_weekday.get, default=None),
        "worst_collection_day": min(by_weekday, key=by_weekday.get, default=None),
    }


# ---------------------------------------------------------------------------
# GET /trends/financial  — AI financial trend detection
# ---------------------------------------------------------------------------


@router.get("/trends/financial")
async def financial_trends(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    # Monthly revenue (last 12 months)
    monthly_stmt = (
        select(
            extract("year", Invoice.issue_date).label("y"),
            extract("month", Invoice.issue_date).label("m"),
            func.sum(Invoice.total).label("revenue"),
            func.sum(Invoice.amount_paid).label("collected"),
            func.count(Invoice.id).label("count"),
        )
        .where(
            Invoice.team_id == current_user.team_id,
            Invoice.issue_date >= date.today() - timedelta(days=365),
        )
        .group_by("y", "m")
        .order_by("y", "m")
    )
    monthly_rows = (await db.execute(monthly_stmt)).mappings().all()
    monthly_data = [
        {
            "month": f"{int(r['y'])}-{int(r['m']):02d}",
            "revenue": float(r["revenue"] or 0),
            "collected": float(r["collected"] or 0),
            "invoice_count": int(r["count"]),
        }
        for r in monthly_rows
    ]

    # Client churn trend
    new_clients_stmt = (
        select(
            extract("year", Client.created_at).label("y"),
            extract("month", Client.created_at).label("m"),
            func.count(Client.id).label("count"),
        )
        .where(
            Client.team_id == current_user.team_id,
            Client.created_at >= _utcnow() - timedelta(days=365),
        )
        .group_by("y", "m")
        .order_by("y", "m")
    )
    client_rows = (await db.execute(new_clients_stmt)).mappings().all()
    client_trend = [
        {"month": f"{int(r['y'])}-{int(r['m']):02d}", "new_clients": int(r["count"])}
        for r in client_rows
    ]

    ai_trends = await ai_service.detect_financial_trends(
        monthly_revenue=monthly_data,
        client_growth=client_trend,
    )

    return {
        "monthly_data": monthly_data,
        "client_growth_trend": client_trend,
        "ai_detected_trends": [
            {
                "trend_name": t.get("name", ""),
                "description": t.get("description", ""),
                "severity": t.get("severity", "info"),
                "forecast_impact": t.get("forecast_impact", ""),
                "suggested_actions": t.get("actions", []),
            }
            for t in ai_trends.get("trends", [])
        ],
        "anomalies": ai_trends.get("anomalies", []),
        "growth_signals": ai_trends.get("growth_signals", []),
        "decline_warnings": ai_trends.get("decline_warnings", []),
        "overall_trend_direction": ai_trends.get("direction", "stable"),
    }
