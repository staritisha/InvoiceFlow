# ═══════════════════════════════════════════════════════════════════════════════
#  AI Invoice Intelligence Platform — app/schemas.py
#  Pydantic v2 request/response contracts for every API surface:
#  auth, users, teams, clients, invoices, payments, reminders, AI, analytics,
#  voice, workflows, notifications, dashboard, and reports.
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


# ═══════════════════════════════════════════════════════════════════════════════
#  GLOBAL BASE CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

class AppBase(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        arbitrary_types_allowed=True,
        use_enum_values=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  REUSABLE BASE SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class TimestampSchema(AppBase):
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PaginationMeta(AppBase):
    page:          int
    limit:         int
    total:         int
    pages:         int
    has_next:      bool
    has_previous:  bool


class PaginatedResponse(AppBase):
    data:       list[Any]
    pagination: PaginationMeta
    success:    bool = True


class APIResponse(AppBase):
    success:    bool = True
    message:    str = "OK"
    data:       Optional[Any] = None
    request_id: Optional[str] = None


class AIMetadataSchema(AppBase):
    ai_generated:     bool    = False
    confidence_score: float   = Field(default=0.0, ge=0.0, le=1.0)
    ai_tags:          list[str] = Field(default_factory=list)
    reasoning:        Optional[str] = None
    prediction:       Optional[dict] = None
    provider:         Optional[str] = None
    tokens_used:      Optional[int] = None


# ═══════════════════════════════════════════════════════════════════════════════
#  AUTH SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class LoginRequest(AppBase):
    email:       EmailStr
    password:    str
    remember_me: bool = False
    device_info: Optional[dict] = None


class RefreshTokenRequest(AppBase):
    refresh_token: str


class PasswordChangeRequest(AppBase):
    current_password: str
    new_password:     str = Field(min_length=8)


class PasswordResetRequest(AppBase):
    email: EmailStr


class PasswordResetConfirm(AppBase):
    token:        str
    new_password: str = Field(min_length=8)


class TokenPermissions(AppBase):
    role:        str
    permissions: list[str] = Field(default_factory=list)
    tier:        str = "free"


class Token(AppBase):
    access_token:  str
    refresh_token: str
    token_type:    str = "bearer"
    expires_in:    int = 3600   # seconds
    user:          Optional["UserOut"] = None
    permissions:   Optional[TokenPermissions] = None
    subscription:  Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
#  USER SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class UserBase(AppBase):
    full_name:        str = Field(min_length=1, max_length=100)
    email:            EmailStr
    timezone:         str = "UTC"
    language:         str = "en"
    theme_preference: str = "light"
    avatar_url:       Optional[str] = None
    phone:            Optional[str] = None
    country:          Optional[str] = None


class UserCreate(UserBase):
    password:          str = Field(min_length=8)
    business_name:     Optional[str] = None
    subscription_tier: str = "free"

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class UserUpdate(AppBase):
    full_name:               Optional[str] = None
    timezone:                Optional[str] = None
    language:                Optional[str] = None
    theme_preference:        Optional[str] = None
    avatar_url:              Optional[str] = None
    phone:                   Optional[str] = None
    notification_preferences: Optional[dict] = None
    dashboard_layout:        Optional[dict] = None
    voice_enabled:           Optional[bool] = None
    command_palette_enabled: Optional[bool] = None
    ai_memory_enabled:       Optional[bool] = None


class UserOut(TimestampSchema):
    id:               int
    full_name:        str
    email:            EmailStr
    role:             str
    is_active:        bool
    team_id:          Optional[int] = None
    timezone:         Optional[str] = None
    language:         Optional[str] = None
    theme_preference: Optional[str] = None
    avatar_url:       Optional[str] = None
    onboarding_completed: bool = False
    ai_usage_count:   int = 0
    voice_enabled:    bool = False
    email_verified:   bool = False
    last_login_at:    Optional[datetime] = None
    subscription_tier: Optional[str] = None   # pulled from team


class UserDashboardProfile(AppBase):
    user:           UserOut
    kpis:           dict = Field(default_factory=dict)
    widgets:        list["DashboardWidgetOut"] = Field(default_factory=list)
    notifications:  list["NotificationOut"] = Field(default_factory=list)
    quick_actions:  list[str] = Field(default_factory=list)
    ai_tips:        list[str] = Field(default_factory=list)
    unread_count:   int = 0
    active_invoices: int = 0


# ═══════════════════════════════════════════════════════════════════════════════
#  TEAM SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class TeamCreate(AppBase):
    name:         str = Field(min_length=1, max_length=150)
    industry:     Optional[str] = None
    company_size: Optional[str] = None
    country:      Optional[str] = None
    currency:     str = "USD"
    timezone:     str = "UTC"
    branding:     Optional[dict] = None


class TeamUpdate(AppBase):
    name:              Optional[str] = None
    industry:          Optional[str] = None
    company_size:      Optional[str] = None
    ai_preferences:    Optional[dict] = None
    branding:          Optional[dict] = None
    feature_overrides: Optional[dict] = None


class TeamOut(TimestampSchema):
    id:                int
    name:              str
    slug:              str
    subscription_tier: str
    industry:          Optional[str] = None
    company_size:      Optional[str] = None
    country:           Optional[str] = None
    currency:          str = "USD"
    member_count:      int = 0
    ai_preferences:    Optional[dict] = None
    branding:          Optional[dict] = None
    ai_health_score:   Optional[float] = None


class TeamInviteCreate(AppBase):
    email:   EmailStr
    role:    str = "member"
    message: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
#  CLIENT SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class ClientBase(AppBase):
    name:                    str = Field(min_length=1, max_length=150)
    email:                   Optional[EmailStr] = None
    phone:                   Optional[str] = None
    address:                 Optional[str] = None
    city:                    Optional[str] = None
    country:                 Optional[str] = None
    currency:                str = "USD"
    company_name:            Optional[str] = None
    industry:                Optional[str] = None
    website:                 Optional[str] = None
    linkedin_url:            Optional[str] = None
    tax_id:                  Optional[str] = None
    preferred_contact_method: str = "email"
    tags:                    list[str] = Field(default_factory=list)


class ClientCreate(ClientBase):
    credit_limit:      Optional[Decimal] = None
    payment_terms_days: int = 30


class ClientUpdate(AppBase):
    name:                    Optional[str] = None
    email:                   Optional[EmailStr] = None
    phone:                   Optional[str] = None
    address:                 Optional[str] = None
    city:                    Optional[str] = None
    country:                 Optional[str] = None
    currency:                Optional[str] = None
    company_name:            Optional[str] = None
    industry:                Optional[str] = None
    website:                 Optional[str] = None
    credit_limit:            Optional[Decimal] = None
    payment_terms_days:      Optional[int] = None
    preferred_contact_method: Optional[str] = None


class ClientRiskScore(AppBase):
    client_id:               int
    risk_score:              float = Field(ge=0, le=100)
    risk_category:           str   # low|medium|high
    late_payment_probability: float = Field(ge=0.0, le=1.0)
    collection_priority:     int = 0
    recommended_action:      str
    credit_limit_suggestion: Optional[Decimal] = None
    confidence:              float = Field(default=0.0, ge=0.0, le=1.0)


class ClientSummary(AppBase):
    client_id:        int
    name:             str
    total_invoiced:   Decimal
    total_paid:       Decimal
    outstanding:      Decimal
    avg_payment_days: float
    payment_behavior: str   # excellent|good|fair|poor
    last_invoice_date: Optional[datetime] = None
    predicted_ltv:    Optional[Decimal] = None
    ai_summary:       Optional[str] = None
    risk_category:    str = "low"


class ClientOut(TimestampSchema):
    id:                      int
    user_id:                 int
    name:                    str
    email:                   Optional[str] = None
    phone:                   Optional[str] = None
    address:                 Optional[str] = None
    city:                    Optional[str] = None
    country:                 Optional[str] = None
    currency:                str = "USD"
    company_name:            Optional[str] = None
    industry:                Optional[str] = None
    website:                 Optional[str] = None
    preferred_contact_method: str = "email"
    payment_terms_days:      int = 30
    credit_limit:            Optional[Decimal] = None
    risk_score:              float = 0.0
    risk_category:           str = "low"
    payment_reliability:     float = 100.0
    relationship_strength:   str = "new"
    ai_summary:              Optional[str] = None
    predicted_ltv:           Optional[Decimal] = None
    last_contacted_at:       Optional[datetime] = None
    next_followup_date:      Optional[datetime] = None


class ClientAnalyticsSchema(AppBase):
    client_id:       int
    payment_history: list[dict] = Field(default_factory=list)
    invoice_trends:  list[dict] = Field(default_factory=list)
    risk_trends:     list[dict] = Field(default_factory=list)
    engagement_score: float = 0.0
    on_time_rate:    float = 0.0
    avg_invoice_value: Decimal = Decimal("0")


# ═══════════════════════════════════════════════════════════════════════════════
#  INVOICE ITEM SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class InvoiceItemBase(AppBase):
    description:    str = Field(min_length=1, max_length=500)
    quantity:       Decimal = Field(gt=0)
    unit_price:     Decimal = Field(ge=0)
    category:       Optional[str] = None
    unit:           Optional[str] = None
    sku:            Optional[str] = None
    discount:       Decimal = Field(default=Decimal("0"), ge=0, le=100)
    tax_percentage: Decimal = Field(default=Decimal("0"), ge=0, le=100)


class InvoiceItemCreate(InvoiceItemBase):
    pass


class InvoiceItemOut(InvoiceItemBase):
    id:           int
    invoice_id:   int
    total_price:  Decimal
    ai_generated: bool = False
    sort_order:   int = 0


# ═══════════════════════════════════════════════════════════════════════════════
#  INVOICE SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class InvoiceBase(AppBase):
    client_id:   Optional[int] = None
    title:       Optional[str] = None
    currency:    str = "USD"
    template_name: str = "modern"
    accent_color: Optional[str] = None
    notes:       Optional[str] = Field(default=None, max_length=2000)
    terms:       Optional[str] = None
    issue_date:  Optional[datetime] = None
    due_date:    Optional[datetime] = None


class InvoiceCreate(InvoiceBase):
    items:                 list[InvoiceItemCreate] = Field(default_factory=list)
    tax_rate:              Decimal = Field(default=Decimal("0"), ge=0, le=100)
    discount_amount:       Decimal = Field(default=Decimal("0"), ge=0)
    auto_reminder_enabled: bool = True
    auto_followup_enabled: bool = False
    workflow_id:           Optional[int] = None

    @field_validator("items")
    @classmethod
    def at_least_one_item(cls, v: list) -> list:
        if len(v) == 0:
            raise ValueError("An invoice must have at least one line item")
        return v


class InvoiceUpdate(AppBase):
    title:                 Optional[str] = None
    client_id:             Optional[int] = None
    status:                Optional[str] = None
    currency:              Optional[str] = None
    template_name:         Optional[str] = None
    accent_color:          Optional[str] = None
    notes:                 Optional[str] = None
    terms:                 Optional[str] = None
    due_date:              Optional[datetime] = None
    tax_rate:              Optional[Decimal] = None
    discount_amount:       Optional[Decimal] = None
    auto_reminder_enabled: Optional[bool] = None
    auto_followup_enabled: Optional[bool] = None
    items:                 Optional[list[InvoiceItemCreate]] = None


class InvoiceStatusUpdate(AppBase):
    status: str

    @field_validator("status")
    @classmethod
    def valid_status(cls, v: str) -> str:
        valid = {"draft","pending","sent","viewed","partially_paid","paid","overdue","cancelled","failed","refunded"}
        if v not in valid:
            raise ValueError(f"Invalid status '{v}'")
        return v


class InvoiceAIFields(AppBase):
    ai_generated:          bool = False
    ai_confidence_score:   Optional[float] = None
    ai_tags:               list[str] = Field(default_factory=list)
    ai_summary:            Optional[str] = None
    ai_detected_category:  Optional[str] = None
    predicted_payment_date: Optional[datetime] = None
    collection_risk_score: float = 0.0


class InvoicePredictionSchema(AppBase):
    invoice_id:            int
    predicted_payment_date: Optional[datetime] = None
    delay_probability:     float = Field(ge=0.0, le=1.0)
    predicted_delay_days:  Optional[int] = None
    risk_level:            str = "low"
    confidence_score:      float = Field(ge=0.0, le=1.0)
    recommended_action:    Optional[str] = None


class InvoiceOut(TimestampSchema):
    id:                    int
    invoice_number:        str
    user_id:               int
    client_id:             Optional[int] = None
    client_name:           Optional[str] = None
    title:                 Optional[str] = None
    status:                str
    currency:              str = "USD"
    issue_date:            Optional[datetime] = None
    due_date:              Optional[datetime] = None
    notes:                 Optional[str] = None

    subtotal:              Decimal
    tax_rate:              Decimal = Decimal("0")
    tax_amount:            Decimal = Decimal("0")
    discount_amount:       Decimal = Decimal("0")
    total_amount:          Decimal
    amount_paid:           Decimal = Decimal("0")
    amount_due:            Decimal = Decimal("0")

    # Computed fields
    payment_percentage:    float = 0.0
    is_overdue:            bool = False
    days_until_due:        Optional[int] = None

    # AI
    ai_generated:          bool = False
    ai_priority:           Optional[str] = None
    collection_risk_score: float = 0.0
    predicted_payment_date: Optional[datetime] = None

    # Tracking
    view_count:            int = 0
    template_name:         str = "modern"
    public_share_token:    Optional[str] = None
    auto_reminder_enabled: bool = True

    items:                 list[InvoiceItemOut] = Field(default_factory=list)


class InvoiceListItem(AppBase):
    """Lightweight schema for list/table views — avoids fetching items."""
    id:             int
    invoice_number: str
    client_name:    Optional[str] = None
    status:         str
    currency:       str = "USD"
    total_amount:   Decimal
    amount_due:     Decimal = Decimal("0")
    due_date:       Optional[datetime] = None
    is_overdue:     bool = False
    ai_priority:    Optional[str] = None
    view_count:     int = 0
    created_at:     Optional[datetime] = None


class InvoiceThemeOut(AppBase):
    theme_name:  str
    preview_url: Optional[str] = None
    accent_color: Optional[str] = None
    primary_color: Optional[str] = None
    font:         Optional[str] = None


class InvoiceAnalyticsSchema(AppBase):
    total_sent:          int = 0
    paid:                int = 0
    overdue:             int = 0
    draft:               int = 0
    partially_paid:      int = 0
    collection_rate:     float = 0.0
    avg_payment_time:    float = 0.0   # days
    total_revenue:       Decimal = Decimal("0")
    total_outstanding:   Decimal = Decimal("0")
    avg_invoice_value:   Decimal = Decimal("0")


# ═══════════════════════════════════════════════════════════════════════════════
#  PAYMENT SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class PaymentBase(AppBase):
    invoice_id:     int
    amount:         Decimal = Field(gt=0)
    method:         str
    currency:       str = "USD"
    notes:          Optional[str] = None


class PaymentCreate(PaymentBase):
    transaction_id: Optional[str] = None
    gateway:        Optional[str] = None
    paid_at:        Optional[datetime] = None


class PaymentOut(TimestampSchema):
    id:                    int
    invoice_id:            int
    amount:                Decimal
    currency:              str
    method:                str
    gateway:               Optional[str] = None
    gateway_transaction_id: Optional[str] = None
    gateway_status:        Optional[str] = None
    receipt_url:           Optional[str] = None
    paid_at:               Optional[datetime] = None
    refunded:              bool = False
    refund_amount:         Optional[Decimal] = None
    processed_by_ai:       bool = False


class StripeCheckoutCreate(AppBase):
    invoice_id:  int
    success_url: Optional[str] = None
    cancel_url:  Optional[str] = None


class StripeCheckoutOut(AppBase):
    checkout_url:  str
    session_id:    str
    expires_at:    Optional[datetime] = None


# ═══════════════════════════════════════════════════════════════════════════════
#  REMINDER SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class ReminderCreate(AppBase):
    invoice_id:    int
    reminder_type: str = "friendly"
    channel:       str = "email"
    scheduled_at:  Optional[datetime] = None
    subject:       Optional[str] = None
    body:          Optional[str] = None


class ReminderOut(TimestampSchema):
    id:                     int
    invoice_id:             int
    client_id:              Optional[int] = None
    reminder_type:          str
    channel:                str
    subject:                Optional[str] = None
    body:                   str
    status:                 str
    scheduled_at:           Optional[datetime] = None
    sent_at:                Optional[datetime] = None
    opened:                 bool = False
    clicked:                bool = False
    replied:                bool = False
    ai_generated:           bool = False
    predicted_response_rate: Optional[float] = None


class AIReminderRequest(AppBase):
    invoice_id:    int
    reminder_type: str = "friendly"
    channel:       str = "email"
    context:       Optional[dict] = None


class AIReminderResponse(AppBase):
    invoice_id:       int
    reminder_type:    str
    subject:          str
    body:             str
    cta_text:         Optional[str] = None
    tone_used:        Optional[str] = None
    send_at_suggestion: Optional[str] = None
    escalate_after_days: int = 7
    ai_metadata:      AIMetadataSchema = Field(default_factory=AIMetadataSchema)


# ═══════════════════════════════════════════════════════════════════════════════
#  RECURRING BILLING SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class RecurringBillingCreate(AppBase):
    client_id:         int
    title:             str = Field(min_length=1, max_length=150)
    description:       Optional[str] = None
    amount:            Decimal = Field(gt=0)
    currency:          str = "USD"
    frequency:         str   # weekly|monthly|quarterly|yearly
    next_billing_date: datetime
    end_date:          Optional[datetime] = None
    auto_send:         bool = False

    @field_validator("frequency")
    @classmethod
    def valid_frequency(cls, v: str) -> str:
        valid = {"weekly", "monthly", "quarterly", "yearly"}
        if v not in valid:
            raise ValueError(f"frequency must be one of {valid}")
        return v


class RecurringBillingOut(TimestampSchema):
    id:                int
    user_id:           int
    client_id:         int
    title:             str
    amount:            Decimal
    currency:          str
    frequency:         str
    next_billing_date: datetime
    end_date:          Optional[datetime] = None
    is_active:         bool
    auto_send:         bool
    total_generated:   int = 0
    failure_count:     int = 0
    last_generated_at: Optional[datetime] = None


# ═══════════════════════════════════════════════════════════════════════════════
#  WORKFLOW SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class WorkflowCreate(AppBase):
    name:        str = Field(min_length=1, max_length=150)
    description: Optional[str] = None
    trigger:     str
    actions:     list[dict] = Field(default_factory=list)
    conditions:  dict = Field(default_factory=dict)
    is_active:   bool = True


class WorkflowUpdate(AppBase):
    name:        Optional[str] = None
    description: Optional[str] = None
    trigger:     Optional[str] = None
    actions:     Optional[list[dict]] = None
    conditions:  Optional[dict] = None
    is_active:   Optional[bool] = None


class WorkflowOut(TimestampSchema):
    id:               int
    name:             str
    description:      Optional[str] = None
    trigger:          str
    actions:          list[dict]
    conditions:       dict
    is_active:        bool
    workflow_version: int = 1
    ai_generated:     bool = False
    run_count:        int = 0
    success_rate:     float = 0.0
    last_run_at:      Optional[datetime] = None


class WorkflowRunOut(TimestampSchema):
    id:                int
    workflow_id:       int
    status:            str
    steps_completed:   int = 0
    steps_total:       int = 0
    execution_time_ms: Optional[float] = None
    error_message:     Optional[str] = None
    ai_actions_taken:  list[dict] = Field(default_factory=list)
    started_at:        Optional[datetime] = None
    finished_at:       Optional[datetime] = None


class WorkflowTriggerRequest(AppBase):
    workflow_id:    int
    trigger_payload: dict = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════════
#  NOTIFICATION SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class NotificationOut(TimestampSchema):
    id:                int
    title:             str
    message:           str
    notification_type: str
    category:          Optional[str] = None
    priority:          str = "normal"
    action_url:        Optional[str] = None
    icon:              Optional[str] = None
    color:             Optional[str] = None
    is_read:           bool = False
    read_at:           Optional[datetime] = None
    expires_at:        Optional[datetime] = None


class NotificationMarkRead(AppBase):
    notification_ids: list[int]


# ═══════════════════════════════════════════════════════════════════════════════
#  ACTIVITY SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class ActivityOut(TimestampSchema):
    id:                       int
    user_id:                  Optional[int] = None
    activity_type:            str
    entity_type:              Optional[str] = None
    entity_id:                Optional[int] = None
    entity_name:              Optional[str] = None
    description:              Optional[str] = None
    ai_generated_description: Optional[str] = None
    importance_score:         float = 0.5


# ═══════════════════════════════════════════════════════════════════════════════
#  EXPENSE SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class ExpenseCreate(AppBase):
    title:        str = Field(min_length=1, max_length=200)
    description:  Optional[str] = None
    amount:       Decimal = Field(gt=0)
    currency:     str = "USD"
    category:     Optional[str] = None
    expense_date: datetime
    vendor:       Optional[str] = None
    receipt_url:  Optional[str] = None


class ExpenseOut(TimestampSchema):
    id:                     int
    user_id:                int
    title:                  str
    amount:                 Decimal
    currency:               str
    category:               Optional[str] = None
    subcategory:            Optional[str] = None
    expense_date:           datetime
    vendor:                 Optional[str] = None
    receipt_url:            Optional[str] = None
    ocr_processed:          bool = False
    tax_deductible:         Optional[bool] = None
    predicted_tax_category: Optional[str] = None
    ai_confidence:          Optional[float] = None
    recurring_detected:     bool = False


# ═══════════════════════════════════════════════════════════════════════════════
#  REPORT SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class ReportCreate(AppBase):
    title:         str = Field(min_length=1, max_length=255)
    report_type:   str
    report_format: str = "pdf"
    parameters:    dict = Field(default_factory=dict)
    scheduled:     bool = False
    schedule_cron: Optional[str] = None


class ReportOut(TimestampSchema):
    id:                int
    user_id:           int
    title:             str
    report_type:       str
    report_format:     str
    status:            str
    file_url:          Optional[str] = None
    file_size_bytes:   Optional[int] = None
    generated_by_ai:   bool = False
    generation_time_ms: Optional[float] = None
    download_count:    int = 0
    last_downloaded_at: Optional[datetime] = None
    scheduled:         bool = False


# ═══════════════════════════════════════════════════════════════════════════════
#  DASHBOARD WIDGET SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class DashboardWidgetCreate(AppBase):
    widget_type:       str
    title:             Optional[str] = None
    config:            dict = Field(default_factory=dict)
    data_source:       Optional[str] = None
    position_x:        int = 0
    position_y:        int = 0
    width:             int = 2
    height:            int = 2
    refresh_interval:  int = 300
    animation_enabled: bool = True
    theme:             str = "default"


class DashboardWidgetOut(AppBase):
    id:                int
    widget_type:       str
    title:             Optional[str] = None
    config:            dict
    data_source:       Optional[str] = None
    position_x:        int
    position_y:        int
    width:             int
    height:            int
    refresh_interval:  int
    ai_personalized:   bool = False
    animation_enabled: bool = True
    minimized:         bool = False
    theme:             str


class DashboardLayoutUpdate(AppBase):
    widgets: list[dict]   # [{id, position_x, position_y, width, height}]


# ═══════════════════════════════════════════════════════════════════════════════
#  AI CONVERSATION & CHAT SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class ChatMessage(AppBase):
    role:    str   # user|assistant|system
    content: str
    ts:      Optional[datetime] = None


class ChatRequest(AppBase):
    message:             str = Field(min_length=1, max_length=8000)
    conversation_id:     Optional[int] = None
    context:             Optional[dict] = None
    provider:            Optional[str] = None
    stream:              bool = False


class ChatResponse(AppBase):
    response:        str
    conversation_id: int
    intent:          Optional[str] = None
    referenced_entities: dict = Field(default_factory=dict)
    suggested_actions:   list[str] = Field(default_factory=list)
    ai_metadata:         AIMetadataSchema = Field(default_factory=AIMetadataSchema)


class AIConversationOut(TimestampSchema):
    id:                  int
    conversation_title:  Optional[str] = None
    intent:              Optional[str] = None
    message_count:       int = 0
    tokens_used:         int = 0
    feedback_score:      Optional[float] = None
    is_archived:         bool = False
    last_activity_at:    Optional[datetime] = None


class AIConversationDetail(AIConversationOut):
    messages:            list[ChatMessage] = Field(default_factory=list)
    conversation_summary: Optional[str] = None
    referenced_entities: dict = Field(default_factory=dict)


class ConversationFeedback(AppBase):
    conversation_id: int
    score:           float = Field(ge=1, le=5)
    comment:         Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
#  VOICE AI SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class VoiceInvoiceRequest(AppBase):
    transcript:     str = Field(min_length=1)
    language:       str = "en"
    audio_duration: Optional[float] = None   # seconds
    provider:       Optional[str] = None


class VoiceCommandResponse(AppBase):
    intent:           str
    entities:         dict = Field(default_factory=dict)
    confidence:       float = Field(ge=0.0, le=1.0)
    action_taken:     Optional[str] = None
    result:           Optional[dict] = None
    follow_up:        Optional[str] = None
    ai_metadata:      AIMetadataSchema = Field(default_factory=AIMetadataSchema)


# ═══════════════════════════════════════════════════════════════════════════════
#  AI COMMAND CENTER SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class AICommandRequest(AppBase):
    command:  str = Field(min_length=1, max_length=2000)
    context:  Optional[dict] = None
    dry_run:  bool = False


class AICommandAction(AppBase):
    action_type:          str
    parameters:           dict = Field(default_factory=dict)
    priority:             str = "medium"
    requires_confirmation: bool = True
    estimated_impact:     Optional[str] = None


class AICommandResponse(AppBase):
    understood_intent:    str
    actions:              list[AICommandAction]
    clarification_needed: bool = False
    clarification_question: Optional[str] = None
    confidence:           float = Field(ge=0.0, le=1.0)
    dry_run:              bool = False


class AICommandConfirm(AppBase):
    command_id: str
    confirmed:  bool


# ═══════════════════════════════════════════════════════════════════════════════
#  BUSINESS INSIGHT SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class BusinessInsightOut(TimestampSchema):
    id:               int
    insight_type:     str
    title:            str
    summary:          str
    severity:         str
    trend_direction:  Optional[str] = None
    confidence_score: float = 0.0
    impact_score:     float = 0.0
    actionable_steps: list[dict] = Field(default_factory=list)
    is_read:          bool = False
    is_actioned:      bool = False
    expires_at:       Optional[datetime] = None


# ═══════════════════════════════════════════════════════════════════════════════
#  AI RECOMMENDATION SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class AIRecommendationOut(TimestampSchema):
    id:               int
    title:            str
    description:      str
    category:         str
    priority:         str
    estimated_impact: Optional[str] = None
    effort_level:     str
    action_steps:     list[dict] = Field(default_factory=list)
    is_accepted:      Optional[bool] = None
    is_dismissed:     bool = False
    confidence:       float = 0.0
    expires_at:       Optional[datetime] = None


class AIRecommendationAction(AppBase):
    recommendation_id: int
    accepted:          bool
    outcome_notes:     Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
#  ANALYTICS SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class KPISchema(AppBase):
    monthly_revenue:   Decimal = Decimal("0")
    annual_revenue:    Decimal = Decimal("0")
    mrr:               Decimal = Decimal("0")
    arr:               Decimal = Decimal("0")
    collection_rate:   float = 0.0
    avg_invoice_value: Decimal = Decimal("0")
    overdue_rate:      float = 0.0
    client_count:      int = 0
    client_growth:     float = 0.0
    outstanding:       Decimal = Decimal("0")
    period:            str = "30d"


class RevenueChartPoint(AppBase):
    date:    str
    revenue: Decimal
    paid:    int
    sent:    int


class CashflowForecastOut(AppBase):
    next_month:      Decimal
    next_quarter:    Decimal
    confidence_low:  Decimal
    confidence_high: Decimal
    growth_trend:    str
    churn_risk:      float
    key_drivers:     list[str] = Field(default_factory=list)
    risks:           list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    ai_metadata:     AIMetadataSchema = Field(default_factory=AIMetadataSchema)


class OverviewAnalyticsOut(AppBase):
    kpis:              KPISchema
    revenue_chart:     list[RevenueChartPoint] = Field(default_factory=list)
    invoice_analytics: InvoiceAnalyticsSchema
    top_clients:       list[ClientSummary] = Field(default_factory=list)
    recent_activity:   list[ActivityOut] = Field(default_factory=list)
    insights:          list[BusinessInsightOut] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
#  AI INVOICE GENERATION SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class AIInvoiceRequest(AppBase):
    prompt:   str = Field(min_length=5, max_length=2000)
    context:  Optional[dict] = None
    provider: Optional[str] = None


class AIInvoiceResponse(AppBase):
    draft:      InvoiceCreate
    ai_metadata: AIMetadataSchema = Field(default_factory=AIMetadataSchema)
    warnings:   list[str] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
#  INTEGRATION SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class IntegrationOut(TimestampSchema):
    id:           int
    provider:     str
    display_name: Optional[str] = None
    is_active:    bool
    scopes:       list[str] = Field(default_factory=list)
    last_synced_at: Optional[datetime] = None
    sync_status:  Optional[str] = None
    error_count:  int = 0


# ═══════════════════════════════════════════════════════════════════════════════
#  PLATFORM / HEALTH SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class HealthOut(AppBase):
    status:    str
    timestamp: datetime
    version:   str
    uptime:    float


class MetricsOut(AppBase):
    total_requests:        int
    failed_requests:       int
    average_response_ms:   float
    ai_requests:           int
    ws_active_connections: int
    top_endpoints:         dict
    slow_endpoints:        dict
    blocked_ips:           int


# ─── Rebuild forward refs ─────────────────────────────────────────────────────
InvoiceOut.model_rebuild()
UserDashboardProfile.model_rebuild()
Token.model_rebuild()
# ═══════════════════════════════════════════════════════════════════════════════
#  BACKWARD-COMPATIBLE ALIASES
#  main.py and existing routers use the original schema names from v1.
#  These aliases let the old imports keep working without changing main.py.
# ═══════════════════════════════════════════════════════════════════════════════
AIFollowupResponse      = AIReminderResponse
CustomerCreate          = ClientCreate
CustomerResponse        = ClientOut
InvoiceItemResponse     = InvoiceItemOut
InvoiceResponse         = InvoiceOut
RecurringBillingResponse = RecurringBillingOut
ReminderResponse        = ReminderOut
UserLogin               = LoginRequest
UserResponse            = UserOut