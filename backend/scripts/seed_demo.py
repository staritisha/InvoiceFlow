"""
Demo data seeder for InvoiceFlow.
Run once after deploying to populate the live demo with realistic data.

Usage:
    python scripts/seed_demo.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone
from app.database import SessionLocal
from app import models
from app.auth import hash_password

db = SessionLocal()

def run():
    # Check if demo already seeded
    existing = db.query(models.User).filter(models.User.email == "demo@invoiceflow.app").first()
    if existing:
        print("Demo data already exists. Skipping.")
        return

    print("Seeding demo data...")

    now = datetime.now(timezone.utc)

    # ── Demo user ────────────────────────────────────────────────────
    user = models.User(
        full_name="Ritisha Demo",
        email="demo@invoiceflow.app",
        hashed_password=hash_password("Demo@1234"),
        role="admin",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.flush()

    # ── Clients ──────────────────────────────────────────────────────
    clients_data = [
        ("Aakash Technologies", "aakash@aakashtech.in",     "+91 98765 43210", "low"),
        ("Sharma Consulting",   "rahul@sharmaconsult.com",  "+91 87654 32109", "low"),
        ("Priya Designs",       "priya@priyadesigns.in",    "+91 76543 21098", "medium"),
        ("NextGen Startups",    "ops@nextgenstartups.io",   "+91 65432 10987", "low"),
        ("Mumbai Retail Co",    "accounts@mumbairetail.com","+91 54321 09876", "high"),
    ]
    clients = []
    for name, email, phone, risk in clients_data:
        c = models.Client(
            user_id=user.id,
            name=name,
            email=email,
            phone=phone,
            risk_category=risk,
            created_at=now,
            updated_at=now,
        )
        db.add(c)
        db.flush()
        clients.append(c)

    # ── Invoices ─────────────────────────────────────────────────────
    invoices_data = [
        # (client_idx, status, amount, days_ago, due_in_days)
        (0, "paid",    45000,  60, 30),
        (1, "paid",    28500,  50, 30),
        (0, "paid",    67000,  40, 30),
        (2, "sent",    32000,  10, 20),
        (4, "overdue", 18500,  45, -15),
        (1, "draft",   55000,   2, 28),
        (3, "sent",    42000,   8, 22),
        (4, "overdue",  9800,  30, -5),
        (0, "paid",    73000,  90, 30),
        (2, "sent",    29500,   5, 25),
        (3, "overdue", 41000,  20, -3),
        (1, "draft",   15000,   1, 30),
    ]
    for i, (ci, status, amount, days_ago, due_in) in enumerate(invoices_data):
        inv = models.Invoice(
            invoice_number=f"INV-2026-{str(i+1).zfill(3)}",
            user_id=user.id,
            client_id=clients[ci].id,
            status=status,
            total_amount=amount,
            issue_date=now - timedelta(days=days_ago),
            due_date=now + timedelta(days=due_in),
            notes="Thank you for your business.",
            subtotal=amount,
            currency="INR",
            created_at=now,
            updated_at=now,
        )
        db.add(inv)
        db.flush()

        # Add line items
        db.add(models.InvoiceItem(
            invoice_id=inv.id,
            description="Professional Services",
            quantity=1,
            unit_price=amount,
            total_price=float(amount),
            created_at=now,
            updated_at=now,
        ))

    # ── Recurring billing ────────────────────────────────────────────
    recurring_data = [
        (0, "Monthly Retainer",     "Development retainer",   25000, "monthly",   7),
        (1, "Consulting Package",   "Monthly consulting",     15000, "monthly",  14),
        (3, "Quarterly Review",     "Strategy review",        45000, "quarterly", 30),
    ]
    for ci, title, desc, amount, freq, next_days in recurring_data:
        db.add(models.RecurringBilling(
            user_id=user.id,
            client_id=clients[ci].id,
            title=title,
            description=desc,
            amount=amount,
            frequency=freq,
            next_billing_date=now + timedelta(days=next_days),
            is_active=True,
            created_at=now,
            updated_at=now,
        ))

    db.commit()
    print("✓ Demo data seeded successfully!")
    print(f"  Login: demo@invoiceflow.app / Demo@1234")
    print(f"  Users: 1 | Clients: {len(clients)} | Invoices: {len(invoices_data)} | Recurring: {len(recurring_data)}")

if __name__ == "__main__":
    run()
    db.close()