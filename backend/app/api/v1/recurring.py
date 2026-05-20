from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models
from app.api.deps import get_db
from app.auth import get_current_user
from app.core.state import app_state
from app.schemas import RecurringBillingCreate, RecurringBillingResponse, InvoiceResponse

router = APIRouter(prefix="/recurring-billing", tags=["Workflows"])


@router.post("", response_model=RecurringBillingResponse)
def create_recurring_billing(
    data: RecurringBillingCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    new_plan = models.RecurringBilling(
        customer_id=data.customer_id,
        user_id=current_user.id,
        title=data.title,
        amount=data.amount,
        frequency=data.frequency,
        next_billing_date=data.next_billing_date,
        is_active=data.is_active,
    )
    db.add(new_plan)
    db.commit()
    db.refresh(new_plan)
    return new_plan


@router.get("", response_model=list[RecurringBillingResponse])
def get_recurring_billings(db: Session = Depends(get_db)):
    return db.query(models.RecurringBilling).all()


@router.get("/{plan_id}", response_model=RecurringBillingResponse)
def get_recurring_billing(plan_id: int, db: Session = Depends(get_db)):
    plan = db.query(models.RecurringBilling).filter(models.RecurringBilling.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Recurring billing plan not found")
    return plan


@router.put("/{plan_id}", response_model=RecurringBillingResponse)
def update_recurring_billing(plan_id: int, data: RecurringBillingCreate, db: Session = Depends(get_db)):
    plan = db.query(models.RecurringBilling).filter(models.RecurringBilling.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Recurring billing plan not found")
    plan.customer_id = data.customer_id
    plan.title = data.title
    plan.amount = data.amount
    plan.frequency = data.frequency
    plan.next_billing_date = data.next_billing_date
    plan.is_active = data.is_active
    db.commit()
    db.refresh(plan)
    return plan


@router.delete("/{plan_id}")
def delete_recurring_billing(plan_id: int, db: Session = Depends(get_db)):
    plan = db.query(models.RecurringBilling).filter(models.RecurringBilling.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Recurring billing plan not found")
    db.delete(plan)
    db.commit()
    return {"message": "Recurring billing plan deleted successfully"}


@router.post("/{plan_id}/generate-invoice", response_model=InvoiceResponse)
def generate_invoice_from_recurring(plan_id: int, db: Session = Depends(get_db)):
    plan = db.query(models.RecurringBilling).filter(models.RecurringBilling.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Recurring billing plan not found")
    if not plan.is_active:
        raise HTTPException(status_code=400, detail="Recurring billing plan is not active")
    invoice_number = f"REC-{plan.id}-{int(datetime.now().timestamp())}"
    new_invoice = models.Invoice(
        invoice_number=invoice_number,
        customer_id=plan.customer_id,
        user_id=plan.user_id,
        due_date=plan.next_billing_date,
        status="draft",
        total_amount=plan.amount,
        notes=f"Auto-generated from recurring billing plan: {plan.title}",
    )
    db.add(new_invoice)
    db.commit()
    db.refresh(new_invoice)
    app_state.record_workflow()
    return new_invoice
