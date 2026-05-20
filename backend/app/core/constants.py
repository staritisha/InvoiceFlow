# ═══════════════════════════════════════════════════════════════════════════════
#  AI Invoice Intelligence Platform — app/core/constants.py
#  Single source of truth for all enums, lookup maps, SaaS limits,
#  feature flags, AI categories, and real-time event definitions.
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

from enum import Enum


# ═══════════════════════════════════════════════════════════════════════════════
#  ENUMS
# ═══════════════════════════════════════════════════════════════════════════════

class UserRole(str, Enum):
    OWNER       = "owner"
    ADMIN       = "admin"
    MANAGER     = "manager"
    ACCOUNTANT  = "accountant"
    SALES       = "sales"
    SUPPORT     = "support"
    MEMBER      = "member"
    VIEWER      = "viewer"


class SubscriptionTier(str, Enum):
    FREE       = "free"
    STARTER    = "starter"
    PRO        = "pro"
    BUSINESS   = "business"
    ENTERPRISE = "enterprise"


class InvoiceStatus(str, Enum):
    DRAFT           = "draft"
    PENDING         = "pending"
    SENT            = "sent"
    VIEWED          = "viewed"
    PARTIALLY_PAID  = "partially_paid"
    PAID            = "paid"
    OVERDUE         = "overdue"
    CANCELLED       = "cancelled"
    FAILED          = "failed"
    REFUNDED        = "refunded"


class PaymentMethod(str, Enum):
    CASH            = "cash"
    CARD            = "card"
    BANK_TRANSFER   = "bank_transfer"
    STRIPE          = "stripe"
    PAYPAL          = "paypal"
    UPI             = "upi"
    CRYPTO          = "crypto"


class ReminderType(str, Enum):
    FRIENDLY        = "friendly"
    PROFESSIONAL    = "professional"
    FIRM            = "firm"
    URGENT          = "urgent"
    FINAL_NOTICE    = "final_notice"


class WorkflowTrigger(str, Enum):
    INVOICE_CREATED     = "invoice_created"
    INVOICE_SENT        = "invoice_sent"
    INVOICE_OVERDUE     = "invoice_overdue"
    INVOICE_PAID        = "invoice_paid"
    CLIENT_CREATED      = "client_created"
    CLIENT_HIGH_RISK    = "client_high_risk"
    PAYMENT_RECEIVED    = "payment_received"
    SCHEDULED           = "scheduled"
    MANUAL              = "manual"


class NotificationType(str, Enum):
    INFO        = "info"
    SUCCESS     = "success"
    WARNING     = "warning"
    ERROR       = "error"
    PAYMENT     = "payment"
    INVOICE     = "invoice"
    AI_INSIGHT  = "ai_insight"
    WORKFLOW    = "workflow"
    REMINDER    = "reminder"
    SYSTEM      = "system"


class InsightType(str, Enum):
    CASHFLOW        = "cashflow"
    REVENUE         = "revenue"
    CLIENT_RISK     = "client_risk"
    PAYMENT_PATTERN = "payment_pattern"
    FORECAST        = "forecast"
    EXPENSE         = "expense"
    GROWTH          = "growth"


class ActivityType(str, Enum):
    CREATE      = "create"
    UPDATE      = "update"
    DELETE      = "delete"
    SEND        = "send"
    PAYMENT     = "payment"
    LOGIN       = "login"
    LOGOUT      = "logout"
    AI_ACTION   = "ai_action"
    WORKFLOW    = "workflow"
    EXPORT      = "export"


class ReportType(str, Enum):
    FINANCIAL   = "financial"
    TAX         = "tax"
    CLIENT      = "client"
    REVENUE     = "revenue"
    EXPENSE     = "expense"
    CASHFLOW    = "cashflow"
    CUSTOM      = "custom"


class ReportFormat(str, Enum):
    PDF  = "pdf"
    CSV  = "csv"
    XLSX = "xlsx"
    HTML = "html"


class InvoiceTheme(str, Enum):
    MODERN  = "modern"
    MINIMAL = "minimal"
    CLASSIC = "classic"
    DARK    = "dark"
    STARTUP = "startup"
    GLASS   = "glass"
    ELEGANT = "elegant"
    BOLD    = "bold"


class AIPriority(str, Enum):
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    CRITICAL = "critical"


class AIActionType(str, Enum):
    CREATE_INVOICE   = "create_invoice"
    SEND_REMINDER    = "send_reminder"
    GENERATE_REPORT  = "generate_report"
    FORECAST_REVENUE = "forecast_revenue"
    ANALYZE_CLIENT   = "analyze_client"
    SMART_SEARCH     = "smart_search"
    CATEGORIZE       = "categorize"
    SUMMARIZE        = "summarize"


class DashboardWidgetType(str, Enum):
    KPI         = "kpi"
    CHART       = "chart"
    INSIGHT     = "insight"
    ACTIVITY    = "activity"
    CASHFLOW    = "cashflow"
    FORECAST    = "forecast"
    LEADERBOARD = "leaderboard"
    HEATMAP     = "heatmap"
    AI_TIPS     = "ai_tips"


class InsightSeverity(str, Enum):
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    CRITICAL = "critical"


class ClientRiskLevel(str, Enum):
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"


class PaymentBehavior(str, Enum):
    EXCELLENT = "excellent"
    GOOD      = "good"
    FAIR      = "fair"
    POOR      = "poor"


class RecurringFrequency(str, Enum):
    WEEKLY    = "weekly"
    MONTHLY   = "monthly"
    QUARTERLY = "quarterly"
    YEARLY    = "yearly"


class VoiceProvider(str, Enum):
    WHISPER = "whisper"
    DEEPGRAM = "deepgram"
    ASSEMBLY = "assemblyai"


# ═══════════════════════════════════════════════════════════════════════════════
#  LOOKUP MAPS
# ═══════════════════════════════════════════════════════════════════════════════

SUPPORTED_CURRENCIES: dict[str, str] = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "INR": "₹",
    "JPY": "¥",
    "AUD": "A$",
    "CAD": "C$",
    "SGD": "S$",
    "AED": "د.إ",
    "CHF": "Fr",
    "CNY": "¥",
    "BRL": "R$",
    "ZAR": "R",
    "MXN": "MX$",
    "SEK": "kr",
    "NOK": "kr",
    "DKK": "kr",
}

INVOICE_THEMES: list[str] = [
    InvoiceTheme.MODERN,
    InvoiceTheme.MINIMAL,
    InvoiceTheme.CLASSIC,
    InvoiceTheme.DARK,
    InvoiceTheme.STARTUP,
    InvoiceTheme.GLASS,
    InvoiceTheme.ELEGANT,
    InvoiceTheme.BOLD,
]

INSIGHT_SEVERITY_LEVELS: list[str] = [
    InsightSeverity.LOW,
    InsightSeverity.MEDIUM,
    InsightSeverity.HIGH,
    InsightSeverity.CRITICAL,
]

WORKFLOW_ACTIONS: list[str] = [
    "send_email",
    "send_whatsapp",
    "create_reminder",
    "generate_report",
    "notify_team",
    "update_invoice_status",
    "assign_user",
    "trigger_webhook",
    "escalate_reminder",
]

AI_PROMPT_TYPES: list[str] = [
    "invoice_generation",
    "business_insights",
    "financial_chatbot",
    "risk_scoring",
    "cashflow_forecast",
    "followup_generation",
    "thank_you_email",
    "smart_search",
    "expense_categorization",
    "client_summary",
]

KPI_METRICS: list[str] = [
    "monthly_revenue",
    "annual_revenue",
    "collection_rate",
    "avg_invoice_value",
    "overdue_rate",
    "client_growth",
    "mrr",
    "arr",
    "dso",           # Days Sales Outstanding
    "churn_rate",
    "cac",           # Customer Acquisition Cost
    "ltv",           # Lifetime Value
]

AI_RECOMMENDATION_TYPES: list[str] = [
    "payment_followup",
    "revenue_growth",
    "client_retention",
    "expense_optimization",
    "invoice_optimization",
    "risk_mitigation",
    "upsell_opportunity",
]

REALTIME_EVENTS: list[str] = [
    "invoice_created",
    "invoice_sent",
    "invoice_paid",
    "invoice_overdue",
    "payment_received",
    "new_notification",
    "workflow_triggered",
    "insight_generated",
    "client_risk_changed",
    "reminder_sent",
    "report_ready",
    "user_joined",
    "kpi_updated",
]

# ── Status transition map — valid next states for each invoice status ──────────

INVOICE_STATUS_TRANSITIONS: dict[str, list[str]] = {
    InvoiceStatus.DRAFT:          [InvoiceStatus.PENDING, InvoiceStatus.CANCELLED],
    InvoiceStatus.PENDING:        [InvoiceStatus.SENT, InvoiceStatus.CANCELLED],
    InvoiceStatus.SENT:           [InvoiceStatus.VIEWED, InvoiceStatus.PAID, InvoiceStatus.OVERDUE, InvoiceStatus.CANCELLED],
    InvoiceStatus.VIEWED:         [InvoiceStatus.PAID, InvoiceStatus.OVERDUE, InvoiceStatus.CANCELLED],
    InvoiceStatus.PARTIALLY_PAID: [InvoiceStatus.PAID, InvoiceStatus.OVERDUE, InvoiceStatus.CANCELLED],
    InvoiceStatus.PAID:           [InvoiceStatus.REFUNDED],
    InvoiceStatus.OVERDUE:        [InvoiceStatus.PAID, InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.CANCELLED],
    InvoiceStatus.CANCELLED:      [],
    InvoiceStatus.FAILED:         [InvoiceStatus.PENDING],
    InvoiceStatus.REFUNDED:       [],
}

def valid_status_transition(current: str, next_status: str) -> bool:
    """Return True if transitioning from *current* to *next_status* is allowed."""
    return next_status in INVOICE_STATUS_TRANSITIONS.get(current, [])


# ═══════════════════════════════════════════════════════════════════════════════
#  SAAS LIMITS
# ═══════════════════════════════════════════════════════════════════════════════

SUBSCRIPTION_LIMITS: dict[str, dict] = {
    SubscriptionTier.FREE: {
        "invoices_per_month":       20,
        "team_members":             1,
        "clients":                  50,
        "ai_requests_per_day":      10,
        "reports_per_month":        3,
        "workflows":                0,
        "voice_commands":           False,
        "custom_branding":          False,
        "api_access":               False,
    },
    SubscriptionTier.STARTER: {
        "invoices_per_month":       100,
        "team_members":             3,
        "clients":                  200,
        "ai_requests_per_day":      50,
        "reports_per_month":        20,
        "workflows":                5,
        "voice_commands":           True,
        "custom_branding":          False,
        "api_access":               False,
    },
    SubscriptionTier.PRO: {
        "invoices_per_month":       500,
        "team_members":             5,
        "clients":                  1000,
        "ai_requests_per_day":      500,
        "reports_per_month":        100,
        "workflows":                20,
        "voice_commands":           True,
        "custom_branding":          True,
        "api_access":               True,
    },
    SubscriptionTier.BUSINESS: {
        "invoices_per_month":       5000,
        "team_members":             20,
        "clients":                  10000,
        "ai_requests_per_day":      2000,
        "reports_per_month":        500,
        "workflows":                100,
        "voice_commands":           True,
        "custom_branding":          True,
        "api_access":               True,
    },
    SubscriptionTier.ENTERPRISE: {
        "invoices_per_month":       -1,        # unlimited
        "team_members":             -1,
        "clients":                  -1,
        "ai_requests_per_day":      -1,
        "reports_per_month":        -1,
        "workflows":                -1,
        "voice_commands":           True,
        "custom_branding":          True,
        "api_access":               True,
    },
}


def get_limit(tier: str, key: str):
    """Return the limit value for a given tier and limit key. -1 means unlimited."""
    return SUBSCRIPTION_LIMITS.get(tier, SUBSCRIPTION_LIMITS[SubscriptionTier.FREE]).get(key)


def is_unlimited(tier: str, key: str) -> bool:
    return get_limit(tier, key) == -1


# ═══════════════════════════════════════════════════════════════════════════════
#  FEATURE FLAGS
# ═══════════════════════════════════════════════════════════════════════════════
#  These are defaults — override dynamically from settings or a DB feature-flag
#  table in production.

FEATURE_FLAGS: dict[str, bool] = {
    "ai_enabled":           True,
    "voice_enabled":        True,
    "workflows_enabled":    True,
    "forecasting_enabled":  True,
    "websocket_enabled":    True,
    "reports_enabled":      True,
    "analytics_enabled":    True,
    "stripe_enabled":       False,    # enable when keys are configured
    "whatsapp_enabled":     False,
    "demo_mode":            False,
    "maintenance_mode":     False,
    "ai_insights_enabled":  True,
    "multi_currency":       True,
    "recurring_billing":    True,
    "experimental_ai":      False,
}


def is_feature_enabled(flag: str) -> bool:
    return FEATURE_FLAGS.get(flag, False)


# ═══════════════════════════════════════════════════════════════════════════════
#  MISC CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_CURRENCY:           str = "USD"
DEFAULT_TIMEZONE:           str = "UTC"
DEFAULT_DATE_FORMAT:        str = "%Y-%m-%d"
DEFAULT_DATETIME_FORMAT:    str = "%Y-%m-%dT%H:%M:%SZ"
DEFAULT_TAX_RATE:           float = 0.0
DEFAULT_PAYMENT_TERMS_DAYS: int = 30

MAX_INVOICE_ITEMS:          int = 100
MAX_NOTES_LENGTH:           int = 2000
MAX_DESCRIPTION_LENGTH:     int = 500
MAX_AI_CONTEXT_TURNS:       int = 20
MAX_EXPORT_ROWS:            int = 10_000

AI_CONFIDENCE_THRESHOLD:    float = 0.65
AI_HIGH_RISK_THRESHOLD:     float = 70.0
SLOW_PAYMENT_THRESHOLD_DAYS: int  = 45
OVERDUE_CRITICAL_DAYS:      int   = 90

SUPPORTED_AUDIO_FORMATS:    list[str] = ["mp3", "wav", "m4a", "ogg", "webm"]
SUPPORTED_UPLOAD_EXTENSIONS: list[str] = ["pdf", "png", "jpg", "jpeg", "csv", "xlsx"]

PDF_THEMES_CONFIG: dict[str, dict] = {
    "modern":  {"primary": "#2563EB", "font": "Helvetica",      "accent": "#DBEAFE"},
    "minimal": {"primary": "#000000", "font": "Helvetica",      "accent": "#FFFFFF"},
    "classic": {"primary": "#1F2937", "font": "Times-Roman",    "accent": "#F3F4F6"},
    "dark":    {"primary": "#111827", "font": "Helvetica",      "accent": "#374151"},
    "startup": {"primary": "#7C3AED", "font": "Helvetica",      "accent": "#EDE9FE"},
    "glass":   {"primary": "#0EA5E9", "font": "Helvetica",      "accent": "#E0F2FE"},
    "elegant": {"primary": "#065F46", "font": "Times-Roman",    "accent": "#D1FAE5"},
    "bold":    {"primary": "#DC2626", "font": "Helvetica-Bold", "accent": "#FEE2E2"},
}
