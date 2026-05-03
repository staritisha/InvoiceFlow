from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, timedelta, timezone
from app.database import engine, Base, SessionLocal
from app import models
from app.schemas import UserCreate, UserResponse, Token, CustomerCreate, CustomerResponse, InvoiceCreate, InvoiceResponse, InvoiceStatusUpdate, ReminderResponse, RecurringBillingCreate, RecurringBillingResponse, InvoiceItemCreate, InvoiceItemResponse, AIFollowupResponse
from app.auth import hash_password, verify_password, create_access_token, get_current_user
from fastapi.responses import FileResponse
import csv
from app.scheduler import start_scheduler
Base.metadata.create_all(bind=engine)

app = FastAPI(title="InvoiceFlow API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/")
def root():
    return {"message": "InvoiceFlow running"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/db-check")
def db_check():
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1"))
        return {"database": "connected", "result": result.scalar()}


@app.post("/auth/register", response_model=UserResponse)
def register_user(user: UserCreate, db: Session = Depends(get_db)):
    existing_user = db.query(models.User).filter(models.User.email == user.email).first()

    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    new_user = models.User(
        full_name=user.full_name,
        email=user.email,
        hashed_password=hash_password(user.password),
        role="admin",
        is_active=True
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return new_user


@app.post("/auth/login", response_model=Token)
def login_user(user: UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.email == user.email).first()

    if not db_user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not verify_password(user.password, db_user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token({"sub": db_user.email, "role": db_user.role})

    return {
        "access_token": token,
        "token_type": "bearer"
    }

@app.post("/customers", response_model=CustomerResponse)
def create_customer(customer: CustomerCreate, db: Session = Depends(get_db)):
    new_customer = models.Customer(
        name=customer.name,
        email=customer.email,
        phone=customer.phone,
        address=customer.address,
        user_id=1
    )

    db.add(new_customer)
    db.commit()
    db.refresh(new_customer)

    return new_customer

@app.get("/customers", response_model=list[CustomerResponse])
def get_customers(db: Session = Depends(get_db)):
    customers = db.query(models.Customer).all()
    return customers

@app.get("/customers/{customer_id}", response_model=CustomerResponse)
def get_customer(customer_id: int, db: Session = Depends(get_db)):
    customer = db.query(models.Customer).filter(models.Customer.id == customer_id).first()

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    return customer

@app.put("/customers/{customer_id}", response_model=CustomerResponse)
def update_customer(customer_id: int, customer: CustomerCreate, db: Session = Depends(get_db)):
    db_customer = db.query(models.Customer).filter(models.Customer.id == customer_id).first()

    if not db_customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    db_customer.name = customer.name
    db_customer.email = customer.email
    db_customer.phone = customer.phone
    db_customer.address = customer.address

    db.commit()
    db.refresh(db_customer)

    return db_customer

@app.delete("/customers/{customer_id}")
def delete_customer(customer_id: int, db: Session = Depends(get_db)):
    customer = db.query(models.Customer).filter(models.Customer.id == customer_id).first()

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    db.delete(customer)
    db.commit()

    return {"message": "Customer deleted successfully"}

@app.post("/invoices", response_model=InvoiceResponse)
def create_invoice(invoice: InvoiceCreate, db: Session = Depends(get_db)):
    new_invoice = models.Invoice(
        invoice_number=invoice.invoice_number,
        customer_id=invoice.customer_id,
        user_id=1,
        due_date=invoice.due_date,
        status=invoice.status,
        total_amount=invoice.total_amount,
        notes=invoice.notes
    )

    db.add(new_invoice)
    db.commit()
    db.refresh(new_invoice)

    return new_invoice


# Get all invoices
@app.get("/invoices", response_model=list[InvoiceResponse])
def get_invoices(db: Session = Depends(get_db)):
    return db.query(models.Invoice).all()


# Get single invoice
@app.get("/invoices/{invoice_id}", response_model=InvoiceResponse)
def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()

    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    return invoice


# Update invoice
@app.put("/invoices/{invoice_id}", response_model=InvoiceResponse)
def update_invoice(invoice_id: int, invoice: InvoiceCreate, db: Session = Depends(get_db)):
    db_invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()

    if not db_invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    db_invoice.invoice_number = invoice.invoice_number
    db_invoice.customer_id = invoice.customer_id
    db_invoice.due_date = invoice.due_date
    db_invoice.status = invoice.status
    db_invoice.total_amount = invoice.total_amount
    db_invoice.notes = invoice.notes

    db.commit()
    db.refresh(db_invoice)

    return db_invoice


# Delete invoice
@app.delete("/invoices/{invoice_id}")
def delete_invoice(invoice_id: int, db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()

    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    db.delete(invoice)
    db.commit()

    return {"message": "Invoice deleted successfully"}

from fastapi.responses import FileResponse
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import os


@app.get("/invoices/{invoice_id}/pdf")
def generate_invoice_pdf(invoice_id: int, db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()

    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    file_name = f"invoice_{invoice_id}.pdf"
    file_path = os.path.join("app", file_name)

    c = canvas.Canvas(file_path, pagesize=letter)

    # Title
    c.setFont("Helvetica-Bold", 16)
    c.drawString(200, 750, "INVOICE")

    # Invoice details
    c.setFont("Helvetica", 12)
    c.drawString(50, 700, f"Invoice Number: {invoice.invoice_number}")
    c.drawString(50, 680, f"Customer ID: {invoice.customer_id}")
    c.drawString(50, 660, f"Status: {invoice.status}")
    c.drawString(50, 640, f"Total Amount: ₹{invoice.total_amount}")

    # Footer
    c.drawString(50, 600, f"Notes: {invoice.notes or ''}")

    c.save()

    return FileResponse(file_path, media_type="application/pdf", filename=file_name)



@app.patch("/invoices/{invoice_id}/status", response_model=InvoiceResponse)
def update_invoice_status(invoice_id: int, status_update: InvoiceStatusUpdate, db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()

    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    invoice.status = status_update.status

    db.commit()
    db.refresh(invoice)

    return invoice

@app.get("/dashboard/summary")
def dashboard_summary(db: Session = Depends(get_db)):
    total_customers = db.query(models.Customer).count()
    total_invoices = db.query(models.Invoice).count()

    paid_invoices = db.query(models.Invoice).filter(models.Invoice.status == "paid").count()
    draft_invoices = db.query(models.Invoice).filter(models.Invoice.status == "draft").count()
    overdue_invoices = db.query(models.Invoice).filter(models.Invoice.status == "overdue").count()

    total_revenue = db.query(models.Invoice).filter(models.Invoice.status == "paid").with_entities(models.Invoice.total_amount).all()
    total_revenue_amount = sum(invoice.total_amount for invoice in total_revenue)

    unpaid_invoices = db.query(models.Invoice).filter(models.Invoice.status != "paid").with_entities(models.Invoice.total_amount).all()
    unpaid_amount = sum(invoice.total_amount for invoice in unpaid_invoices)

    return {
        "total_customers": total_customers,
        "total_invoices": total_invoices,
        "paid_invoices": paid_invoices,
        "draft_invoices": draft_invoices,
        "overdue_invoices": overdue_invoices,
        "total_revenue": total_revenue_amount,
        "unpaid_amount": unpaid_amount
    }
@app.post("/invoices/{invoice_id}/send-reminder", response_model=ReminderResponse)
def send_reminder(invoice_id: int, db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()

    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    customer = db.query(models.Customer).filter(models.Customer.id == invoice.customer_id).first()

    subject = f"Payment Reminder for Invoice {invoice.invoice_number}"

    message = f"""
Hello,

This is a reminder that your invoice {invoice.invoice_number} is currently marked as '{invoice.status}'.

Total Amount: ₹{invoice.total_amount}

Please complete the payment at your earliest convenience.

Thank you,
InvoiceFlow Team
"""

    return {
        "invoice_id": invoice.id,
        "customer_email": customer.email if customer else None,
        "subject": subject,
        "message": message
    }



@app.post("/recurring-billing", response_model=RecurringBillingResponse)
def create_recurring_billing(data: RecurringBillingCreate, db: Session = Depends(get_db)):
    new_plan = models.RecurringBilling(
        customer_id=data.customer_id,
        user_id=1,
        title=data.title,
        amount=data.amount,
        frequency=data.frequency,
        next_billing_date=data.next_billing_date,
        is_active=data.is_active
    )

    db.add(new_plan)
    db.commit()
    db.refresh(new_plan)

    return new_plan


@app.get("/recurring-billing", response_model=list[RecurringBillingResponse])
def get_recurring_billings(db: Session = Depends(get_db)):
    return db.query(models.RecurringBilling).all()


@app.get("/recurring-billing/{plan_id}", response_model=RecurringBillingResponse)
def get_recurring_billing(plan_id: int, db: Session = Depends(get_db)):
    plan = db.query(models.RecurringBilling).filter(models.RecurringBilling.id == plan_id).first()

    if not plan:
        raise HTTPException(status_code=404, detail="Recurring billing plan not found")

    return plan


@app.put("/recurring-billing/{plan_id}", response_model=RecurringBillingResponse)
def update_recurring_billing(
    plan_id: int,
    data: RecurringBillingCreate,
    db: Session = Depends(get_db)
):
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


@app.delete("/recurring-billing/{plan_id}")
def delete_recurring_billing(plan_id: int, db: Session = Depends(get_db)):
    plan = db.query(models.RecurringBilling).filter(models.RecurringBilling.id == plan_id).first()

    if not plan:
        raise HTTPException(status_code=404, detail="Recurring billing plan not found")

    db.delete(plan)
    db.commit()

    return {"message": "Recurring billing plan deleted successfully"}


@app.post("/recurring-billing/{plan_id}/generate-invoice", response_model=InvoiceResponse)
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
        notes=f"Auto-generated from recurring billing plan: {plan.title}"
    )

    db.add(new_invoice)
    db.commit()
    db.refresh(new_invoice)

    return new_invoice

@app.post("/invoice-items", response_model=InvoiceItemResponse)
def create_invoice_item(item: InvoiceItemCreate, db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == item.invoice_id).first()

    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    total_price = item.quantity * item.unit_price

    new_item = models.InvoiceItem(
        invoice_id=item.invoice_id,
        description=item.description,
        quantity=item.quantity,
        unit_price=item.unit_price,
        total_price=total_price
    )

    db.add(new_item)

    invoice.total_amount += total_price

    db.commit()
    db.refresh(new_item)

    return new_item


@app.get("/invoice-items", response_model=list[InvoiceItemResponse])
def get_invoice_items(db: Session = Depends(get_db)):
    return db.query(models.InvoiceItem).all()


@app.get("/invoice-items/{item_id}", response_model=InvoiceItemResponse)
def get_invoice_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(models.InvoiceItem).filter(models.InvoiceItem.id == item_id).first()

    if not item:
        raise HTTPException(status_code=404, detail="Invoice item not found")

    return item


@app.put("/invoice-items/{item_id}", response_model=InvoiceItemResponse)
def update_invoice_item(item_id: int, item: InvoiceItemCreate, db: Session = Depends(get_db)):
    db_item = db.query(models.InvoiceItem).filter(models.InvoiceItem.id == item_id).first()

    if not db_item:
        raise HTTPException(status_code=404, detail="Invoice item not found")

    invoice = db.query(models.Invoice).filter(models.Invoice.id == db_item.invoice_id).first()

    old_total = db_item.total_price
    new_total = item.quantity * item.unit_price

    db_item.invoice_id = item.invoice_id
    db_item.description = item.description
    db_item.quantity = item.quantity
    db_item.unit_price = item.unit_price
    db_item.total_price = new_total

    if invoice:
        invoice.total_amount = invoice.total_amount - old_total + new_total

    db.commit()
    db.refresh(db_item)

    return db_item


@app.delete("/invoice-items/{item_id}")
def delete_invoice_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(models.InvoiceItem).filter(models.InvoiceItem.id == item_id).first()

    if not item:
        raise HTTPException(status_code=404, detail="Invoice item not found")

    invoice = db.query(models.Invoice).filter(models.Invoice.id == item.invoice_id).first()

    if invoice:
        invoice.total_amount -= item.total_price

    db.delete(item)
    db.commit()

    return {"message": "Invoice item deleted successfully"}



@app.get("/exports/invoices-csv")
def export_invoices_csv(db: Session = Depends(get_db)):
    file_path = "app/invoices_export.csv"

    invoices = db.query(models.Invoice).all()

    with open(file_path, mode="w", newline="") as file:
        writer = csv.writer(file)

        writer.writerow([
            "ID",
            "Invoice Number",
            "Customer ID",
            "Status",
            "Total Amount",
            "Due Date",
            "Notes"
        ])

        for invoice in invoices:
            writer.writerow([
                invoice.id,
                invoice.invoice_number,
                invoice.customer_id,
                invoice.status,
                invoice.total_amount,
                invoice.due_date,
                invoice.notes
            ])

    return FileResponse(
        file_path,
        media_type="text/csv",
        filename="invoices_export.csv"
    )


@app.get("/auth/me", response_model=UserResponse)
def get_me(current_user: models.User = Depends(get_current_user)):
    return current_user


@app.post("/scheduler/run-recurring-billing")
def run_recurring_billing_scheduler(db: Session = Depends(get_db)):
    today = datetime.now(timezone.utc)

    plans = db.query(models.RecurringBilling).filter(
        models.RecurringBilling.is_active == True,
        models.RecurringBilling.next_billing_date <= today
    ).all()

    created_invoices = []

    for plan in plans:
        invoice_number = f"AUTO-{plan.id}-{int(datetime.now().timestamp())}"

        new_invoice = models.Invoice(
            invoice_number=invoice_number,
            customer_id=plan.customer_id,
            user_id=plan.user_id,
            due_date=plan.next_billing_date,
            status="draft",
            total_amount=plan.amount,
            notes=f"Auto-generated by scheduler from plan: {plan.title}"
        )

        db.add(new_invoice)
        db.flush()

        if plan.frequency == "monthly":
            plan.next_billing_date = plan.next_billing_date + timedelta(days=30)
        elif plan.frequency == "quarterly":
            plan.next_billing_date = plan.next_billing_date + timedelta(days=90)
        elif plan.frequency == "yearly":
            plan.next_billing_date = plan.next_billing_date + timedelta(days=365)

        created_invoices.append(new_invoice.invoice_number)

    db.commit()

    return {
        "message": "Recurring billing scheduler completed",
        "created_invoices": created_invoices,
        "count": len(created_invoices)
    }

@app.post("/invoices/{invoice_id}/ai-followup", response_model=AIFollowupResponse)
def generate_ai_followup(invoice_id: int, tone: str = "polite", db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()

    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    # Different tone messages
    if tone == "polite":
        subject = f"Friendly Reminder for Invoice {invoice.invoice_number}"
        message = f"""
Hello,

Hope you're doing well. This is a gentle reminder regarding your invoice {invoice.invoice_number}.

Amount Due: ₹{invoice.total_amount}

We would appreciate it if you could process the payment at your convenience.

Thank you,
InvoiceFlow Team
"""

    elif tone == "firm":
        subject = f"Payment Reminder - Invoice {invoice.invoice_number}"
        message = f"""
Hello,

This is a reminder that invoice {invoice.invoice_number} is still pending.

Amount Due: ₹{invoice.total_amount}

Kindly ensure the payment is completed as soon as possible to avoid any inconvenience.

Regards,
InvoiceFlow Team
"""

    elif tone == "urgent":
        subject = f"Urgent: Invoice {invoice.invoice_number} Overdue"
        message = f"""
Hello,

Your invoice {invoice.invoice_number} is now overdue.

Outstanding Amount: ₹{invoice.total_amount}

Immediate action is required. Please process the payment without further delay.

Regards,
InvoiceFlow Team
"""

    else:
        subject = f"Reminder for Invoice {invoice.invoice_number}"
        message = f"""
Hello,

Please note that invoice {invoice.invoice_number} is pending.

Amount: ₹{invoice.total_amount}

Kindly take necessary action.

InvoiceFlow Team
"""

    return {
        "invoice_id": invoice.id,
        "tone": tone,
        "subject": subject,
        "message": message
    }


from app.utils import send_email


@app.post("/invoices/{invoice_id}/send-email")
def send_invoice_email(invoice_id: int, tone: str = "polite", db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()

    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    customer = db.query(models.Customer).filter(models.Customer.id == invoice.customer_id).first()

    if not customer or not customer.email:
        raise HTTPException(status_code=400, detail="Customer email not found")

    # Reuse AI follow-up logic
    if tone == "polite":
        subject = f"Friendly Reminder for Invoice {invoice.invoice_number}"
        message = f"""
Hello,

This is a gentle reminder for invoice {invoice.invoice_number}.

Amount: ₹{invoice.total_amount}

Thank you,
InvoiceFlow
"""
    elif tone == "urgent":
        subject = f"URGENT: Invoice {invoice.invoice_number} Overdue"
        message = f"""
Hello,

Your invoice {invoice.invoice_number} is overdue.

Amount: ₹{invoice.total_amount}

Please pay immediately.

InvoiceFlow
"""
    else:
        subject = f"Reminder for Invoice {invoice.invoice_number}"
        message = f"""
Hello,

Invoice {invoice.invoice_number} is pending.

Amount: ₹{invoice.total_amount}

InvoiceFlow
"""

    send_email(customer.email, subject, message)

    return {"message": f"Email sent to {customer.email}"}


@app.on_event("startup")
def start_background_scheduler():
    start_scheduler()