"""
Create Admin User CLI
Creates a superadmin user with full platform access.

Usage:
    python scripts/create_admin.py
    python scripts/create_admin.py --email admin@company.com --password Admin1234! --name "Jane Doe"
"""

import asyncio
import argparse
import uuid
import getpass
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select

from app.config import settings
from app.models import User, Team
from app.core.security import hash_password


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _validate_password(password: str) -> bool:
    if len(password) < 8:
        print("❌ Password must be at least 8 characters.")
        return False
    if not any(c.isupper() for c in password):
        print("❌ Password must contain at least one uppercase letter.")
        return False
    if not any(c.isdigit() for c in password):
        print("❌ Password must contain at least one digit.")
        return False
    return True


async def _email_exists(db: AsyncSession, email: str) -> bool:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none() is not None


# ─────────────────────────────────────────────────────────────────────────────
# Core creation
# ─────────────────────────────────────────────────────────────────────────────

async def create_admin(
    email: str,
    password: str,
    full_name: str,
    business_name: str,
    team_name: str,
):
    engine = create_async_engine(settings.database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        # Check duplicate
        if await _email_exists(db, email):
            print(f"❌ User with email '{email}' already exists.")
            await engine.dispose()
            return

        user_id = str(uuid.uuid4())
        team_id = str(uuid.uuid4())

        # Create team
        team = Team(
            id=team_id,
            name=team_name,
            slug=team_name.lower().replace(" ", "-")[:50],
            owner_id=user_id,
        )
        db.add(team)
        await db.flush()

        # Create superadmin user
        user = User(
            id=user_id,
            email=email,
            username=email.split("@")[0],
            hashed_password=hash_password(password),
            full_name=full_name,
            role="admin",
            team_id=team_id,
            is_active=True,
            is_superuser=True,
            subscription_tier="enterprise",
            business_name=business_name,
        )
        db.add(user)
        await db.commit()

    await engine.dispose()

    print("\n" + "─" * 45)
    print("  ✅ Superadmin created successfully!")
    print("─" * 45)
    print(f"  Email:         {email}")
    print(f"  Full Name:     {full_name}")
    print(f"  Business:      {business_name}")
    print(f"  Team:          {team_name}")
    print(f"  Role:          superadmin")
    print(f"  User ID:       {user_id}")
    print(f"  Team ID:       {team_id}")
    print("─" * 45 + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# Interactive prompt fallback
# ─────────────────────────────────────────────────────────────────────────────

def _interactive_prompt() -> dict:
    print("\n🔐 InvoiceFlow – Create Admin User\n" + "─" * 40)
    email = input("Admin email: ").strip()
    if not email or "@" not in email:
        print("❌ Invalid email.")
        sys.exit(1)

    while True:
        password = getpass.getpass("Password (min 8 chars, 1 upper, 1 digit): ")
        if _validate_password(password):
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm:
                print("❌ Passwords do not match. Try again.")
            else:
                break

    full_name     = input("Full name [Admin User]: ").strip() or "Admin User"
    business_name = input("Business name [InvoiceFlow]: ").strip() or "InvoiceFlow"
    team_name     = input("Team name [Admin Team]: ").strip() or "Admin Team"

    return {
        "email": email,
        "password": password,
        "full_name": full_name,
        "business_name": business_name,
        "team_name": team_name,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create an InvoiceFlow admin user")
    parser.add_argument("--email",         help="Admin email address")
    parser.add_argument("--password",      help="Admin password")
    parser.add_argument("--name",          default="Admin User",    help="Full name")
    parser.add_argument("--business",      default="InvoiceFlow",   help="Business name")
    parser.add_argument("--team",          default="Admin Team",     help="Team name")
    args = parser.parse_args()

    if args.email and args.password:
        # Non-interactive mode
        if not _validate_password(args.password):
            sys.exit(1)
        asyncio.run(create_admin(
            email=args.email,
            password=args.password,
            full_name=args.name,
            business_name=args.business,
            team_name=args.team,
        ))
    else:
        # Interactive mode
        kwargs = _interactive_prompt()
        asyncio.run(create_admin(**kwargs))