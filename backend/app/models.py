# ═══════════════════════════════════════════════════════════════════════════════
#  AI Invoice Intelligence Platform — app/models.py
#  AI-ready, analytics-optimized, workflow-scalable SQLAlchemy ORM layer.
#  Every table is designed for realtime dashboards, AI features, and future
#  enterprise expansion without schema migration pain.
# ═══════════════════════════════════════════════════════════════════════════════
from __future__ import annotations


import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, ForeignKey,
    Integer, Numeric, String, Text, Float, BigInteger,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


# ═══════════════════════════════════════════════════════════════════════════════
#  BASE MIXINS
# ═══════════════════════════════════════════════════════════════════════════════

class TimestampMixin:
    created_at = Column(
    DateTime(timezone=True),
    server_default=func.now(),
    default=lambda: datetime.now(tz.utc),
    nullable=False
)

updated_at = Column(
    DateTime(timezone=True),
    server_default=func.now(),
    default=lambda: datetime.now(tz.utc),
    onupdate=lambda: datetime.now(tz.utc),
    nullable=False
)

class SoftDeleteMixin:
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)


class AuditMixin(TimestampMixin, SoftDeleteMixin):
    """Full audit trail mixin — timestamps + soft delete."""
    pass


# ═══════════════════════════════════════════════════════════════════════════════
#  TEAM  (multi-tenant root)
# ═══════════════════════════════════════════════════════════════════════════════

class Team(AuditMixin, Base):
    __tablename__ = "teams"

    id                   = Column(Integer, primary_key=True, index=True)
    name                 = Column(String(150), nullable=False)
    slug                 = Column(String(100), unique=True, index=True, nullable=False)
    owner_id             = Column(Integer, ForeignKey("users.id"), nullable=True)

    # SaaS billing
    subscription_tier    = Column(String(50), default="free", nullable=False)
    subscription_expires = Column(DateTime(timezone=True), nullable=True)
    stripe_customer_id   = Column(String(100), nullable=True)
    stripe_subscription_id = Column(String(100), nullable=True)

    # Business context (used for AI personalization)
    industry             = Column(String(100), nullable=True)
    company_size         = Column(String(50), nullable=True)   # "1-10", "11-50", etc.
    monthly_revenue      = Column(Numeric(14, 2), nullable=True)
    country              = Column(String(50), nullable=True)
    timezone             = Column(String(50), default="UTC")
    currency             = Column(String(10), default="USD")

    # Customization
    ai_preferences       = Column(JSON, default=dict)          # temperature, provider, persona
    branding             = Column(JSON, default=dict)           # logo_url, colors, fonts
    feature_overrides    = Column(JSON, default=dict)          # feature-flag overrides per team

    # AI-computed
    ai_health_score      = Column(Float, nullable=True)        # 0–100 overall business health

    # Relationships
    users                = relationship("User", back_populates="team", foreign_keys="User.team_id")
    clients              = relationship("Client", back_populates="team")
    invoices             = relationship("Invoice", back_populates="team")
    workflows            = relationship("Workflow", back_populates="team")
    insights             = relationship("BusinessInsight", back_populates="team")
    recommendations      = relationship("AIRecommendation", back_populates="team")


# ═══════════════════════════════════════════════════════════════════════════════
#  USER
# ═══════════════════════════════════════════════════════════════════════════════

class User(AuditMixin, Base):
    __tablename__ = "users"

    id                       = Column(Integer, primary_key=True, index=True)
    full_name                = Column(String(100), nullable=False)
    email                    = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password          = Column(String(255), nullable=False)
    role                     = Column(String(50), default="member", nullable=False)
    is_active                = Column(Boolean, default=True, nullable=False)

    # Multi-tenant
    team_id                  = Column(Integer, ForeignKey("teams.id"), nullable=True, index=True)

    # Profile
    avatar_url               = Column(String(500), nullable=True)
    phone                    = Column(String(30), nullable=True)
    timezone                 = Column(String(50), default="UTC")
    language                 = Column(String(10), default="en")
    country                  = Column(String(50), nullable=True)

    # Preferences
    theme_preference         = Column(String(20), default="light")
    notification_preferences = Column(JSON, default=dict)
    dashboard_layout         = Column(JSON, default=dict)
    command_palette_enabled  = Column(Boolean, default=True)
    voice_enabled            = Column(Boolean, default=False)

    # Onboarding
    onboarding_completed     = Column(Boolean, default=False)
    onboarding_step          = Column(Integer, default=0)

    # AI usage tracking
    ai_usage_count           = Column(Integer, default=0, nullable=False)
    ai_tokens_consumed       = Column(BigInteger, default=0, nullable=False)
    last_ai_interaction      = Column(DateTime(timezone=True), nullable=True)
    ai_memory_enabled        = Column(Boolean, default=True)

    # Auth
    refresh_token_hash       = Column(String(255), nullable=True)
    last_login_at            = Column(DateTime(timezone=True), nullable=True)
    last_login_ip            = Column(String(50), nullable=True)
    failed_login_attempts    = Column(Integer, default=0)
    locked_until             = Column(DateTime(timezone=True), nullable=True)
    email_verified           = Column(Boolean, default=False)
    mfa_enabled              = Column(Boolean, default=False)
    mfa_secret               = Column(String(100), nullable=True)

    # Relationships
    team                     = relationship("Team", back_populates="users", foreign_keys=[team_id])
    invoices                 = relationship("Invoice", back_populates="user")
    clients                  = relationship("Client", back_populates="user")
    activities               = relationship("Activity", back_populates="user")
    notifications            = relationship("Notification", back_populates="user")
    conversations            = relationship("AIConversation", back_populates="user")
    widgets                  = relationship("DashboardWidget", back_populates="user")
    expenses                 = relationship("Expense", back_populates="user")
    reports                  = relationship("Report", back_populates="user")

    __table_args__ = (
        Index("ix_users_team_role", "team_id", "role"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  CLIENT  (AI-powered)
# ═══════════════════════════════════════════════════════════════════════════════

class Client(AuditMixin, Base):
    __tablename__ = "clients"

    id                       = Column(Integer, primary_key=True, index=True)
    user_id                  = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    team_id                  = Column(Integer, ForeignKey("teams.id"), nullable=True, index=True)

    # Identity
    name                     = Column(String(150), nullable=False, index=True)
    email                    = Column(String(255), nullable=True, index=True)
    phone                    = Column(String(30), nullable=True)
    address                  = Column(String(500), nullable=True)
    city                     = Column(String(100), nullable=True)
    country                  = Column(String(50), nullable=True)
    currency                 = Column(String(10), default="USD")

    # Business profile
    company_name             = Column(String(150), nullable=True)
    industry                 = Column(String(100), nullable=True)
    website                  = Column(String(255), nullable=True)
    linkedin_url             = Column(String(255), nullable=True)
    tax_id                   = Column(String(100), nullable=True)
    preferred_contact_method = Column(String(30), default="email")

    # Financial intelligence
    credit_limit             = Column(Numeric(14, 2), nullable=True)
    payment_terms_days       = Column(Integer, default=30)
    payment_reliability      = Column(Float, default=100.0)   # 0–100 score
    payment_success_rate     = Column(Float, default=100.0)
    average_payment_delay    = Column(Float, default=0.0)     # days

    # AI risk fields
    risk_score               = Column(Float, default=0.0)     # 0–100
    risk_category            = Column(String(20), default="low")
    late_payment_probability = Column(Float, default=0.0)     # 0–1
    collection_priority      = Column(Integer, default=0)     # 0=normal, 1=high, 2=urgent
    sentiment_score          = Column(Float, nullable=True)   # -1 to 1
    predicted_ltv            = Column(Numeric(14, 2), nullable=True)

    # AI-generated content
    ai_summary               = Column(Text, nullable=True)
    relationship_strength    = Column(String(20), default="new")  # new|warm|loyal|at_risk|churned

    # Engagement analytics
    emails_sent              = Column(Integer, default=0)
    emails_opened            = Column(Integer, default=0)
    invoice_views            = Column(Integer, default=0)
    last_contacted_at        = Column(DateTime(timezone=True), nullable=True)
    next_followup_date       = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    user                     = relationship("User", back_populates="clients")
    team                     = relationship("Team", back_populates="clients")
    invoices                 = relationship("Invoice", back_populates="client")
    reminders                = relationship("Reminder", back_populates="client")

    __table_args__ = (
        Index("ix_clients_team_risk", "team_id", "risk_category"),
        Index("ix_clients_user_name", "user_id", "name"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  INVOICE  (core business entity)
# ═══════════════════════════════════════════════════════════════════════════════

class Invoice(AuditMixin, Base):
    __tablename__ = "invoices"

    id                       = Column(Integer, primary_key=True, index=True)
    invoice_number           = Column(String(50), unique=True, index=True, nullable=False)
    user_id                  = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    team_id                  = Column(Integer, ForeignKey("teams.id"), nullable=True, index=True)
    client_id                = Column(Integer, ForeignKey("clients.id"), nullable=True, index=True)

    # Core fields
    title                    = Column(String(200), nullable=True)
    status                   = Column(String(50), default="draft", nullable=False, index=True)
    currency                 = Column(String(10), default="USD")
    issue_date               = Column(DateTime(timezone=True), server_default=func.now())
    due_date                 = Column(DateTime(timezone=True), nullable=True, index=True)
    notes                    = Column(Text, nullable=True)
    terms                    = Column(Text, nullable=True)

    # Amounts
    subtotal                 = Column(Numeric(14, 2), default=0, nullable=False)
    tax_rate                 = Column(Numeric(5, 2), default=0)
    tax_amount               = Column(Numeric(14, 2), default=0)
    discount_amount          = Column(Numeric(14, 2), default=0)
    total_amount             = Column(Numeric(14, 2), default=0, nullable=False)
    amount_paid              = Column(Numeric(14, 2), default=0)
    amount_due               = Column(Numeric(14, 2), default=0)

    # AI intelligence
    ai_generated             = Column(Boolean, default=False)
    ai_confidence_score      = Column(Float, nullable=True)    # 0–1
    ai_tags                  = Column(JSON, default=list)
    ai_summary               = Column(Text, nullable=True)
    ai_detected_category     = Column(String(100), nullable=True)
    ai_payment_prediction    = Column(JSON, default=dict)      # {date, probability, risk}

    # Payment intelligence
    predicted_payment_date   = Column(DateTime(timezone=True), nullable=True)
    predicted_delay_days     = Column(Integer, nullable=True)
    collection_risk_score    = Column(Float, default=0.0)

    # Automation
    auto_reminder_enabled    = Column(Boolean, default=True)
    auto_followup_enabled    = Column(Boolean, default=False)
    workflow_id              = Column(Integer, ForeignKey("workflows.id"), nullable=True)

    # Client tracking
    view_count               = Column(Integer, default=0)
    last_viewed_at           = Column(DateTime(timezone=True), nullable=True)
    opened_by_client         = Column(Boolean, default=False)
    client_device_info       = Column(JSON, default=dict)
    client_ip                = Column(String(50), nullable=True)

    # UX / Branding
    template_name            = Column(String(50), default="modern")
    accent_color             = Column(String(10), nullable=True)
    custom_css               = Column(Text, nullable=True)
    public_share_token       = Column(String(100), unique=True, nullable=True, index=True)
    logo_url                 = Column(String(500), nullable=True)

    # Business metrics
    profit_margin            = Column(Float, nullable=True)
    processing_time_seconds  = Column(Float, nullable=True)
    conversion_source        = Column(String(50), nullable=True)

    # Relationships
    user                     = relationship("User", back_populates="invoices")
    team                     = relationship("Team", back_populates="invoices")
    client                   = relationship("Client", back_populates="invoices")
    items                    = relationship("InvoiceItem", back_populates="invoice", cascade="all, delete-orphan")
    payments                 = relationship("Payment", back_populates="invoice")
    reminders                = relationship("Reminder", back_populates="invoice")
    activities               = relationship("Activity", back_populates="invoice")
    workflow                 = relationship("Workflow", foreign_keys=[workflow_id])

    __table_args__ = (
        Index("ix_invoices_user_status", "user_id", "status"),
        Index("ix_invoices_team_due", "team_id", "due_date"),
        Index("ix_invoices_status_due", "status", "due_date"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  INVOICE ITEM
# ═══════════════════════════════════════════════════════════════════════════════

class InvoiceItem(TimestampMixin, Base):
    __tablename__ = "invoice_items"

    id              = Column(Integer, primary_key=True, index=True)
    invoice_id      = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)

    description     = Column(String(500), nullable=False)
    category        = Column(String(100), nullable=True)
    sku             = Column(String(100), nullable=True)
    unit            = Column(String(50), nullable=True)          # hrs, units, days
    quantity        = Column(Numeric(10, 3), nullable=False)
    unit_price      = Column(Numeric(14, 2), nullable=False)
    discount        = Column(Numeric(5, 2), default=0)           # percentage
    tax_percentage  = Column(Numeric(5, 2), default=0)
    total_price     = Column(Numeric(14, 2), nullable=False)
    ai_generated    = Column(Boolean, default=False)
    sort_order      = Column(Integer, default=0)

    invoice         = relationship("Invoice", back_populates="items")


# ═══════════════════════════════════════════════════════════════════════════════
#  PAYMENT
# ═══════════════════════════════════════════════════════════════════════════════

class Payment(TimestampMixin, Base):
    __tablename__ = "payments"

    id                  = Column(Integer, primary_key=True, index=True)
    invoice_id          = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    user_id             = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    amount              = Column(Numeric(14, 2), nullable=False)
    currency            = Column(String(10), default="USD")
    exchange_rate       = Column(Float, default=1.0)

    method              = Column(String(50), nullable=False)   # cash, stripe, paypal…
    gateway             = Column(String(50), nullable=True)
    gateway_transaction_id = Column(String(200), nullable=True, unique=True)
    gateway_status      = Column(String(50), nullable=True)
    receipt_url         = Column(String(500), nullable=True)

    paid_at             = Column(DateTime(timezone=True), nullable=True)
    notes               = Column(Text, nullable=True)

    # Refund tracking
    refunded            = Column(Boolean, default=False)
    refund_amount       = Column(Numeric(14, 2), nullable=True)
    refund_reason       = Column(String(300), nullable=True)
    refunded_at         = Column(DateTime(timezone=True), nullable=True)

    # Failure tracking
    failure_reason      = Column(String(300), nullable=True)
    retry_count         = Column(Integer, default=0)

    # AI
    processed_by_ai     = Column(Boolean, default=False)

    invoice             = relationship("Invoice", back_populates="payments")


# ═══════════════════════════════════════════════════════════════════════════════
#  REMINDER
# ═══════════════════════════════════════════════════════════════════════════════

class Reminder(TimestampMixin, Base):
    __tablename__ = "reminders"

    id                       = Column(Integer, primary_key=True, index=True)
    invoice_id               = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    client_id                = Column(Integer, ForeignKey("clients.id"), nullable=True, index=True)
    user_id                  = Column(Integer, ForeignKey("users.id"), nullable=False)

    reminder_type            = Column(String(50), nullable=False)  # friendly|firm|urgent…
    channel                  = Column(String(30), default="email") # email|whatsapp|sms
    subject                  = Column(String(255), nullable=True)
    body                     = Column(Text, nullable=False)
    scheduled_at             = Column(DateTime(timezone=True), nullable=True)
    sent_at                  = Column(DateTime(timezone=True), nullable=True)
    status                   = Column(String(30), default="pending")

    # Delivery tracking
    opened                   = Column(Boolean, default=False)
    opened_at                = Column(DateTime(timezone=True), nullable=True)
    clicked                  = Column(Boolean, default=False)
    clicked_at               = Column(DateTime(timezone=True), nullable=True)
    replied                  = Column(Boolean, default=False)
    delivery_status          = Column(String(50), nullable=True)

    # AI scoring
    ai_tone_score            = Column(Float, nullable=True)
    ai_effectiveness_score   = Column(Float, nullable=True)
    predicted_response_rate  = Column(Float, nullable=True)
    ai_generated             = Column(Boolean, default=False)

    invoice                  = relationship("Invoice", back_populates="reminders")
    client                   = relationship("Client", back_populates="reminders")


# ═══════════════════════════════════════════════════════════════════════════════
#  RECURRING BILLING
# ═══════════════════════════════════════════════════════════════════════════════

class RecurringBilling(AuditMixin, Base):
    __tablename__ = "recurring_billings"

    id                      = Column(Integer, primary_key=True, index=True)
    user_id                 = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    client_id               = Column(Integer, ForeignKey("clients.id"), nullable=False, index=True)
    team_id                 = Column(Integer, ForeignKey("teams.id"), nullable=True)

    title                   = Column(String(150), nullable=False)
    description             = Column(Text, nullable=True)
    amount                  = Column(Numeric(14, 2), nullable=False)
    currency                = Column(String(10), default="USD")

    frequency               = Column(String(50), nullable=False)  # weekly|monthly|quarterly|yearly
    next_billing_date       = Column(DateTime(timezone=True), nullable=False, index=True)
    last_generated_at       = Column(DateTime(timezone=True), nullable=True)
    end_date                = Column(DateTime(timezone=True), nullable=True)

    is_active               = Column(Boolean, default=True, nullable=False)
    auto_send               = Column(Boolean, default=False)
    dynamic_amount_enabled  = Column(Boolean, default=False)

    failure_count           = Column(Integer, default=0)
    last_failure_reason     = Column(String(300), nullable=True)
    total_generated         = Column(Integer, default=0)


# ═══════════════════════════════════════════════════════════════════════════════
#  WORKFLOW
# ═══════════════════════════════════════════════════════════════════════════════

class Workflow(AuditMixin, Base):
    __tablename__ = "workflows"

    id                  = Column(Integer, primary_key=True, index=True)
    team_id             = Column(Integer, ForeignKey("teams.id"), nullable=True, index=True)
    user_id             = Column(Integer, ForeignKey("users.id"), nullable=False)

    name                = Column(String(150), nullable=False)
    description         = Column(Text, nullable=True)
    trigger             = Column(String(100), nullable=False)   # WorkflowTrigger value
    actions             = Column(JSON, default=list)            # list of action dicts
    conditions          = Column(JSON, default=dict)
    is_active           = Column(Boolean, default=True, index=True)

    # Metadata
    workflow_version    = Column(Integer, default=1)
    ai_generated        = Column(Boolean, default=False)
    visual_layout       = Column(JSON, default=dict)            # for visual canvas editor

    # Analytics
    run_count           = Column(Integer, default=0)
    success_count       = Column(Integer, default=0)
    failure_count       = Column(Integer, default=0)
    success_rate        = Column(Float, default=0.0)
    last_run_at         = Column(DateTime(timezone=True), nullable=True)

    team                = relationship("Team", back_populates="workflows")
    runs                = relationship("WorkflowRun", back_populates="workflow")


class WorkflowRun(TimestampMixin, Base):
    __tablename__ = "workflow_runs"

    id                  = Column(Integer, primary_key=True, index=True)
    workflow_id         = Column(Integer, ForeignKey("workflows.id"), nullable=False, index=True)

    status              = Column(String(30), default="running")  # running|success|failed
    trigger_payload     = Column(JSON, default=dict)
    result              = Column(JSON, default=dict)
    error_message       = Column(Text, nullable=True)
    steps_completed     = Column(Integer, default=0)
    steps_total         = Column(Integer, default=0)
    ai_actions_taken    = Column(JSON, default=list)
    execution_time_ms   = Column(Float, nullable=True)
    started_at          = Column(DateTime(timezone=True), server_default=func.now())
    finished_at         = Column(DateTime(timezone=True), nullable=True)

    workflow            = relationship("Workflow", back_populates="runs")


# ═══════════════════════════════════════════════════════════════════════════════
#  NOTIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

class Notification(TimestampMixin, Base):
    __tablename__ = "notifications"

    id              = Column(Integer, primary_key=True, index=True)
    user_id         = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    team_id         = Column(Integer, ForeignKey("teams.id"), nullable=True)

    title           = Column(String(255), nullable=False)
    message         = Column(Text, nullable=False)
    notification_type = Column(String(50), nullable=False)
    category        = Column(String(50), nullable=True)

    priority        = Column(String(20), default="normal")    # low|normal|high|critical
    action_url      = Column(String(500), nullable=True)
    icon            = Column(String(50), nullable=True)
    color           = Column(String(20), nullable=True)
    extra_data      = Column(JSON, default=dict)

    is_read         = Column(Boolean, default=False, index=True)
    read_at         = Column(DateTime(timezone=True), nullable=True)
    seen_at         = Column(DateTime(timezone=True), nullable=True)
    broadcasted     = Column(Boolean, default=False)           # sent over WebSocket
    expires_at      = Column(DateTime(timezone=True), nullable=True)

    user            = relationship("User", back_populates="notifications")

    __table_args__ = (
        Index("ix_notifications_user_read", "user_id", "is_read"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  ACTIVITY  (realtime timeline)
# ═══════════════════════════════════════════════════════════════════════════════

class Activity(TimestampMixin, Base):
    __tablename__ = "activities"

    id                      = Column(Integer, primary_key=True, index=True)
    user_id                 = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    team_id                 = Column(Integer, ForeignKey("teams.id"), nullable=True, index=True)
    invoice_id              = Column(Integer, ForeignKey("invoices.id"), nullable=True)

    activity_type           = Column(String(50), nullable=False, index=True)
    entity_type             = Column(String(50), nullable=True)
    entity_id               = Column(Integer, nullable=True)
    entity_name             = Column(String(200), nullable=True)
    entity_snapshot         = Column(JSON, default=dict)

    description             = Column(Text, nullable=True)
    ai_generated_description = Column(Text, nullable=True)
    importance_score        = Column(Float, default=0.5)     # 0–1 for feed ranking

    event_data              = Column(JSON, default=dict)
    ip_address              = Column(String(50), nullable=True)

    user                    = relationship("User", back_populates="activities")
    invoice                 = relationship("Invoice", back_populates="activities")

    __table_args__ = (
        Index("ix_activities_team_type", "team_id", "activity_type"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  EXPENSE
# ═══════════════════════════════════════════════════════════════════════════════

class Expense(AuditMixin, Base):
    __tablename__ = "expenses"

    id                      = Column(Integer, primary_key=True, index=True)
    user_id                 = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    team_id                 = Column(Integer, ForeignKey("teams.id"), nullable=True)

    title                   = Column(String(200), nullable=False)
    description             = Column(Text, nullable=True)
    amount                  = Column(Numeric(14, 2), nullable=False)
    currency                = Column(String(10), default="USD")
    category                = Column(String(100), nullable=True)
    subcategory             = Column(String(100), nullable=True)
    expense_date            = Column(DateTime(timezone=True), nullable=False)
    vendor                  = Column(String(150), nullable=True)
    receipt_url             = Column(String(500), nullable=True)

    # AI + OCR
    receipt_text            = Column(Text, nullable=True)       # raw OCR output
    receipt_metadata        = Column(JSON, default=dict)
    ocr_processed           = Column(Boolean, default=False)
    ai_confidence           = Column(Float, nullable=True)
    predicted_tax_category  = Column(String(100), nullable=True)
    tax_deductible          = Column(Boolean, nullable=True)
    recurring_detected      = Column(Boolean, default=False)

    user                    = relationship("User", back_populates="expenses")


# ═══════════════════════════════════════════════════════════════════════════════
#  REPORT
# ═══════════════════════════════════════════════════════════════════════════════

class Report(TimestampMixin, Base):
    __tablename__ = "reports"

    id                  = Column(Integer, primary_key=True, index=True)
    user_id             = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    team_id             = Column(Integer, ForeignKey("teams.id"), nullable=True)

    title               = Column(String(255), nullable=False)
    report_type         = Column(String(50), nullable=False)
    report_format       = Column(String(20), nullable=False)
    parameters          = Column(JSON, default=dict)       # filters, date range, etc.
    file_url            = Column(String(500), nullable=True)
    file_size_bytes     = Column(BigInteger, nullable=True)

    status              = Column(String(30), default="pending")
    generated_by_ai     = Column(Boolean, default=False)
    generation_time_ms  = Column(Float, nullable=True)

    download_count      = Column(Integer, default=0)
    last_downloaded_at  = Column(DateTime(timezone=True), nullable=True)
    scheduled           = Column(Boolean, default=False)
    schedule_cron       = Column(String(50), nullable=True)

    user                = relationship("User", back_populates="reports")


# ═══════════════════════════════════════════════════════════════════════════════
#  AI CONVERSATION
# ═══════════════════════════════════════════════════════════════════════════════

class AIConversation(AuditMixin, Base):
    __tablename__ = "ai_conversations"

    id                      = Column(Integer, primary_key=True, index=True)
    user_id                 = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    team_id                 = Column(Integer, ForeignKey("teams.id"), nullable=True)

    conversation_title      = Column(String(200), nullable=True)
    memory_key              = Column(String(100), unique=True, index=True, nullable=True)
    intent                  = Column(String(100), nullable=True)   # last detected intent

    messages                = Column(JSON, default=list)           # full message history
    conversation_summary    = Column(Text, nullable=True)          # AI-compressed memory
    referenced_entities     = Column(JSON, default=dict)           # {invoices: [], clients: []}

    # Metrics
    message_count           = Column(Integer, default=0)
    tokens_used             = Column(Integer, default=0)
    total_cost_usd          = Column(Float, default=0.0)
    response_time_ms        = Column(Float, nullable=True)
    feedback_score          = Column(Float, nullable=True)         # 1–5 user rating
    ai_provider             = Column(String(50), nullable=True)

    last_activity_at        = Column(DateTime(timezone=True), server_default=func.now())
    is_archived             = Column(Boolean, default=False)

    user                    = relationship("User", back_populates="conversations")
    messages_log            = relationship("AIMessage", back_populates="conversation", cascade="all, delete-orphan")


class AIMessage(TimestampMixin, Base):
    __tablename__ = "ai_messages"

    id                  = Column(Integer, primary_key=True, index=True)
    conversation_id     = Column(Integer, ForeignKey("ai_conversations.id"), nullable=False, index=True)

    role                = Column(String(20), nullable=False)   # system|user|assistant
    content             = Column(Text, nullable=False)
    tokens              = Column(Integer, nullable=True)
    latency_ms          = Column(Float, nullable=True)
    provider            = Column(String(50), nullable=True)
    model               = Column(String(100), nullable=True)
    intent              = Column(String(100), nullable=True)
    msg_data            = Column(JSON, default=dict)

    conversation        = relationship("AIConversation", back_populates="messages_log")


# ═══════════════════════════════════════════════════════════════════════════════
#  BUSINESS INSIGHT
# ═══════════════════════════════════════════════════════════════════════════════

class BusinessInsight(TimestampMixin, Base):
    __tablename__ = "business_insights"

    id                  = Column(Integer, primary_key=True, index=True)
    team_id             = Column(Integer, ForeignKey("teams.id"), nullable=True, index=True)
    user_id             = Column(Integer, ForeignKey("users.id"), nullable=False)

    insight_type        = Column(String(50), nullable=False, index=True)
    title               = Column(String(255), nullable=False)
    summary             = Column(Text, nullable=False)
    severity            = Column(String(20), default="medium")
    trend_direction     = Column(String(20), nullable=True)  # up|down|flat

    confidence_score    = Column(Float, default=0.0)
    impact_score        = Column(Float, default=0.0)
    actionable_steps    = Column(JSON, default=list)

    is_read             = Column(Boolean, default=False)
    is_actioned         = Column(Boolean, default=False)
    expires_at          = Column(DateTime(timezone=True), nullable=True)
    extra_data          = Column(JSON, default=dict)

    team                = relationship("Team", back_populates="insights")

    __table_args__ = (
        Index("ix_insights_team_type", "team_id", "insight_type"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  DASHBOARD WIDGET
# ═══════════════════════════════════════════════════════════════════════════════

class DashboardWidget(TimestampMixin, Base):
    __tablename__ = "dashboard_widgets"

    id                  = Column(Integer, primary_key=True, index=True)
    user_id             = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    widget_type         = Column(String(50), nullable=False)
    title               = Column(String(150), nullable=True)
    config              = Column(JSON, default=dict)       # chart type, filters, colors
    data_source         = Column(String(100), nullable=True)
    position_x          = Column(Integer, default=0)
    position_y          = Column(Integer, default=0)
    width               = Column(Integer, default=2)
    height              = Column(Integer, default=2)

    refresh_interval    = Column(Integer, default=300)     # seconds
    ai_personalized     = Column(Boolean, default=False)
    animation_enabled   = Column(Boolean, default=True)
    minimized           = Column(Boolean, default=False)
    theme               = Column(String(30), default="default")
    is_visible          = Column(Boolean, default=True)

    user                = relationship("User", back_populates="widgets")


# ═══════════════════════════════════════════════════════════════════════════════
#  AI RECOMMENDATION  (new table)
# ═══════════════════════════════════════════════════════════════════════════════

class AIRecommendation(TimestampMixin, Base):
    __tablename__ = "ai_recommendations"

    id                  = Column(Integer, primary_key=True, index=True)
    team_id             = Column(Integer, ForeignKey("teams.id"), nullable=True, index=True)
    user_id             = Column(Integer, ForeignKey("users.id"), nullable=False)

    title               = Column(String(255), nullable=False)
    description         = Column(Text, nullable=False)
    category            = Column(String(100), nullable=False)  # AIRecommendationTypes value
    priority            = Column(String(20), default="medium")

    estimated_impact    = Column(String(200), nullable=True)   # e.g. "+$2,400 MRR"
    effort_level        = Column(String(20), default="medium")
    action_steps        = Column(JSON, default=list)
    context_data        = Column(JSON, default=dict)

    is_accepted         = Column(Boolean, nullable=True)       # None=pending
    is_dismissed        = Column(Boolean, default=False)
    accepted_at         = Column(DateTime(timezone=True), nullable=True)
    outcome_reported    = Column(Boolean, default=False)
    outcome_notes       = Column(Text, nullable=True)

    expires_at          = Column(DateTime(timezone=True), nullable=True)
    ai_provider         = Column(String(50), nullable=True)
    confidence          = Column(Float, default=0.0)

    team                = relationship("Team", back_populates="recommendations")

    __table_args__ = (
        Index("ix_ai_recs_team_priority", "team_id", "priority"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  INTEGRATION  (third-party connections)
# ═══════════════════════════════════════════════════════════════════════════════

class Integration(AuditMixin, Base):
    __tablename__ = "integrations"

    id              = Column(Integer, primary_key=True, index=True)
    team_id         = Column(Integer, ForeignKey("teams.id"), nullable=False, index=True)
    user_id         = Column(Integer, ForeignKey("users.id"), nullable=False)

    provider        = Column(String(50), nullable=False)   # stripe|slack|quickbooks|xero…
    display_name    = Column(String(100), nullable=True)
    is_active       = Column(Boolean, default=True)

    access_token    = Column(Text, nullable=True)          # store encrypted in production
    refresh_token   = Column(Text, nullable=True)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)
    scopes          = Column(JSON, default=list)
    config          = Column(JSON, default=dict)
    webhook_url     = Column(String(500), nullable=True)

    last_synced_at  = Column(DateTime(timezone=True), nullable=True)
    sync_status     = Column(String(30), nullable=True)
    error_count     = Column(Integer, default=0)
    last_error      = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("team_id", "provider", name="uq_team_integration"),
    )
