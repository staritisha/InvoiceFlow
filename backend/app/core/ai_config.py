# ═══════════════════════════════════════════════════════════════════════════════
#  AI Invoice Intelligence Platform — app/core/ai_config.py
#  Single source of truth for every AI provider, model, temperature profile,
#  prompt template, safety guardrail, cost rule, and retry policy.
#  "If this file is powerful → your whole product feels intelligent."
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from app.config import settings

logger = logging.getLogger("invoiceflow.ai")


# ═══════════════════════════════════════════════════════════════════════════════
#  ENUMS
# ═══════════════════════════════════════════════════════════════════════════════

class AIProvider(str, Enum):
    OPENAI     = "openai"
    CLAUDE     = "claude"
    GEMINI     = "gemini"
    GROQ       = "groq"
    OPENROUTER = "openrouter"


class AITask(str, Enum):
    CHAT                  = "chat"
    INVOICE_GENERATION    = "invoice_generation"
    BUSINESS_INSIGHTS     = "business_insights"
    REMINDER_GENERATION   = "reminder_generation"
    FINANCIAL_CHATBOT     = "financial_chatbot"
    EXPENSE_CATEGORIZE    = "expense_categorization"
    REVENUE_FORECAST      = "revenue_forecast"
    CLIENT_RISK_SCORING   = "client_risk_scoring"
    FOLLOWUP_SCHEDULING   = "followup_scheduling"
    AUTO_FILL_INVOICE     = "auto_fill_invoice"
    THANK_YOU_EMAIL       = "thank_you_email"
    COMMAND_INTERPRET     = "command_interpretation"
    BUSINESS_OPTIMIZE     = "business_optimization"
    SMART_SEARCH          = "smart_search"


# ═══════════════════════════════════════════════════════════════════════════════
#  TEMPERATURE PROFILES
# ═══════════════════════════════════════════════════════════════════════════════

TEMPERATURE_PROFILES: dict[str, float] = {
    AITask.INVOICE_GENERATION:  0.20,   # precise, structured
    AITask.REVENUE_FORECAST:    0.25,   # analytical
    AITask.CLIENT_RISK_SCORING: 0.25,
    AITask.EXPENSE_CATEGORIZE:  0.20,
    AITask.FOLLOWUP_SCHEDULING: 0.30,
    AITask.AUTO_FILL_INVOICE:   0.30,
    AITask.BUSINESS_INSIGHTS:   0.50,   # balanced
    AITask.BUSINESS_OPTIMIZE:   0.50,
    AITask.COMMAND_INTERPRET:   0.30,
    AITask.SMART_SEARCH:        0.30,
    AITask.REMINDER_GENERATION: 0.60,   # creative but professional
    AITask.THANK_YOU_EMAIL:     0.65,
    AITask.FINANCIAL_CHATBOT:   0.70,   # conversational
    AITask.CHAT:                0.75,
}


# ═══════════════════════════════════════════════════════════════════════════════
#  PROVIDER CONFIGURATIONS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ProviderConfig:
    name:        AIProvider
    base_url:    str
    api_key_env: str                        # name of the env-var holding the key
    default_model: str
    task_models: dict[str, str] = field(default_factory=dict)
    max_tokens:  int   = 2048
    top_p:       float = 1.0
    freq_penalty: float = 0.0
    pres_penalty: float = 0.0
    cost_per_1k_prompt:     float = 0.0    # USD
    cost_per_1k_completion: float = 0.0

    def api_key(self) -> str:
        import os
        return os.environ.get(self.api_key_env, "")

    def model_for(self, task: str) -> str:
        return self.task_models.get(task, self.default_model)

    def headers(self) -> dict[str, str]:
        key = self.api_key()
        base: dict[str, str] = {"Content-Type": "application/json"}
        if self.name == AIProvider.OPENAI:
            base["Authorization"] = f"Bearer {key}"
            base["OpenAI-Beta"]   = "assistants=v2"
        elif self.name == AIProvider.CLAUDE:
            base["x-api-key"]         = key
            base["anthropic-version"]  = "2023-06-01"
        elif self.name == AIProvider.GEMINI:
            base["Authorization"] = f"Bearer {key}"
        elif self.name in (AIProvider.GROQ, AIProvider.OPENROUTER):
            base["Authorization"] = f"Bearer {key}"
        return base


PROVIDER_REGISTRY: dict[str, ProviderConfig] = {

    AIProvider.OPENAI: ProviderConfig(
        name          = AIProvider.OPENAI,
        base_url      = "https://api.openai.com/v1",
        api_key_env   = "OPENAI_API_KEY",
        default_model = "gpt-4o-mini",
        task_models   = {
            AITask.INVOICE_GENERATION:  "gpt-4o-mini",
            AITask.REVENUE_FORECAST:    "gpt-4o",
            AITask.BUSINESS_INSIGHTS:   "gpt-4o",
            AITask.CLIENT_RISK_SCORING: "gpt-4o",
            AITask.FINANCIAL_CHATBOT:   "gpt-4o-mini",
            AITask.CHAT:                "gpt-4o-mini",
            AITask.REMINDER_GENERATION: "gpt-4o-mini",
            AITask.EXPENSE_CATEGORIZE:  "gpt-4o-mini",
            AITask.COMMAND_INTERPRET:   "gpt-4o",
        },
        max_tokens            = 2048,
        cost_per_1k_prompt    = 0.00015,
        cost_per_1k_completion= 0.00060,
    ),

    AIProvider.CLAUDE: ProviderConfig(
        name          = AIProvider.CLAUDE,
        base_url      = "https://api.anthropic.com/v1",
        api_key_env   = "ANTHROPIC_API_KEY",
        default_model = "claude-3-haiku-20240307",
        task_models   = {
            AITask.BUSINESS_INSIGHTS:   "claude-3-5-sonnet-20241022",
            AITask.REVENUE_FORECAST:    "claude-3-5-sonnet-20241022",
            AITask.FINANCIAL_CHATBOT:   "claude-3-haiku-20240307",
            AITask.CHAT:                "claude-3-haiku-20240307",
        },
        max_tokens            = 2048,
        cost_per_1k_prompt    = 0.00025,
        cost_per_1k_completion= 0.00125,
    ),

    AIProvider.GROQ: ProviderConfig(
        name          = AIProvider.GROQ,
        base_url      = "https://api.groq.com/openai/v1",
        api_key_env   = "GROQ_API_KEY",
        default_model = "llama3-8b-8192",
        task_models   = {
            AITask.CHAT:                "llama3-8b-8192",
            AITask.FINANCIAL_CHATBOT:   "llama3-8b-8192",
            AITask.REMINDER_GENERATION: "llama3-8b-8192",
        },
        max_tokens            = 1024,
        cost_per_1k_prompt    = 0.00000,   # free tier
        cost_per_1k_completion= 0.00000,
    ),

    AIProvider.GEMINI: ProviderConfig(
        name          = AIProvider.GEMINI,
        base_url      = "https://generativelanguage.googleapis.com/v1beta",
        api_key_env   = "GOOGLE_API_KEY",
        default_model = "gemini-1.5-flash",
        task_models   = {
            AITask.BUSINESS_INSIGHTS:   "gemini-1.5-pro",
            AITask.REVENUE_FORECAST:    "gemini-1.5-pro",
            AITask.CHAT:                "gemini-1.5-flash",
        },
        max_tokens            = 2048,
        cost_per_1k_prompt    = 0.000075,
        cost_per_1k_completion= 0.000300,
    ),

    AIProvider.OPENROUTER: ProviderConfig(
        name          = AIProvider.OPENROUTER,
        base_url      = "https://openrouter.ai/api/v1",
        api_key_env   = "OPENROUTER_API_KEY",
        default_model = "mistralai/mistral-7b-instruct",
        task_models   = {
            AITask.CHAT:                "mistralai/mistral-7b-instruct",
            AITask.REMINDER_GENERATION: "mistralai/mistral-7b-instruct",
        },
        max_tokens            = 1024,
        cost_per_1k_prompt    = 0.00007,
        cost_per_1k_completion= 0.00007,
    ),
}

# Ordered fallback chain — primary → cheaper alternatives
FALLBACK_CHAIN: list[str] = [
    AIProvider.OPENAI,
    AIProvider.GROQ,
    AIProvider.GEMINI,
    AIProvider.OPENROUTER,
    AIProvider.CLAUDE,
]


def get_provider(name: Optional[str] = None) -> ProviderConfig:
    """Return the named provider config, defaulting to settings.ai_provider."""
    key = name or getattr(settings, "ai_provider", AIProvider.OPENAI)
    return PROVIDER_REGISTRY.get(key, PROVIDER_REGISTRY[AIProvider.OPENAI])


# ═══════════════════════════════════════════════════════════════════════════════
#  COST TRACKING
# ═══════════════════════════════════════════════════════════════════════════════

_cost_log: list[dict] = []
_session_tokens: dict[str, int] = {"prompt": 0, "completion": 0}
MAX_DAILY_COST_USD    = 5.00
MAX_REQUEST_TOKENS    = 3000


def log_token_usage(
    task: str,
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Record token usage and return estimated USD cost for this call."""
    cfg  = get_provider(provider)
    cost = (prompt_tokens / 1000 * cfg.cost_per_1k_prompt +
            completion_tokens / 1000 * cfg.cost_per_1k_completion)
    _session_tokens["prompt"]     += prompt_tokens
    _session_tokens["completion"] += completion_tokens
    _cost_log.append({
        "task":             task,
        "provider":         provider,
        "prompt_tokens":    prompt_tokens,
        "completion_tokens":completion_tokens,
        "estimated_usd":    round(cost, 6),
        "ts":               datetime.now(timezone.utc).isoformat(),
    })
    logger.info(
        f"[ai-cost] task={task} provider={provider} "
        f"prompt={prompt_tokens} completion={completion_tokens} "
        f"cost=${cost:.6f}"
    )
    return cost


def session_cost_summary() -> dict:
    """Return cumulative session token and cost totals."""
    cfg   = get_provider()
    total = sum(e["estimated_usd"] for e in _cost_log)
    return {
        "prompt_tokens":     _session_tokens["prompt"],
        "completion_tokens": _session_tokens["completion"],
        "total_tokens":      _session_tokens["prompt"] + _session_tokens["completion"],
        "estimated_usd":     round(total, 4),
        "request_count":     len(_cost_log),
        "budget_remaining":  round(MAX_DAILY_COST_USD - total, 4),
        "recent":            _cost_log[-10:],
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  PROMPT VERSIONING
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PromptTemplate:
    key:             str
    version:         str
    system:          str
    user_template:   str              # use {placeholders} for context injection
    updated_at:      str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    performance_score: float = 0.0   # 0.0–1.0, update from eval pipeline
    json_mode:       bool = False
    max_tokens:      int  = 1024


# ═══════════════════════════════════════════════════════════════════════════════
#  SYSTEM PERSONALITY
# ═══════════════════════════════════════════════════════════════════════════════

_PERSONALITY = """\
You are InvoiceFlow AI — a professional, sharp, and empathetic AI CFO assistant \
built for modern businesses and freelancers.

Your character:
- You speak like a trusted startup advisor and financial analyst combined.
- You are concise, data-driven, and action-oriented.
- You proactively surface risks and opportunities the user hasn't asked for.
- You never hallucinate financial figures; if data is missing, say so clearly.
- You always return structured, valid JSON when asked.
- You speak in plain business English — no jargon dumps.
"""

_SAFETY_GUARDRAILS = """\
SAFETY RULES — follow unconditionally:
1. Never invent or modify invoice amounts, tax rates, or totals.
2. Never give personalized legal or regulated financial advice.
3. If asked to fabricate payment confirmations, refuse politely.
4. All numeric outputs must be mathematically verifiable from the provided data.
5. If confidence is low, prefix the answer with [LOW CONFIDENCE] and explain why.
6. Never expose user data outside the current conversation context.
"""

_JSON_ENFORCEMENT = """\
RESPONSE FORMAT:
- Return ONLY a valid JSON object. No markdown, no prose before or after.
- All numeric fields must be numbers (not strings).
- All date fields must be ISO 8601 strings.
- If you cannot produce valid JSON, return: {"error": "reason", "success": false}
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  PROMPT TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════════

PROMPTS: dict[str, PromptTemplate] = {

    # ── Invoice Generation ────────────────────────────────────────────────────
    AITask.INVOICE_GENERATION: PromptTemplate(
        key          = AITask.INVOICE_GENERATION,
        version      = "1.3.0",
        json_mode    = True,
        max_tokens   = 1500,
        system       = f"""{_PERSONALITY}

{_SAFETY_GUARDRAILS}

{_JSON_ENFORCEMENT}

You are an expert at parsing natural language invoice requests and converting them into structured invoice JSON.
""",
        user_template = """\
User context:
- Company: {company_name}
- Currency: {currency}
- Default tax rate: {tax_rate}%
- Previous clients: {recent_clients}

User request:
"{user_input}"

Generate a complete invoice JSON with this structure:
{{
  "client_name": "",
  "client_email": "",
  "issue_date": "",
  "due_date": "",
  "currency": "",
  "items": [{{"description": "", "quantity": 0, "unit_price": 0, "total": 0}}],
  "subtotal": 0,
  "tax_rate": 0,
  "tax_amount": 0,
  "total_amount": 0,
  "notes": "",
  "priority": "normal|high|critical",
  "suggested_payment_terms": "",
  "confidence": 0.0
}}
""",
    ),

    # ── Business Insights ─────────────────────────────────────────────────────
    AITask.BUSINESS_INSIGHTS: PromptTemplate(
        key          = AITask.BUSINESS_INSIGHTS,
        version      = "1.2.0",
        json_mode    = True,
        max_tokens   = 1500,
        system       = f"""{_PERSONALITY}

{_SAFETY_GUARDRAILS}

{_JSON_ENFORCEMENT}

You are a business intelligence analyst. Identify actionable insights from the financial data provided.
""",
        user_template = """\
Business data snapshot:
- Total revenue (30d): {revenue_30d}
- Outstanding invoices: {outstanding_count} worth {outstanding_value}
- Overdue invoices: {overdue_count} worth {overdue_value}
- Top clients: {top_clients}
- Collection rate: {collection_rate}%
- MRR: {mrr}
- User role: {user_role}

Generate a JSON array of insights (max 5):
[{{
  "type": "cashflow|revenue|client_risk|payment_pattern|forecast|expense|growth",
  "severity": "low|medium|high|critical",
  "title": "",
  "summary": "",
  "recommended_action": "",
  "impact_estimate": "",
  "confidence": 0.0
}}]
""",
    ),

    # ── Reminder Generation ───────────────────────────────────────────────────
    AITask.REMINDER_GENERATION: PromptTemplate(
        key          = AITask.REMINDER_GENERATION,
        version      = "1.1.0",
        json_mode    = True,
        max_tokens   = 800,
        system       = f"""{_PERSONALITY}

{_SAFETY_GUARDRAILS}

{_JSON_ENFORCEMENT}

You write psychologically optimized, high-conversion payment reminder messages. \
Match tone strictly to the requested type.
""",
        user_template = """\
Invoice details:
- Client name: {client_name}
- Amount due: {currency}{amount}
- Due date: {due_date}
- Days overdue: {days_overdue}
- Payment link: {payment_link}
- Reminder type: {reminder_type}  (friendly|professional|firm|urgent|final_notice)
- Previous reminders sent: {reminder_count}

Return:
{{
  "subject": "",
  "body": "",
  "cta_text": "",
  "tone_used": "",
  "send_at_suggestion": "",
  "escalate_after_days": 0
}}
""",
    ),

    # ── Financial Chatbot ─────────────────────────────────────────────────────
    AITask.FINANCIAL_CHATBOT: PromptTemplate(
        key          = AITask.FINANCIAL_CHATBOT,
        version      = "1.4.0",
        json_mode    = False,
        max_tokens   = 1000,
        system       = f"""{_PERSONALITY}

{_SAFETY_GUARDRAILS}

You are the user's AI CFO. Answer questions about their business finances clearly and concisely.
Provide specific numbers and actionable advice. If data is missing, ask for it.
Keep answers under 200 words unless the user asks for detail.
Conversation history is provided for context — maintain continuity.
""",
        user_template = """\
Business context:
- Company: {company_name}
- Revenue (30d): {revenue_30d}
- Outstanding: {outstanding_value}
- Top clients: {top_clients}
- User role: {user_role}

Conversation history:
{conversation_history}

User question: {user_input}
""",
    ),

    # ── Expense Categorization ────────────────────────────────────────────────
    AITask.EXPENSE_CATEGORIZE: PromptTemplate(
        key          = AITask.EXPENSE_CATEGORIZE,
        version      = "1.0.0",
        json_mode    = True,
        max_tokens   = 600,
        system       = f"""{_PERSONALITY}

{_JSON_ENFORCEMENT}

You are an expert bookkeeper. Classify expenses accurately and consistently.
""",
        user_template = """\
Expense entries to classify:
{expense_list}

For each entry return:
[{{
  "id": "",
  "description": "",
  "amount": 0,
  "category": "software|hardware|salary|marketing|tax|subscriptions|travel|office|legal|other",
  "subcategory": "",
  "tax_deductible": true,
  "confidence": 0.0
}}]
""",
    ),

    # ── Revenue Forecast ──────────────────────────────────────────────────────
    AITask.REVENUE_FORECAST: PromptTemplate(
        key          = AITask.REVENUE_FORECAST,
        version      = "1.1.0",
        json_mode    = True,
        max_tokens   = 1200,
        system       = f"""{_PERSONALITY}

{_SAFETY_GUARDRAILS}

{_JSON_ENFORCEMENT}

You are a financial forecasting model. Base predictions strictly on provided data. \
Never fabricate trend signals not present in the data.
""",
        user_template = """\
Historical revenue data (last 12 months):
{monthly_revenue}

Outstanding pipeline:
{outstanding_invoices}

Client growth rate: {client_growth_rate}%
Avg invoice value: {avg_invoice_value}
Collection rate: {collection_rate}%
Seasonal notes: {seasonal_notes}

Return:
{{
  "next_month_forecast": 0,
  "next_quarter_forecast": 0,
  "confidence_interval": {{"low": 0, "high": 0}},
  "churn_risk_percent": 0,
  "payment_delay_risk": "low|medium|high",
  "growth_trend": "declining|flat|growing|accelerating",
  "key_drivers": [],
  "risks": [],
  "recommendations": []
}}
""",
    ),

    # ── Client Risk Scoring ───────────────────────────────────────────────────
    AITask.CLIENT_RISK_SCORING: PromptTemplate(
        key          = AITask.CLIENT_RISK_SCORING,
        version      = "1.2.0",
        json_mode    = True,
        max_tokens   = 800,
        system       = f"""{_PERSONALITY}

{_SAFETY_GUARDRAILS}

{_JSON_ENFORCEMENT}

You are a credit risk analyst. Score clients based on payment behavior and history.
""",
        user_template = """\
Client profile:
- Name: {client_name}
- Total invoiced: {total_invoiced}
- Total paid: {total_paid}
- Avg payment delay (days): {avg_delay}
- Overdue invoices: {overdue_count}
- Oldest overdue (days): {oldest_overdue_days}
- Revenue dependence: {revenue_dependence}%
- Invoice history: {invoice_history}

Return:
{{
  "risk_score": 0,
  "risk_level": "low|medium|high",
  "risk_reasons": [],
  "recommended_action": "",
  "credit_limit_suggestion": 0,
  "payment_terms_suggestion": "",
  "confidence": 0.0
}}
""",
    ),

    # ── Follow-up Scheduling ──────────────────────────────────────────────────
    AITask.FOLLOWUP_SCHEDULING: PromptTemplate(
        key          = AITask.FOLLOWUP_SCHEDULING,
        version      = "1.0.0",
        json_mode    = True,
        max_tokens   = 600,
        system       = f"""{_PERSONALITY}

{_JSON_ENFORCEMENT}

You are an expert at designing optimal follow-up communication schedules for invoice collection.
""",
        user_template = """\
Invoice context:
- Client payment behavior: {payment_behavior}
- Days since invoice sent: {days_since_sent}
- Days overdue: {days_overdue}
- Invoice amount: {currency}{amount}
- Previous contact attempts: {contact_attempts}
- Client timezone: {client_timezone}

Return:
{{
  "next_followup_date": "",
  "recommended_channel": "email|whatsapp|phone",
  "tone": "friendly|professional|firm|urgent|final_notice",
  "escalate_to_human": false,
  "followup_schedule": [
    {{"day": 0, "tone": "", "channel": "", "note": ""}}
  ]
}}
""",
    ),

    # ── Auto-fill Invoice ─────────────────────────────────────────────────────
    AITask.AUTO_FILL_INVOICE: PromptTemplate(
        key          = AITask.AUTO_FILL_INVOICE,
        version      = "1.0.0",
        json_mode    = True,
        max_tokens   = 1000,
        system       = f"""{_PERSONALITY}

{_SAFETY_GUARDRAILS}

{_JSON_ENFORCEMENT}

You auto-complete partial invoice data using client history and business context. \
Only suggest values you are confident about. Mark uncertain fields with confidence < 0.6.
""",
        user_template = """\
Partial invoice input:
{partial_invoice}

Client history:
{client_history}

Business defaults:
- Currency: {currency}
- Tax rate: {tax_rate}%
- Payment terms: {payment_terms} days

Fill in all missing fields and return the completed invoice JSON with a `confidence` \
score per field (0.0–1.0).
""",
    ),

    # ── Thank-You Email ───────────────────────────────────────────────────────
    AITask.THANK_YOU_EMAIL: PromptTemplate(
        key          = AITask.THANK_YOU_EMAIL,
        version      = "1.0.0",
        json_mode    = True,
        max_tokens   = 600,
        system       = f"""{_PERSONALITY}

{_JSON_ENFORCEMENT}

You write warm, professional post-payment thank-you emails that subtly \
encourage repeat business without being pushy.
""",
        user_template = """\
Payment details:
- Client name: {client_name}
- Amount paid: {currency}{amount}
- Invoice number: {invoice_number}
- Payment date: {payment_date}
- Client total business (lifetime): {lifetime_value}
- Services delivered: {service_summary}
- Company name: {company_name}

Return:
{{
  "subject": "",
  "body": "",
  "upsell_suggestion": "",
  "cta": "",
  "tone": ""
}}
""",
    ),

    # ── Command Interpretation ────────────────────────────────────────────────
    AITask.COMMAND_INTERPRET: PromptTemplate(
        key          = AITask.COMMAND_INTERPRET,
        version      = "1.1.0",
        json_mode    = True,
        max_tokens   = 800,
        system       = f"""{_PERSONALITY}

{_JSON_ENFORCEMENT}

You parse natural language AI commands and convert them into structured, executable action plans.
Only return actions that are safe and reversible where possible.
""",
        user_template = """\
Available actions: {available_actions}
User role: {user_role}
User command: "{user_input}"
Business context: {business_context}

Return:
{{
  "understood_intent": "",
  "actions": [
    {{
      "action_type": "",
      "parameters": {{}},
      "priority": "low|medium|high",
      "requires_confirmation": true,
      "estimated_impact": ""
    }}
  ],
  "clarification_needed": false,
  "clarification_question": "",
  "confidence": 0.0
}}
""",
    ),

    # ── Business Optimization ─────────────────────────────────────────────────
    AITask.BUSINESS_OPTIMIZE: PromptTemplate(
        key          = AITask.BUSINESS_OPTIMIZE,
        version      = "1.0.0",
        json_mode    = True,
        max_tokens   = 1200,
        system       = f"""{_PERSONALITY}

{_SAFETY_GUARDRAILS}

{_JSON_ENFORCEMENT}

You are a business growth advisor. Provide ranked, specific, and actionable optimization recommendations.
""",
        user_template = """\
Business metrics:
- MRR: {mrr}
- ARR: {arr}
- Collection rate: {collection_rate}%
- Avg invoice value: {avg_invoice_value}
- Overdue rate: {overdue_rate}%
- Client count: {client_count}
- Top revenue clients: {top_clients}
- Expense summary: {expense_summary}

Return top 5 optimization recommendations:
[{{
  "category": "revenue_growth|client_retention|expense_optimization|invoice_optimization|payment_followup",
  "title": "",
  "description": "",
  "estimated_revenue_impact": "",
  "effort_level": "low|medium|high",
  "priority_rank": 1,
  "action_steps": []
}}]
""",
    ),
}


def get_prompt(task: str) -> PromptTemplate:
    """Return the PromptTemplate for a given AITask, with fallback to chat."""
    return PROMPTS.get(task, PROMPTS[AITask.FINANCIAL_CHATBOT])


# ═══════════════════════════════════════════════════════════════════════════════
#  CONTEXT INJECTION HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def build_system_prompt(task: str, extra_rules: str = "") -> str:
    """Assemble the full system prompt for a task."""
    template = get_prompt(task)
    return template.system + (f"\n\n{extra_rules}" if extra_rules else "")


def render_user_prompt(task: str, context: dict) -> str:
    """
    Render the user_template for *task* by substituting *context* keys.
    Missing keys are replaced with "N/A" to avoid hard crashes.
    """
    template = get_prompt(task)
    safe_ctx = {k: (v if v is not None else "N/A") for k, v in context.items()}
    try:
        return template.user_template.format_map(safe_ctx)
    except KeyError as e:
        logger.warning(f"[ai-config] Missing prompt key {e} for task={task}")
        from collections import defaultdict
        return template.user_template.format_map(defaultdict(lambda: "N/A", safe_ctx))


def optimize_context(messages: list[dict], max_chars: int = 6000) -> list[dict]:
    """
    Trim conversation history from the oldest non-system messages first to stay
    within *max_chars*. Always preserves the system message and the latest user turn.
    """
    total = sum(len(m.get("content", "")) for m in messages)
    if total <= max_chars:
        return messages

    system_msgs = [m for m in messages if m.get("role") == "system"]
    other_msgs  = [m for m in messages if m.get("role") != "system"]

    while other_msgs and sum(len(m.get("content", "")) for m in system_msgs + other_msgs) > max_chars:
        if len(other_msgs) > 1:
            other_msgs.pop(0)    # drop oldest non-system message
        else:
            break

    logger.debug(f"[ai-config] Context trimmed: {total} → {sum(len(m.get('content','')) for m in system_msgs + other_msgs)} chars")
    return system_msgs + other_msgs


def build_payload(
    task: str,
    context: dict,
    provider: Optional[str] = None,
    override_temperature: Optional[float] = None,
    conversation_history: Optional[list[dict]] = None,
) -> dict:
    """
    Build the complete API payload for a given task and provider.
    Handles message assembly, context optimization, token limits, and JSON mode.
    """
    cfg      = get_provider(provider)
    template = get_prompt(task)
    model    = cfg.model_for(task)
    temp     = override_temperature if override_temperature is not None else TEMPERATURE_PROFILES.get(task, 0.7)

    system_content = build_system_prompt(task)
    user_content   = render_user_prompt(task, context)

    messages: list[dict] = [{"role": "system", "content": system_content}]
    if conversation_history:
        messages.extend(conversation_history)
    messages.append({"role": "user", "content": user_content})
    messages = optimize_context(messages)

    payload: dict[str, Any] = {
        "model":       model,
        "messages":    messages,
        "temperature": temp,
        "max_tokens":  min(template.max_tokens, cfg.max_tokens),
        "top_p":       cfg.top_p,
    }

    if cfg.freq_penalty:
        payload["frequency_penalty"] = cfg.freq_penalty
    if cfg.pres_penalty:
        payload["presence_penalty"] = cfg.pres_penalty
    if template.json_mode and cfg.name == AIProvider.OPENAI:
        payload["response_format"] = {"type": "json_object"}

    return payload


# ═══════════════════════════════════════════════════════════════════════════════
#  RETRY + FALLBACK LOGIC
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class RetryPolicy:
    max_attempts:      int   = 3
    base_delay_sec:    float = 1.0
    backoff_factor:    float = 2.0
    reduce_tokens_on_retry: bool = True
    fallback_providers: list[str] = field(default_factory=lambda: FALLBACK_CHAIN)


DEFAULT_RETRY = RetryPolicy()


def should_retry(status_code: int, attempt: int, policy: RetryPolicy = DEFAULT_RETRY) -> bool:
    """Return True if the request should be retried."""
    retryable = status_code in (429, 500, 502, 503, 504)
    return retryable and attempt < policy.max_attempts


def retry_delay(attempt: int, policy: RetryPolicy = DEFAULT_RETRY) -> float:
    """Exponential back-off delay in seconds."""
    return policy.base_delay_sec * (policy.backoff_factor ** (attempt - 1))


def next_fallback_provider(current_provider: str, tried: list[str]) -> Optional[str]:
    """Return the next provider in the fallback chain that hasn't been tried."""
    for p in FALLBACK_CHAIN:
        if p not in tried and p != current_provider:
            return p
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  MEMORY CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

AI_MEMORY_CONFIG = {
    "max_turns":           20,       # max conversation turns stored
    "max_context_chars":   6000,     # chars before trimming kicks in
    "summary_threshold":   15,       # turns before auto-summarize
    "expiry_hours":        24,       # conversation expires after inactivity
    "persona_injection":   True,     # always prepend personality prompt
    "context_compression": True,     # compress history before sending
}
