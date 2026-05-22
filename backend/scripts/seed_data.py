"""
Seed Data Script
Seeds a demo user, clients, invoices, and payments into the database.

Usage:
    python scripts/seed_data.py
    python scripts/seed_data.py --email demo@invoiceflow.ai --password demo1234
"""

import asyncio
import argparse
import uuid
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# ── Adjust import path if running from project root ──────────────────────────
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import settings
from app.models import Base, User, Team, Client, Invoice, InvoiceItem, Payment, Notification
from app.core.security import hash_password


# ─────────────────────────────────────────────────────────────────────────────
# Seed config
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_EMAIL    = "demo@invoiceflow.ai"
DEFAULT_PASSWORD = "Demo1234!"
DEFAULT_BUSINESS = "Demo Studio"

CLIENTS_DATA = [
    {"name": "Acme Corporation",   "email": "billing@acme.example.com",     "company": "Acme Corp",         "tags": ["enterprise"]},
    {"name": "Nova Digital",       "email": "accounts@novadigital.example.com","company": "Nova Digital Ltd", "tags": ["startup", "tech"]},
    {"name": "Green Leaf Co.",     "email": "pay@greenleaf.example.com",     "company": "Green Leaf Co",     "tags": ["small-business"]},
    {"name": "Bright Futures Inc.","email": "finance@brightfutures.example.com","company": "Bright Futures", "tags": ["ngo"]},
    {"name": "TechWave Studio",    "email": "hello@techwave.example.com",    "company": "TechWave Studio",   "tags": ["design", "tech"]},
]

INVOICE_TEMPLATES = [
    {"desc": "Web Development – Sprint 12", "items": [("Frontend Development", 20, 150), ("Backend API", 15, 180), ("Code Review", 5, 120)]},
    {"desc": "Monthly Retainer – Strategy", "items": [("Strategy Consulting", 8, 250), ("Weekly Reports", 4, 100)]},
    {"desc": "Brand Identity Package",       "items": [("Logo Design", 1, 1200), ("Brand Guidelines", 1, 800), ("Social Media Kit", 1, 400)]},
    {"desc": "Mobile App Development",       "items": [("UI/UX Design", 30, 130), ("React Native Dev", 40, 170), ("QA Testing", 10, 90)]},
    {"desc": "SEO & Content Package",        "items": [("SEO Audit", 1, 600), ("Content Writing", 10, 80), ("Technical SEO", 5, 120)]},
]

STATUSES = ["paid", "sent", "overdue", "pending", "partial"]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc)


def _rand_date(days_back=0, days_forward=0):
    offset = random.randint(-abs(days_back), abs(days_forward))
    return _now() + timedelta(days=offset)


async def _get_or_create_team(db: AsyncSession, owner_id: str) -> str:
    team_id = str(uuid.uuid4())
    team = Team(
        id=team_id,
        name=DEFAULT_BUSINESS,
        slug=f"demo-{team_id[:8]}",
        owner_id=owner_id,
    )
    db.add(team)
    await db.flush()
    return team_id


# ─────────────────────────────────────────────────────────────────────────────
# Seed functions
# ─────────────────────────────────────────────────────────────────────────────

async def seed_user(db: AsyncSession, email: str, password: str) -> tuple[str, str]:
    """Create demo user + team. Returns (user_id, team_id)."""
    user_id = str(uuid.uuid4())
    team_id = await _get_or_create_team(db, user_id)

    user = User(
        id=user_id,
        email=email,
        username=email.split("@")[0],
        hashed_password=hash_password(password),
        full_name="Demo User",
        role="admin",
        team_id=team_id,
        is_active=True,
        subscription_tier="pro",
        business_name=DEFAULT_BUSINESS,
        last_login=_now(),
    )
    db.add(user)

    # Update team owner
    from sqlalchemy import update
    await db.execute(
        update(Team).where(Team.id == team_id).values(owner_id=user_id)
    )
    await db.commit()
    print(f"  ✅ User created: {email}")
    return user_id, team_id


async def seed_clients(db: AsyncSession, user_id: str, team_id: str) -> list[str]:
    """Create demo clients. Returns list of client IDs."""
    client_ids = []
    for data in CLIENTS_DATA:
        client_id = str(uuid.uuid4())
        client = Client(
            id=client_id,
            name=data["name"],
            email=data["email"],
            company=data["company"],
            phone=f"+1-555-{random.randint(1000,9999)}",
            tags=data["tags"],
            team_id=team_id,
            created_by=user_id,
            payment_behavior_score=random.uniform(55, 95),
            risk_score=random.uniform(0.05, 0.45),
            total_invoiced=0,
            total_paid=0,
            average_days_to_pay=random.randint(5, 35),
        )
        db.add(client)
        client_ids.append(client_id)

    await db.commit()
    print(f"  ✅ {len(client_ids)} clients created")
    return client_ids


async def seed_invoices(db: AsyncSession, user_id: str, team_id: str, client_ids: list[str]) -> int:
    """Create demo invoices with items and payments."""
    count = 0
    themes = ["modern", "classic", "minimal", "bold", "startup", "elegant"]

    for i, tmpl in enumerate(INVOICE_TEMPLATES):
        for j, status in enumerate(STATUSES):
            client_id = client_ids[(i + j) % len(client_ids)]
            invoice_id = str(uuid.uuid4())
            invoice_number = f"INV-{2026000 + count + 1}"

            # Calculate amounts
            subtotal = sum(qty * rate for _, qty, rate in tmpl["items"])
            tax_rate = random.choice([0, 5, 10, 15])
            tax_amount = round(subtotal * tax_rate / 100, 2)
            discount = random.choice([0, 0, 0, 50, 100])
            total = round(subtotal + tax_amount - discount, 2)

            if status == "paid":
                amount_paid = total
                balance_due = 0
            elif status == "partial":
                amount_paid = round(total * random.uniform(0.3, 0.7), 2)
                balance_due = round(total - amount_paid, 2)
            else:
                amount_paid = 0
                balance_due = total

            issue_date = _rand_date(days_back=60)
            due_date_offset = -random.randint(1, 30) if status == "overdue" else random.randint(7, 45)
            due_date = _now() + timedelta(days=due_date_offset)
            paid_date = _now() - timedelta(days=random.randint(1, 15)) if status == "paid" else None

            invoice = Invoice(
                id=invoice_id,
                number=invoice_number,
                client_id=client_id,
                user_id=user_id,
                team_id=team_id,
                status=status,
                currency="USD",
                theme=random.choice(themes),
                subtotal=subtotal,
                tax_rate=tax_rate,
                tax_amount=tax_amount,
                discount=discount,
                total=total,
                amount_paid=amount_paid,
                balance_due=balance_due,
                issue_date=issue_date,
                due_date=due_date,
                paid_date=paid_date,
                description=tmpl["desc"],
                source="manual",
                reminders_sent=random.randint(0, 3) if status in ("overdue", "sent") else 0,
            )
            db.add(invoice)
            await db.flush()

            # Add items
            for desc, qty, rate in tmpl["items"]:
                item = InvoiceItem(
                    id=str(uuid.uuid4()),
                    invoice_id=invoice_id,
                    description=desc,
                    quantity=qty,
                    rate=rate,
                    amount=qty * rate,
                )
                db.add(item)

            # Add payment record if paid/partial
            if amount_paid > 0:
                payment = Payment(
                    id=str(uuid.uuid4()),
                    invoice_id=invoice_id,
                    amount=amount_paid,
                    method=random.choice(["bank_transfer", "stripe", "cash", "cheque"]),
                    paid_at=paid_date or _now() - timedelta(days=1),
                )
                db.add(payment)

            count += 1

    await db.commit()
    print(f"  ✅ {count} invoices created")
    return count


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main(email: str, password: str):
    print("\n🌱 InvoiceFlow Seed Data\n" + "─" * 40)

    engine = create_async_engine(settings.database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        print("Creating demo user...")
        user_id, team_id = await seed_user(db, email, password)
        print("Creating demo clients...")
        client_ids = await seed_clients(db, user_id, team_id)
        print("Creating demo invoices...")
        invoice_count = await seed_invoices(db, user_id, team_id, client_ids)

    await engine.dispose()

    print(f"\n✨ Seed complete!")
    print(f"   Email:    {email}")
    print(f"   Password: {password}")
    print(f"   Clients:  {len(client_ids)}")
    print(f"   Invoices: {invoice_count}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed InvoiceFlow demo data")
    parser.add_argument("--email",    default=DEFAULT_EMAIL,    help="Demo user email")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Demo user password")
    args = parser.parse_args()
    asyncio.run(main(args.email, args.password))