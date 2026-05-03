from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timezone

from app.database import SessionLocal
from app import models
from app.utils import send_email


def run_daily_reminders():
    db = SessionLocal()

    try:
        invoices = db.query(models.Invoice).all()

        for invoice in invoices:
            if invoice.status != "paid":
                customer = db.query(models.Customer).filter(
                    models.Customer.id == invoice.customer_id
                ).first()

                if not customer or not customer.email:
                    continue

                subject = f"Reminder: Invoice {invoice.invoice_number}"

                message = f"""
Hello,

This is an automated reminder for invoice {invoice.invoice_number}.

Amount: ₹{invoice.total_amount}

Please complete your payment.

Thank you,
InvoiceFlow
"""

                try:
                    send_email(customer.email, subject, message)
                except Exception as e:
                    print("Email failed:", e)

    finally:
        db.close()


def start_scheduler():
    scheduler = BackgroundScheduler()
    
    # runs every 24 hours
    scheduler.add_job(run_daily_reminders, "interval", hours=24)
    
    scheduler.start()