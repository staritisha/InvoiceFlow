"""
app/routers/reports.py

AI-Powered Report Engine for InvoiceFlow AI Platform.
Covers multi-format report generation (PDF/CSV/Excel/JSON), AI narrative summaries,
invoice aging, tax, cash flow, business health, anomaly detection, background
processing with WebSocket progress updates, secure download, and report analytics.
"""

from __future__ import annotations

import csv
import io
import json
import os
import secrets
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, extract, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import ActivityType, ReportFormat, ReportType
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
    Report,
    User,
)
from app.schemas import ReportCreate
from app.services.ai_service import AIService
from app.services.analytics_service import AnalyticsService
from app.services.notification_service import NotificationService
from app.services.report_service import ReportService
from app.websocket.manager import ws_manager
from auth import get_current_user

router = APIRouter(prefix="/reports", tags=["Reports"])

ai_service = AIService()
analytics_service = AnalyticsService()
report_service = ReportService()
notification_service = NotificationService()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_REPORT_TYPES = {
    "financial": ["revenue", "profit_loss", "cash_flow", "taxes", "expenses"],
    "invoice":   ["overdue", "aging", "recurring", "paid_vs_unpaid"],
    "ai":        ["business_health", "revenue_forecast", "client_risk", "growth_prediction"],
    "team":      ["team_performance", "productivity", "activity_logs"],
}

SUPPORTED_FORMATS = ["pdf", "csv", "excel", "json"]

PDF_THEMES = ["startup", "enterprise", "minimal", "elegant", "dark", "futuristic"]

# Signed download token store (in-memory; replace with Redis in production)
_download_tokens: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _date_range_for_period(period: str) -> tuple[date, date]:
    today = date.today()
    if period == "this_month":
        return today.replace(day=1), today
    if period == "last_month":
        first = today.replace(day=1)
        last_day = first - timedelta(days=1)
        return last_day.replace(day=1), last_day
    if period == "this_quarter":
        q_start_month = ((today.month - 1) // 3) * 3 + 1
        return today.replace(month=q_start_month, day=1), today
    if period == "this_year":
        return today.replace(month=1, day=1), today
    if period == "last_30_days":
        return today - timedelta(days=30), today
    if period == "last_90_days":
        return today - timedelta(days=90), today
    if period == "last_12_months":
        return today - timedelta(days=365), today
    return today - timedelta(days=30), today


def _signed_download_url(report_id: UUID, base_url: str, ttl_minutes: int = 60) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + ttl_minutes * 60
    _download_tokens[token] = {
        "report_id": str(report_id),
        "expires_at": expires_at,
    }
    return f"{base_url}/api/reports/{report_id}/download?token={token}"


def _validate_download_token(token: str, report_id: UUID) -> bool:
    entry = _download_tokens.get(token)
    if not entry:
        return False
    if entry["report_id"] != str(report_id):
        return False
    if time.time() > entry["expires_at"]:
        _download_tokens.pop(token, None)
        return False
    return True


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
        entity_type="report",
        entity_id=entity_id,
        description=description,
        metadata=metadata or {},
        created_at=_utcnow(),
    ))


# ---------------------------------------------------------------------------
# GET /types/list  — All supported report types & formats
# ---------------------------------------------------------------------------


@router.get("/types/list")
async def list_report_types() -> dict:
    return {
        "report_types": SUPPORTED_REPORT_TYPES,
        "all_types": [t for types in SUPPORTED_REPORT_TYPES.values() for t in types],
        "formats": SUPPORTED_FORMATS,
        "pdf_themes": PDF_THEMES,
        "descriptions": {
            # Financial
            "revenue":             "Monthly/quarterly revenue breakdown with growth trends",
            "profit_loss":         "Income vs. expenses P&L statement",
            "cash_flow":           "Cash in/out timeline with AI forecast",
            "taxes":               "GST/VAT tax totals, quarterly summaries, accounting export",
            "expenses":            "Categorized expense breakdown with AI cost-cutting suggestions",
            # Invoice
            "overdue":             "All overdue invoices with days late and risk warnings",
            "aging":               "Invoice aging buckets: current, 30, 60, 90+ days",
            "recurring":           "Recurring invoice performance, MRR/ARR, churn",
            "paid_vs_unpaid":      "Collection efficiency across all invoices",
            # AI
            "business_health":     "AI 0–100 health score across 8 business dimensions",
            "revenue_forecast":    "AI monthly/quarterly revenue predictions with confidence",
            "client_risk":         "AI risk scores for all clients with recommended actions",
            "growth_prediction":   "Startup-style growth trajectory and runway estimation",
            # Team
            "team_performance":    "Invoice creation, reminders sent, and workflow activity per user",
            "productivity":        "Time-to-payment, automation savings, efficiency KPIs",
            "activity_logs":       "Full audit trail of all team actions",
        },
    }


# ---------------------------------------------------------------------------
# GET /  — Paginated report list
# ---------------------------------------------------------------------------


@router.get("/")
async def list_reports(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, max_length=200),
    report_type: Optional[str] = Query(None),
    report_format: Optional[str] = Query(None),
    sort_by: str = Query("newest", regex="^(newest|oldest|most_downloaded)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    offset = (page - 1) * page_size

    stmt = select(Report).where(Report.team_id == current_user.team_id)

    if search:
        stmt = stmt.where(Report.title.ilike(f"%{search}%"))
    if report_type:
        stmt = stmt.where(Report.type == report_type)
    if report_format:
        stmt = stmt.where(Report.format == report_format)

    sort_map = {
        "newest": desc(Report.id),
        "oldest": Report.id,
    }
    stmt = stmt.order_by(sort_map.get(sort_by, desc(Report.id)))

    total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one() or 0)
    reports = (await db.execute(stmt.offset(offset).limit(page_size))).scalars().all()

    domain = os.getenv("REPLIT_DEV_DOMAIN", "localhost")
    base_url = f"https://{domain}"

    return {
        "items": [
            {
                "id": str(r.id),
                "type": r.type,
                "format": r.format,
                "title": r.title,
                "url": r.url,
                "filters": r.filters,
                "created_by": str(r.created_by) if r.created_by else None,
                "download_url": _signed_download_url(r.id, base_url, ttl_minutes=15),
            }
            for r in reports
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": -(-total // page_size),
    }


# ---------------------------------------------------------------------------
# POST /  — Generate report (async background)
# ---------------------------------------------------------------------------


@router.post("/", status_code=status.HTTP_202_ACCEPTED)
async def generate_report(
    payload: ReportCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    require_permission(current_user, "reports:create")

    all_types = [t for types in SUPPORTED_REPORT_TYPES.values() for t in types]
    if payload.type not in all_types:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported report type '{payload.type}'. See /reports/types/list.",
        )
    if payload.format not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported format '{payload.format}'. Choose: {SUPPORTED_FORMATS}",
        )

    # Create placeholder record immediately
    period = getattr(payload, "period", "last_30_days") or "last_30_days"
    theme = getattr(payload, "theme", "startup") or "startup"
    start, end = _date_range_for_period(period)

    report = Report(
        team_id=current_user.team_id,
        type=payload.type,
        format=payload.format,
        title=payload.title or _auto_title(payload.type, period),
        url=None,  # filled once generated
        filters={
            "period": period,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "theme": theme,
            **(payload.filters or {}),
        },
        created_by=current_user.id,
    )
    db.add(report)
    await db.flush()
    report_id = report.id
    await db.commit()

    # Broadcast: generation started
    await ws_manager.broadcast_to_team(
        str(current_user.team_id),
        {"event": "REPORT_GENERATING", "report_id": str(report_id), "type": payload.type, "format": payload.format},
    )

    background_tasks.add_task(
        _generate_report_bg,
        report_id=report_id,
        report_type=payload.type,
        report_format=payload.format,
        team_id=current_user.team_id,
        user_id=current_user.id,
        filters=report.filters,
        theme=theme,
    )

    return {
        "report_id": str(report_id),
        "status": "generating",
        "type": payload.type,
        "format": payload.format,
        "title": report.title,
        "message": "Report is being generated. Watch for REPORT_COMPLETED via WebSocket.",
        "estimated_seconds": _estimated_generation_time(payload.type, payload.format),
    }


def _auto_title(report_type: str, period: str) -> str:
    label = report_type.replace("_", " ").title()
    now = datetime.now().strftime("%B %Y")
    return f"{label} Report — {now}"


def _estimated_generation_time(report_type: str, fmt: str) -> int:
    base = {"pdf": 8, "excel": 5, "csv": 2, "json": 1}.get(fmt, 5)
    if report_type in ("business_health", "revenue_forecast", "client_risk"):
        base += 5  # AI-heavy reports take longer
    return base


async def _generate_report_bg(
    report_id: UUID,
    report_type: str,
    report_format: str,
    team_id: UUID,
    user_id: UUID,
    filters: dict,
    theme: str,
) -> None:
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        try:
            start = date.fromisoformat(filters.get("start", str(date.today() - timedelta(days=30))))
            end = date.fromisoformat(filters.get("end", str(date.today())))

            await ws_manager.broadcast_to_team(str(team_id), {
                "event": "REPORT_PROGRESS", "report_id": str(report_id), "step": "Gathering data…"
            })

            # Collect data for this report type
            data = await _collect_report_data(db, report_type=report_type, team_id=team_id, start=start, end=end)

            await ws_manager.broadcast_to_team(str(team_id), {
                "event": "REPORT_PROGRESS", "report_id": str(report_id), "step": "AI generating insights…"
            })

            # AI narrative + insight cards
            ai_narrative = await ai_service.generate_report_narrative(
                report_type=report_type,
                data=data,
                period=f"{start} to {end}",
            )
            data["ai_narrative"] = ai_narrative

            await ws_manager.broadcast_to_team(str(team_id), {
                "event": "REPORT_PROGRESS", "report_id": str(report_id),
                "step": f"Building {report_format.upper()}…"
            })

            # Generate file in requested format
            file_bytes, mime_type, filename = await _render_report(
                report_type=report_type,
                fmt=report_format,
                data=data,
                theme=theme,
                team_id=team_id,
            )

            # Store file (object storage / local static)
            file_url = await report_service.store_file(
                file_bytes=file_bytes,
                filename=filename,
                team_id=str(team_id),
                report_id=str(report_id),
            )

            # Update Report record
            await db.execute(
                update(Report)
                .where(Report.id == report_id)
                .values(url=file_url)
            )

            # AI Insight card
            if ai_narrative.get("key_insight"):
                db.add(BusinessInsight(
                    team_id=team_id,
                    type="report_insight",
                    title=f"{report_type.replace('_', ' ').title()} Report Ready",
                    content=ai_narrative.get("key_insight", ""),
                    severity="info",
                    category="analytics",
                    is_read=False,
                    ai_generated=True,
                    metadata={"report_id": str(report_id), "type": report_type},
                ))

            await db.commit()

            await ws_manager.broadcast_to_team(str(team_id), {
                "event": "REPORT_COMPLETED",
                "report_id": str(report_id),
                "type": report_type,
                "format": report_format,
                "file_url": file_url,
                "ai_summary": ai_narrative.get("executive_summary", ""),
            })

        except Exception as exc:
            await db.execute(
                update(Report).where(Report.id == report_id).values(url="__failed__")
            )
            await db.commit()
            await ws_manager.broadcast_to_team(str(team_id), {
                "event": "REPORT_FAILED", "report_id": str(report_id), "error": str(exc)
            })


async def _collect_report_data(
    db: AsyncSession,
    report_type: str,
    team_id: UUID,
    start: date,
    end: date,
) -> dict:
    """Collect raw DB data for the given report type."""
    data: dict = {"report_type": report_type, "period_start": str(start), "period_end": str(end)}

    # ---- Revenue data ----
    if report_type in ("revenue", "profit_loss", "cash_flow", "business_health",
                       "revenue_forecast", "growth_prediction", "paid_vs_unpaid"):
        rev_stmt = select(
            func.coalesce(func.sum(Invoice.total), 0).label("total"),
            func.coalesce(func.sum(Invoice.amount_paid), 0).label("paid"),
            func.coalesce(func.sum(Invoice.balance_due), 0).label("outstanding"),
            func.count(Invoice.id).label("count"),
        ).where(Invoice.team_id == team_id, Invoice.issue_date >= start, Invoice.issue_date <= end)
        rev = (await db.execute(rev_stmt)).mappings().one()

        monthly_stmt = (
            select(
                extract("year", Invoice.issue_date).label("y"),
                extract("month", Invoice.issue_date).label("m"),
                func.sum(Invoice.total).label("revenue"),
                func.sum(Invoice.amount_paid).label("collected"),
                func.count(Invoice.id).label("count"),
            )
            .where(Invoice.team_id == team_id, Invoice.issue_date >= start, Invoice.issue_date <= end)
            .group_by("y", "m")
            .order_by("y", "m")
        )
        monthly_rows = (await db.execute(monthly_stmt)).mappings().all()

        data["revenue"] = {
            "total": float(rev["total"]),
            "collected": float(rev["paid"]),
            "outstanding": float(rev["outstanding"]),
            "invoice_count": int(rev["count"]),
            "collection_rate": round(float(rev["paid"]) / float(rev["total"]) * 100, 2) if float(rev["total"]) > 0 else 0,
            "monthly_trend": [
                {
                    "month": f"{int(r['y'])}-{int(r['m']):02d}",
                    "revenue": float(r["revenue"] or 0),
                    "collected": float(r["collected"] or 0),
                    "count": int(r["count"]),
                }
                for r in monthly_rows
            ],
        }

    # ---- Invoice aging ----
    if report_type in ("aging", "overdue"):
        today = date.today()
        buckets = {"current": [], "30_days": [], "60_days": [], "90_plus": []}
        overdue_stmt = (
            select(Invoice.id, Invoice.number, Invoice.due_date, Invoice.balance_due,
                   Invoice.client_id, Client.name.label("client_name"))
            .join(Client, Invoice.client_id == Client.id, isouter=True)
            .where(Invoice.team_id == team_id, Invoice.balance_due > 0)
        )
        overdue_rows = (await db.execute(overdue_stmt)).all()
        for row in overdue_rows:
            days = (today - row[2]).days if row[2] else 0
            entry = {
                "id": str(row[0]), "number": row[1],
                "days_overdue": max(0, days),
                "balance": float(row[3] or 0),
                "client": row[5],
            }
            if days <= 0:
                buckets["current"].append(entry)
            elif days <= 30:
                buckets["30_days"].append(entry)
            elif days <= 60:
                buckets["60_days"].append(entry)
            else:
                buckets["90_plus"].append(entry)

        data["aging"] = {
            "buckets": buckets,
            "totals": {k: round(sum(i["balance"] for i in v), 2) for k, v in buckets.items()},
        }

    # ---- Expenses ----
    if report_type in ("expenses", "profit_loss"):
        exp_stmt = (
            select(
                Expense.category,
                func.sum(Expense.amount).label("total"),
                func.count(Expense.id).label("count"),
            )
            .where(Expense.team_id == team_id, Expense.date >= start, Expense.date <= end)
            .group_by(Expense.category)
            .order_by(desc("total"))
        )
        exp_rows = (await db.execute(exp_stmt)).mappings().all()
        data["expenses"] = {
            "by_category": [
                {"category": r["category"], "total": float(r["total"] or 0), "count": int(r["count"])}
                for r in exp_rows
            ],
            "total": sum(float(r["total"] or 0) for r in exp_rows),
        }

    # ---- Tax summary ----
    if report_type == "taxes":
        tax_stmt = select(
            func.sum(Invoice.tax_amount).label("total_tax"),
            func.sum(Invoice.total).label("gross"),
            func.avg(Invoice.tax_rate).label("avg_rate"),
        ).where(Invoice.team_id == team_id, Invoice.issue_date >= start, Invoice.issue_date <= end)
        tax_row = (await db.execute(tax_stmt)).mappings().one()
        data["taxes"] = {
            "total_tax_collected": float(tax_row["total_tax"] or 0),
            "gross_revenue": float(tax_row["gross"] or 0),
            "avg_tax_rate": round(float(tax_row["avg_rate"] or 0), 2),
        }

    # ---- Client risk ----
    if report_type == "client_risk":
        risk_stmt = (
            select(Client.id, Client.name, Client.risk_score, Client.total_invoiced,
                   Client.total_paid, Client.average_days_to_pay)
            .where(Client.team_id == team_id, Client.is_active.is_(True))
            .order_by(desc(Client.risk_score))
        )
        risk_rows = (await db.execute(risk_stmt)).all()
        data["clients"] = [
            {
                "id": str(r[0]), "name": r[1], "risk_score": r[2] or 0,
                "total_invoiced": float(r[3] or 0), "total_paid": float(r[4] or 0),
                "avg_days": round(float(r[5] or 0), 1),
            }
            for r in risk_rows
        ]

    return data


async def _render_report(
    report_type: str,
    fmt: str,
    data: dict,
    theme: str,
    team_id: UUID,
) -> tuple[bytes, str, str]:
    """Render report data into the requested format, return (bytes, mime, filename)."""
    now_str = datetime.now().strftime("%Y%m%d_%H%M")
    base_name = f"{report_type}_{now_str}"
    narrative = data.get("ai_narrative", {})

    if fmt == "json":
        payload = json.dumps(data, indent=2, default=str)
        return payload.encode("utf-8"), "application/json", f"{base_name}.json"

    if fmt == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        # Write AI summary at top
        writer.writerow(["# AI Executive Summary"])
        writer.writerow([narrative.get("executive_summary", "")])
        writer.writerow([])
        # Write main data rows
        _csv_write_section(writer, "Revenue", data.get("revenue", {}))
        _csv_write_section(writer, "Aging Buckets", data.get("aging", {}).get("totals", {}))
        _csv_write_section(writer, "Expenses by Category", {
            r["category"]: r["total"] for r in data.get("expenses", {}).get("by_category", [])
        })
        _csv_write_section(writer, "Tax Summary", data.get("taxes", {}))
        return output.getvalue().encode("utf-8"), "text/csv", f"{base_name}.csv"

    if fmt == "excel":
        excel_bytes = await report_service.render_excel(
            report_type=report_type,
            data=data,
            narrative=narrative,
        )
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return excel_bytes, mime, f"{base_name}.xlsx"

    if fmt == "pdf":
        pdf_bytes = await report_service.render_pdf(
            report_type=report_type,
            data=data,
            narrative=narrative,
            theme=theme,
        )
        return pdf_bytes, "application/pdf", f"{base_name}.pdf"

    raise ValueError(f"Unknown format: {fmt}")


def _csv_write_section(writer: Any, title: str, section: dict) -> None:
    if not section:
        return
    writer.writerow([f"# {title}"])
    writer.writerow(["Key", "Value"])
    for k, v in section.items():
        writer.writerow([k, v])
    writer.writerow([])


# ---------------------------------------------------------------------------
# GET /{id}  — Full report metadata
# ---------------------------------------------------------------------------


@router.get("/{report_id}")
async def get_report(
    report_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = select(Report).where(
        Report.id == report_id, Report.team_id == current_user.team_id
    )
    result = await db.execute(stmt)
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found.")

    domain = os.getenv("REPLIT_DEV_DOMAIN", "localhost")
    base_url = f"https://{domain}"
    download_url = _signed_download_url(report.id, base_url, ttl_minutes=60)

    status_label = "completed"
    if report.url is None:
        status_label = "generating"
    elif report.url == "__failed__":
        status_label = "failed"

    return {
        "id": str(report.id),
        "type": report.type,
        "format": report.format,
        "title": report.title,
        "status": status_label,
        "filters": report.filters,
        "created_by": str(report.created_by) if report.created_by else None,
        "file_url": report.url if status_label == "completed" else None,
        "download_url": download_url if status_label == "completed" else None,
        "share_link": f"{base_url}/api/reports/{report.id}/download",
        "pdf_theme": (report.filters or {}).get("theme", "startup"),
        "ai_summary": None,  # regenerated on demand via /ai-summary endpoint
    }


# ---------------------------------------------------------------------------
# GET /{id}/download  — Secure streaming download
# ---------------------------------------------------------------------------


@router.get("/{report_id}/download")
async def download_report(
    report_id: UUID,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    stmt = select(Report).where(
        Report.id == report_id, Report.team_id == current_user.team_id
    )
    report = (await db.execute(stmt)).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found.")
    if not report.url or report.url == "__failed__":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Report not yet ready.")

    # Validate signed token if provided (public / shareable links)
    if token and not _validate_download_token(token, report_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or expired download token.")

    # Fetch file bytes from storage
    file_bytes, mime_type = await report_service.fetch_file(
        file_url=report.url,
        report_format=report.format,
    )

    ext = {"pdf": "pdf", "csv": "csv", "excel": "xlsx", "json": "json"}.get(report.format, "bin")
    filename = f"{report.title or report.type}_{report_id}.{ext}".replace(" ", "_")

    await _log_activity(
        db,
        team_id=current_user.team_id,
        user_id=current_user.id,
        action_type=ActivityType.downloaded,
        entity_id=report.id,
        description=f"Report '{report.title}' downloaded",
        metadata={"format": report.format, "token_used": bool(token)},
    )
    await db.commit()

    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type=mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(file_bytes)),
        },
    )


# ---------------------------------------------------------------------------
# DELETE /{id}  — Soft / permanent delete
# ---------------------------------------------------------------------------


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(
    report_id: UUID,
    permanent: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    stmt = select(Report).where(
        Report.id == report_id, Report.team_id == current_user.team_id
    )
    report = (await db.execute(stmt)).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found.")

    # Admins can permanently delete; others get soft-delete via null URL
    if permanent and current_user.is_superuser:
        # Clean up stored file
        if report.url and report.url not in (None, "__failed__"):
            await report_service.delete_file(report.url)
        await db.delete(report)
    else:
        # Soft delete: mark URL as deleted
        report.url = "__deleted__"

    await _log_activity(
        db,
        team_id=current_user.team_id,
        user_id=current_user.id,
        action_type=ActivityType.deleted,
        entity_id=report.id,
        description=f"Report '{report.title}' {'permanently deleted' if permanent else 'archived'}",
    )
    await db.commit()


# ---------------------------------------------------------------------------
# GET /{id}/ai-summary  — On-demand AI narrative for a completed report
# ---------------------------------------------------------------------------


@router.get("/{report_id}/ai-summary")
async def report_ai_summary(
    report_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = select(Report).where(
        Report.id == report_id, Report.team_id == current_user.team_id
    )
    report = (await db.execute(stmt)).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found.")

    filters = report.filters or {}
    start = date.fromisoformat(filters.get("start", str(date.today() - timedelta(days=30))))
    end = date.fromisoformat(filters.get("end", str(date.today())))

    data = await _collect_report_data(
        db, report_type=report.type, team_id=current_user.team_id, start=start, end=end
    )
    narrative = await ai_service.generate_report_narrative(
        report_type=report.type, data=data, period=f"{start} to {end}"
    )

    return {
        "report_id": str(report.id),
        "report_type": report.type,
        "executive_summary": narrative.get("executive_summary", ""),
        "key_insights": narrative.get("key_insights", []),
        "warnings": narrative.get("warnings", []),
        "opportunities": narrative.get("opportunities", []),
        "ai_recommendations": narrative.get("recommendations", []),
        "insight_cards": narrative.get("insight_cards", []),
        "kpis": narrative.get("kpis", {}),
        "anomalies_detected": narrative.get("anomalies", []),
        "generated_at": _utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# POST /executive-brief  — One-click AI executive brief
# ---------------------------------------------------------------------------


@router.post("/executive-brief", status_code=status.HTTP_202_ACCEPTED)
async def executive_brief(
    period: str = Query("this_month"),
    fmt: str = Query("pdf", regex="^(pdf|json)$"),
    theme: str = Query("startup"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    One-click "Explain my business performance this month" report.
    Combines: revenue, aging, client risk, health score, cash flow forecast
    into a single executive-level AI brief.
    """
    start, end = _date_range_for_period(period)

    report = Report(
        team_id=current_user.team_id,
        type="business_health",
        format=fmt,
        title=f"Executive Brief — {datetime.now().strftime('%B %Y')}",
        url=None,
        filters={"period": period, "start": str(start), "end": str(end), "theme": theme},
        created_by=current_user.id,
    )
    db.add(report)
    await db.flush()
    report_id = report.id
    await db.commit()

    await ws_manager.broadcast_to_team(
        str(current_user.team_id),
        {"event": "REPORT_GENERATING", "report_id": str(report_id), "type": "executive_brief"},
    )

    background_tasks.add_task(
        _generate_report_bg,
        report_id=report_id,
        report_type="business_health",
        report_format=fmt,
        team_id=current_user.team_id,
        user_id=current_user.id,
        filters=report.filters,
        theme=theme,
    )

    return {
        "report_id": str(report_id),
        "status": "generating",
        "title": report.title,
        "message": "Executive AI brief is being generated. Watch REPORT_COMPLETED via WebSocket.",
        "estimated_seconds": 15,
    }


# ---------------------------------------------------------------------------
# POST /investor-mode  — Startup investor-ready report
# ---------------------------------------------------------------------------


@router.post("/investor-mode", status_code=status.HTTP_202_ACCEPTED)
async def investor_report(
    period: str = Query("this_year"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Generates a startup-style investor report:
    ARR/MRR, revenue growth, churn, runway estimate, AI growth forecast.
    Output: PDF with investor-grade styling.
    """
    start, end = _date_range_for_period(period)
    report = Report(
        team_id=current_user.team_id,
        type="growth_prediction",
        format="pdf",
        title=f"Investor Report — {datetime.now().strftime('%Y')}",
        url=None,
        filters={"period": period, "start": str(start), "end": str(end), "theme": "enterprise", "investor_mode": True},
        created_by=current_user.id,
    )
    db.add(report)
    await db.flush()
    report_id = report.id
    await db.commit()

    background_tasks.add_task(
        _generate_report_bg,
        report_id=report_id,
        report_type="growth_prediction",
        report_format="pdf",
        team_id=current_user.team_id,
        user_id=current_user.id,
        filters=report.filters,
        theme="enterprise",
    )

    return {
        "report_id": str(report_id),
        "status": "generating",
        "title": report.title,
        "message": "Investor-mode report generating. Includes ARR/MRR, growth, runway, and AI forecast.",
        "estimated_seconds": 20,
    }
