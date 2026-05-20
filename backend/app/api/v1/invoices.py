import os
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from sqlalchemy.orm import Session

from app import models
from app.api.deps import get_db
from app.auth import get_current_user
from app.core.state import app_state
from app.schemas import (
    InvoiceCreate,
    InvoiceResponse,
    InvoiceStatusUpdate,
    ReminderResponse,
    AIFollowupResponse,
)
from app.services.ai_service import ai_service
from app.utils import send_email

router = APIRouter(prefix="/invoices", tags=["Invoices"])


@router.post("", response_model=InvoiceResponse)
def create_invoice(
    invoice: InvoiceCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    new_invoice = models.Invoice(
        invoice_number=invoice.invoice_number,
        customer_id=invoice.customer_id,
        user_id=current_user.id,
        due_date=invoice.due_date,
        status=invoice.status,
        total_amount=invoice.total_amount,
        notes=invoice.notes,
    )
    db.add(new_invoice)
    db.commit()
    db.refresh(new_invoice)
    return new_invoice


@router.get("", response_model=list[InvoiceResponse])
def get_invoices(db: Session = Depends(get_db)):
    return db.query(models.Invoice).all()


@router.get("/{invoice_id}", response_model=InvoiceResponse)
def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


@router.put("/{invoice_id}", response_model=InvoiceResponse)
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


@router.delete("/{invoice_id}")
def delete_invoice(invoice_id: int, db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    db.delete(invoice)
    db.commit()
    return {"message": "Invoice deleted successfully"}


@router.patch("/{invoice_id}/status", response_model=InvoiceResponse)
def update_invoice_status(
    invoice_id: int, status_update: InvoiceStatusUpdate, db: Session = Depends(get_db)
):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    invoice.status = status_update.status
    db.commit()
    db.refresh(invoice)
    return invoice


@router.get("/{invoice_id}/pdf")
def generate_invoice_pdf(invoice_id: int, db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    file_name = f"invoice_{invoice_id}.pdf"
    file_path = os.path.join("app", file_name)

    c = canvas.Canvas(file_path, pagesize=letter)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(200, 750, "INVOICE")
    c.setFont("Helvetica", 12)
    c.drawString(50, 700, f"Invoice Number: {invoice.invoice_number}")
    c.drawString(50, 680, f"Customer ID: {invoice.customer_id}")
    c.drawString(50, 660, f"Status: {invoice.status}")
    c.drawString(50, 640, f"Total Amount: ₹{invoice.total_amount}")
    c.drawString(50, 600, f"Notes: {invoice.notes or ''}")
    c.save()

    return FileResponse(file_path, media_type="application/pdf", filename=file_name)


@router.post("/{invoice_id}/send-reminder", response_model=ReminderResponse)
def send_reminder(invoice_id: int, db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    customer = db.query(models.Customer).filter(models.Customer.id == invoice.customer_id).first()
    subject = f"Payment Reminder for Invoice {invoice.invoice_number}"
    message = (
        f"Hello,\n\nThis is a reminder that your invoice {invoice.invoice_number} "
        f"is currently marked as '{invoice.status}'.\n\nTotal Amount: ₹{invoice.total_amount}\n\n"
        "Please complete the payment at your earliest convenience.\n\nThank you,\nInvoiceFlow Team"
    )
    app_state.record_reminder()
    return {
        "invoice_id": invoice.id,
        "customer_email": customer.email if customer else None,
        "subject": subject,
        "message": message,
    }


@router.post("/{invoice_id}/ai-followup", response_model=AIFollowupResponse)
def generate_ai_followup(
    invoice_id: int,
    request: Request,
    tone: str = "polite",
    db: Session = Depends(get_db),
):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    usage = ai_service.track_usage("invoice_followup")
    request.state.ai_tokens = usage["prompt_tokens"] + usage["completion_tokens"]

    if tone == "polite":
        subject = f"Friendly Reminder for Invoice {invoice.invoice_number}"
        message = (
            f"Hello,\n\nHope you're doing well. This is a gentle reminder regarding "
            f"your invoice {invoice.invoice_number}.\n\nAmount Due: ₹{invoice.total_amount}\n\n"
            "Thank you,\nInvoiceFlow Team"
        )
    elif tone == "firm":
        subject = f"Payment Reminder - Invoice {invoice.invoice_number}"
        message = (
            f"Hello,\n\nThis is a reminder that invoice {invoice.invoice_number} is still pending.\n\n"
            f"Amount Due: ₹{invoice.total_amount}\n\nKindly ensure payment is completed as soon as possible.\n\n"
            "Regards,\nInvoiceFlow Team"
        )
    elif tone == "urgent":
        subject = f"Urgent: Invoice {invoice.invoice_number} Overdue"
        message = (
            f"Hello,\n\nYour invoice {invoice.invoice_number} is now overdue.\n\n"
            f"Outstanding Amount: ₹{invoice.total_amount}\n\nImmediate action is required.\n\n"
            "Regards,\nInvoiceFlow Team"
        )
    else:
        subject = f"Reminder for Invoice {invoice.invoice_number}"
        message = (
            f"Hello,\n\nInvoice {invoice.invoice_number} is pending.\n\n"
            f"Amount: ₹{invoice.total_amount}\n\nInvoiceFlow Team"
        )

    return {"invoice_id": invoice.id, "tone": tone, "subject": subject, "message": message}


@router.post("/{invoice_id}/send-email")
def send_invoice_email(invoice_id: int, tone: str = "polite", db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    customer = db.query(models.Customer).filter(models.Customer.id == invoice.customer_id).first()
    if not customer or not customer.email:
        raise HTTPException(status_code=400, detail="Customer email not found")

    subject = f"Reminder for Invoice {invoice.invoice_number}"
    message = (
        f"Hello,\n\nInvoice {invoice.invoice_number} is pending.\n\n"
        f"Amount: ₹{invoice.total_amount}\n\nInvoiceFlow"
    )
    send_email(customer.email, subject, message)
    app_state.record_reminder()
    return {"message": f"Email sent to {customer.email}"}
