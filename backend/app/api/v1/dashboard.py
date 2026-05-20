from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import extract, func
from sqlalchemy.orm import Session

from app import models
from app.api.deps import get_db

router = APIRouter(prefix="/dashboard", tags=["Analytics"])


@router.get("/summary")
def dashboard_summary(db: Session = Depends(get_db)):
    total_customers = db.query(models.Customer).count()
    total_invoices = db.query(models.Invoice).count()
    paid_invoices = db.query(models.Invoice).filter(models.Invoice.status == "paid").count()
    draft_invoices = db.query(models.Invoice).filter(models.Invoice.status == "draft").count()
    overdue_invoices = db.query(models.Invoice).filter(models.Invoice.status == "overdue").count()
    total_revenue_amount = sum(
        i.total_amount for i in db.query(models.Invoice).filter(models.Invoice.status == "paid").all()
    )
    unpaid_amount = sum(
        i.total_amount for i in db.query(models.Invoice).filter(models.Invoice.status != "paid").all()
    )
    return {
        "total_customers": total_customers,
        "total_invoices": total_invoices,
        "paid_invoices": paid_invoices,
        "draft_invoices": draft_invoices,
        "overdue_invoices": overdue_invoices,
        "total_revenue": total_revenue_amount,
        "unpaid_amount": unpaid_amount,
    }


@router.get("/monthly-revenue")
def monthly_revenue(db: Session = Depends(get_db)):
    results = (
        db.query(
            extract("month", models.Invoice.issue_date).label("month"),
            func.sum(models.Invoice.total_amount).label("amount"),
        )
        .filter(
            models.Invoice.status == "paid",
            extract("year", models.Invoice.issue_date) == datetime.now().year,
        )
        .group_by("month")
        .order_by("month")
        .all()
    )
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return [
        {"month": month_names[int(r.month) - 1], "amount": float(r.amount or 0)}
        for r in results
    ]
