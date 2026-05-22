"""
Onboarding Flow
New user signup → welcome email → demo data → AI product tour → personalized setup → check-in reminders.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Demo data templates
# ---------------------------------------------------------------------------

DEMO_CLIENTS = [
    {"name": "Acme Corporation", "email": "billing@acme.example.com", "company": "Acme Corp", "phone": "+1-555-0101", "tags": ["enterprise", "long-term"]},
    {"name": "Nova Digital", "email": "accounts@novadigital.example.com", "company": "Nova Digital Ltd", "phone": "+1-555-0202", "tags": ["startup", "tech"]},
    {"name": "Green Leaf Co.", "email": "pay@greenleaf.example.com", "company": "Green Leaf Co", "phone": "+1-555-0303", "tags": ["small-business"]},
]

DEMO_INVOICE_TEMPLATES = [
    {"description": "Web Development Services", "items": [{"description": "Frontend Development", "quantity": 20, "rate": 120}, {"description": "Backend API", "quantity": 15, "rate": 150}], "days_until_due": 30},
    {"description": "Monthly Consulting Retainer", "items": [{"description": "Strategy Consulting", "quantity": 8, "rate": 200}], "days_until_due": 15},
    {"description": "Design & Branding Package", "items": [{"description": "Logo Design", "quantity": 1, "rate": 800}, {"description": "Brand Guidelines", "quantity": 1, "rate": 500}], "days_until_due": 21},
]

ONBOARDING_STEPS = [
    {"step": 1, "title": "Welcome to InvoiceFlow AI! 👋", "description": "Your AI-powered invoicing platform is ready. Let's get you set up in 5 minutes.", "action": "tour_start"},
    {"step": 2, "title": "Create your first client", "description": "Add a client to get started. You can import existing clients later.", "action": "navigate_clients"},
    {"step": 3, "title": "Generate your first invoice", "description": "Try AI invoice generation – just describe what you need in plain English!", "action": "navigate_invoices"},
    {"step": 4, "title": "Set up automated reminders", "description": "Never chase payments manually again. Configure your reminder workflow.", "action": "navigate_workflows"},
    {"step": 5, "title": "Explore your dashboard", "description": "Your AI business assistant has analysed your data and has insights ready.", "action": "navigate_dashboard"},
]

AI_TIPS = [
    "💡 Type 'Invoice Acme for 10 hours design work at $120/hr due in 30 days' to create an invoice instantly.",
    "📊 Your business health score updates in real-time as invoices are paid.",
    "🤖 Ask the AI assistant anything – 'What are my overdue invoices this month?'",
    "⚡ Set up a recurring invoice workflow to automate monthly billing.",
    "🔔 Enable WhatsApp reminders for 3x better payment response rates.",
    "📈 Use cash flow forecasting to plan your finances 90 days ahead.",
]


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

async def send_welcome_notification(user_id: str, user_name: str, db: AsyncSession) -> None:
    """Create in-app welcome notification."""
    try:
        from app.models import Notification
        from app.core.constants import NotificationType
        notif = Notification(
            id=uuid.uuid4(),
            user_id=user_id,
            type=NotificationType.SYSTEM,
            title=f"Welcome to InvoiceFlow AI, {user_name}! 🎉",
            message="Your AI-powered invoicing platform is ready. Complete your onboarding to unlock all features.",
            read=False,
            data={"onboarding": True, "step": 1},
            created_at=datetime.now(timezone.utc),
        )
        db.add(notif)
        await db.commit()
        logger.info(f"[OnboardingFlow] Welcome notification sent to user {user_id}")
    except Exception as e:
        logger.error(f"[OnboardingFlow] send_welcome_notification error: {e}")
        await db.rollback()


async def create_demo_data(user_id: str, team_id: str, db: AsyncSession) -> dict:
    """Auto-create demo clients and invoices so the dashboard looks live."""
    from app.models import Client, Invoice, InvoiceItem
    from app.utils import generate_invoice_number
    import random

    created = {"clients": 0, "invoices": 0}
    client_ids = []

    # Create demo clients
    for tmpl in DEMO_CLIENTS:
        try:
            client = Client(
                id=uuid.uuid4(),
                name=tmpl["name"],
                email=tmpl["email"],
                company=tmpl["company"],
                phone=tmpl["phone"],
                tags=tmpl["tags"],
                team_id=team_id,
                created_by=user_id,
                payment_behavior_score=random.randint(60, 95),
                risk_score=random.uniform(0.1, 0.4),
            )
            db.add(client)
            await db.flush()
            client_ids.append(str(client.id))
            created["clients"] += 1
        except Exception as e:
            logger.warning(f"[OnboardingFlow] Demo client create error: {e}")

    await db.commit()

    # Create demo invoices
    statuses = ["paid", "sent", "overdue", "pending"]
    for i, inv_tmpl in enumerate(DEMO_INVOICE_TEMPLATES):
        try:
            client_id = client_ids[i % len(client_ids)] if client_ids else None
            if not client_id:
                continue

            now = datetime.now(timezone.utc)
            status = statuses[i % len(statuses)]
            subtotal = sum(item["quantity"] * item["rate"] for item in inv_tmpl["items"])
            tax = round(subtotal * 0.1, 2)
            total = subtotal + tax
            amount_paid = total if status == "paid" else (subtotal * 0.5 if status == "partial" else 0)

            invoice = Invoice(
                id=uuid.uuid4(),
                number=f"DEMO-{1000 + i + 1}",
                client_id=client_id,
                user_id=user_id,
                team_id=team_id,
                status=status,
                currency="USD",
                subtotal=subtotal,
                tax_rate=10,
                tax_amount=tax,
                discount=0,
                total=total,
                amount_paid=amount_paid,
                balance_due=total - amount_paid,
                issue_date=now - timedelta(days=random.randint(5, 60)),
                due_date=now + timedelta(days=inv_tmpl["days_until_due"] - random.randint(0, 20)),
                description=inv_tmpl["description"],
                source="demo",
                theme="modern",
            )
            db.add(invoice)
            await db.flush()

            for item_tmpl in inv_tmpl["items"]:
                item = InvoiceItem(
                    id=uuid.uuid4(),
                    invoice_id=invoice.id,
                    description=item_tmpl["description"],
                    quantity=item_tmpl["quantity"],
                    rate=item_tmpl["rate"],
                    amount=item_tmpl["quantity"] * item_tmpl["rate"],
                )
                db.add(item)

            created["invoices"] += 1
        except Exception as e:
            logger.warning(f"[OnboardingFlow] Demo invoice create error: {e}")

    await db.commit()
    logger.info(f"[OnboardingFlow] Demo data created: {created}")
    return created


async def schedule_checkin_reminders(user_id: str, db: AsyncSession) -> None:
    """Schedule Day 1, Day 3, and Day 7 check-in notifications."""
    try:
        from app.models import Notification
        from app.core.constants import NotificationType
        check_ins = [
            (1, "Day 1 Tip 🚀", "Did you know? You can create an invoice in seconds using AI. Just describe it!"),
            (3, "Day 3 Tip ⚡", "Set up your first automated workflow to send payment reminders automatically."),
            (7, "Week 1 Review 📊", "Check your business health score. Your AI assistant has insights waiting for you!"),
        ]
        now = datetime.now(timezone.utc)
        for days, title, message in check_ins:
            notif = Notification(
                id=uuid.uuid4(),
                user_id=user_id,
                type=NotificationType.SYSTEM,
                title=title,
                message=message,
                read=False,
                data={"scheduled": True, "deliver_at": (now + timedelta(days=days)).isoformat()},
                created_at=now + timedelta(days=days),
            )
            db.add(notif)
        await db.commit()
        logger.info(f"[OnboardingFlow] Check-in reminders scheduled for user {user_id}")
    except Exception as e:
        logger.error(f"[OnboardingFlow] schedule_checkin_reminders error: {e}")
        await db.rollback()


def get_onboarding_steps() -> list[dict]:
    """Return ordered onboarding steps for the UI tour."""
    return ONBOARDING_STEPS


def get_ai_tips(n: int = 3) -> list[str]:
    """Return n random AI tips for the onboarding screen."""
    import random
    return random.sample(AI_TIPS, min(n, len(AI_TIPS)))


async def create_default_workflow(user_id: str, team_id: str, db: AsyncSession) -> None:
    """Create a default 'Overdue Invoice Reminder' workflow for new users."""
    try:
        from app.models import Workflow
        from app.core.constants import WorkflowTrigger
        workflow = Workflow(
            id=uuid.uuid4(),
            name="Auto Overdue Reminder",
            description="Automatically sends a reminder when an invoice becomes overdue",
            trigger_type=WorkflowTrigger.INVOICE_OVERDUE,
            conditions={"days_overdue": 1},
            actions=[{"type": "send_reminder", "tone": "friendly"}],
            team_id=team_id,
            is_active=True,
            created_by=user_id,
        )
        db.add(workflow)
        await db.commit()
        logger.info(f"[OnboardingFlow] Default workflow created for team {team_id}")
    except Exception as e:
        logger.warning(f"[OnboardingFlow] create_default_workflow error: {e}")
        await db.rollback()


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def run_onboarding_flow(
    user_id: str,
    team_id: str,
    user_name: str,
    load_demo_data: bool = True,
    db: AsyncSession = None,
) -> dict:
    """
    Full onboarding flow for a new user.
    Returns summary of completed steps.
    """
    result = {
        "welcome_sent": False,
        "demo_data": {},
        "checkins_scheduled": False,
        "default_workflow_created": False,
        "onboarding_steps": get_onboarding_steps(),
        "tips": get_ai_tips(3),
    }

    if db is None:
        logger.warning("[OnboardingFlow] No DB session provided, skipping DB steps")
        return result

    await send_welcome_notification(user_id, user_name, db)
    result["welcome_sent"] = True

    if load_demo_data:
        result["demo_data"] = await create_demo_data(user_id, team_id, db)

    await schedule_checkin_reminders(user_id, db)
    result["checkins_scheduled"] = True

    await create_default_workflow(user_id, team_id, db)
    result["default_workflow_created"] = True

    logger.info(f"[OnboardingFlow] Completed for user {user_id}")
    return result