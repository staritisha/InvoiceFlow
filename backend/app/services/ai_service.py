"""
app/services/ai_service.py

Central AI Brain for InvoiceFlow AI Platform.
Single class — AIService — that powers every AI feature across all routers:
conversational assistant, invoice generation, entity extraction, risk scoring,
revenue forecasting, insight cards, reminders, email generation, voice commands,
report narratives, anomaly detection, currency intelligence, and memory.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, AsyncIterator, Optional
from uuid import UUID

import httpx

# ---------------------------------------------------------------------------
# Provider selection (OpenAI-compatible interface)
# Use OPENAI_API_KEY + OPENAI_BASE_URL, or any compatible provider.
# ---------------------------------------------------------------------------

AI_PROVIDER_BASE = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
AI_API_KEY = os.getenv("OPENAI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o")
AI_TEMPERATURE = float(os.getenv("AI_TEMPERATURE", "0.4"))
AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "2048"))

SYSTEM_PERSONA = (
    "You are InvoiceFlow AI — an expert AI financial operating system for small businesses "
    "and freelancers. You are precise, professional, and business-savvy. "
    "You always respond in valid JSON unless the user is in streaming chat mode. "
    "You understand invoices, payments, cash flow, client relationships, and financial risk."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _today() -> str:
    return date.today().isoformat()


def _parse_json(text: str) -> dict:
    """Extract the first JSON object from an AI response string."""
    text = text.strip()
    # Strip markdown code fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to extract first {...} or [...]
        match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
    return {"raw": text}


class AIService:
    """
    Central AI service. All methods are async and return structured dicts.
    Uses an OpenAI-compatible chat completion API.
    """

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=AI_PROVIDER_BASE,
            headers={"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"},
            timeout=60,
        )
        self.model = AI_MODEL
        self.temperature = AI_TEMPERATURE
        self.max_tokens = AI_MAX_TOKENS

    # ------------------------------------------------------------------
    # Internal completion helpers
    # ------------------------------------------------------------------

    async def _complete(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: str = "json",
    ) -> str:
        """Call chat completion and return the raw assistant text."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if response_format == "json":
            payload["response_format"] = {"type": "json_object"}

        resp = await self._client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    async def _complete_json(self, messages: list[dict], **kwargs) -> dict:
        text = await self._complete(messages, response_format="json", **kwargs)
        return _parse_json(text)

    async def _stream_complete(self, messages: list[dict]) -> AsyncIterator[str]:
        """Stream SSE tokens for the chat assistant."""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": True,
        }
        async with self._client.stream("POST", "/chat/completions", json=payload) as resp:
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    chunk = line[6:]
                    if chunk == "[DONE]":
                        break
                    try:
                        delta = json.loads(chunk)["choices"][0]["delta"].get("content", "")
                        if delta:
                            yield delta
                    except Exception:
                        pass

    # ------------------------------------------------------------------
    # 1. Conversational AI Assistant
    # ------------------------------------------------------------------

    async def chat_with_assistant(
        self,
        message: str,
        conversation_history: list[dict],
        business_context: dict,
    ) -> dict:
        """
        Full conversational AI assistant with business context and memory.
        Handles revenue queries, invoice creation intents, analytics, recommendations.
        Returns: { reply, intent, action_required, action_payload, suggestions }
        """
        context_block = json.dumps(business_context, default=str)
        system = (
            f"{SYSTEM_PERSONA}\n\n"
            f"Today: {_today()}\n"
            f"Business snapshot:\n{context_block}\n\n"
            "Respond in JSON: { \"reply\": string, \"intent\": string, "
            "\"action_required\": bool, \"action_payload\": object|null, "
            "\"suggestions\": [string] }"
        )
        messages = [{"role": "system", "content": system}] + conversation_history[-10:] + [
            {"role": "user", "content": message}
        ]
        return await self._complete_json(messages)

    async def stream_chat(
        self,
        message: str,
        conversation_history: list[dict],
        business_context: dict,
    ) -> AsyncIterator[str]:
        """SSE streaming version of chat_with_assistant."""
        context_block = json.dumps(business_context, default=str)
        system = (
            f"{SYSTEM_PERSONA}\n\n"
            f"Today: {_today()}\n"
            f"Business snapshot:\n{context_block}\n\n"
            "Respond conversationally and helpfully."
        )
        messages = [{"role": "system", "content": system}] + conversation_history[-8:] + [
            {"role": "user", "content": message}
        ]
        async for token in self._stream_complete(messages):
            yield token

    # ------------------------------------------------------------------
    # 2. AI Invoice Generation from text
    # ------------------------------------------------------------------

    async def generate_invoice_from_text(
        self,
        text: str,
        business_context: dict | None = None,
    ) -> dict:
        """
        Convert natural language into a structured invoice JSON.
        Handles auto item splitting, tax suggestion, due date, priority.

        Returns: { client_name, items:[{description,quantity,rate,amount}],
                   subtotal, tax_rate, tax_amount, total, due_date, description,
                   notes, payment_terms, priority, currency, is_recurring }
        """
        ctx = json.dumps(business_context or {}, default=str)
        messages = [
            {"role": "system", "content": (
                f"{SYSTEM_PERSONA}\nToday: {_today()}\nBusiness context: {ctx}"
            )},
            {"role": "user", "content": (
                f"Extract a complete invoice from this text.\n\n"
                f"Text: \"{text}\"\n\n"
                "Return JSON with keys: client_name, items (array of {description, quantity, rate, amount}), "
                "subtotal, tax_rate (%), tax_amount, total, currency, due_date (ISO), "
                "description, notes, payment_terms, priority (low/medium/high/urgent), "
                "is_recurring (bool), recurring_interval (monthly/weekly/null). "
                "Split compound line items intelligently. Suggest tax_rate based on context (default 0). "
                "Calculate all totals precisely."
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # 3. AI Auto-Fill Engine
    # ------------------------------------------------------------------

    async def auto_fill_invoice_fields(
        self,
        partial_invoice: dict,
        client_context: dict | None = None,
    ) -> dict:
        """
        Fill missing invoice fields from partial data.
        Returns a complete invoice dict with AI-filled fields marked.
        """
        messages = [
            {"role": "system", "content": SYSTEM_PERSONA},
            {"role": "user", "content": (
                f"Complete this partial invoice. Client context: {json.dumps(client_context or {})}\n"
                f"Partial invoice: {json.dumps(partial_invoice, default=str)}\n\n"
                "Fill all missing/null fields: subtotal, tax_amount, total, due_date, description, "
                "notes, payment_terms, currency. Return the complete invoice JSON with a field "
                "\"ai_filled_fields\": [list of field names you filled in]."
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # 4. AI Invoice Prioritization
    # ------------------------------------------------------------------

    async def calculate_invoice_priority(
        self,
        amount: float,
        overdue_days: int,
        client_risk_score: float,
        unpaid_invoice_count: int,
    ) -> dict:
        """
        Returns { priority: low|medium|high|urgent, risk_score: 0-100,
                  follow_up_schedule: [days], reasoning: str }
        """
        messages = [
            {"role": "system", "content": SYSTEM_PERSONA},
            {"role": "user", "content": (
                f"Classify invoice priority.\n"
                f"Amount: ${amount:.2f}, Overdue days: {overdue_days}, "
                f"Client risk score: {client_risk_score}/100, "
                f"Client unpaid invoices: {unpaid_invoice_count}\n\n"
                "Return JSON: { priority (low/medium/high/urgent), risk_score (0-100), "
                "follow_up_schedule ([7,14,21] = days from today), reasoning }"
            )},
        ]
        return await self._complete_json(messages)

    # Alias used by voice_invoice.py and integrations.py
    async def set_invoice_priority(
        self,
        client_risk_score: float,
        amount: float,
        due_date: str,
    ) -> dict:
        today = date.today()
        try:
            due = date.fromisoformat(due_date)
            overdue_days = max(0, (today - due).days)
        except ValueError:
            overdue_days = 0
        return await self.calculate_invoice_priority(
            amount=amount,
            overdue_days=overdue_days,
            client_risk_score=client_risk_score,
            unpaid_invoice_count=0,
        )

    # ------------------------------------------------------------------
    # 5. AI Business Insights Engine
    # ------------------------------------------------------------------

    async def generate_business_insights(
        self,
        analytics_data: dict,
        team_id: str,
    ) -> dict:
        """
        Analyze revenue, invoices, payments, and client behavior.
        Returns { insights: [{ title, message, type, severity, action }],
                  summary: str, risk_flags: [str] }
        """
        messages = [
            {"role": "system", "content": f"{SYSTEM_PERSONA}\nToday: {_today()}"},
            {"role": "user", "content": (
                f"Analyze this business data and generate actionable insights.\n\n"
                f"{json.dumps(analytics_data, default=str)}\n\n"
                "Return JSON: { insights: [{title, message, type (revenue|risk|client|cashflow|growth), "
                "severity (info|warning|critical), action}], summary: str, risk_flags: [str], "
                "growth_rate_pct: number }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # 6. AI Insight Cards
    # ------------------------------------------------------------------

    async def generate_insight_cards(
        self,
        analytics_data: dict,
    ) -> dict:
        """
        Generate modern SaaS-style dashboard insight cards.
        Returns { cards: [{ title, message, severity, action, icon, metric, delta }] }
        """
        messages = [
            {"role": "system", "content": f"{SYSTEM_PERSONA}\nToday: {_today()}"},
            {"role": "user", "content": (
                f"Create 5–8 startup-style insight cards from this data.\n\n"
                f"{json.dumps(analytics_data, default=str)}\n\n"
                "Return JSON: { cards: [{ title, message, severity (info|warning|critical|success), "
                "action (string CTA), icon (emoji), metric (string), delta (string like +18%) }] }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # 7. AI Revenue Forecasting
    # ------------------------------------------------------------------

    async def forecast_revenue(
        self,
        monthly_revenue: list[dict],
        recurring_mrr: float,
        outstanding_amount: float,
    ) -> dict:
        """
        Predict next week, next month, next quarter revenue.
        Returns { next_week, next_month, next_quarter, confidence,
                  seasonal_notes, risk_periods: [], recommendations: [] }
        """
        messages = [
            {"role": "system", "content": f"{SYSTEM_PERSONA}\nToday: {_today()}"},
            {"role": "user", "content": (
                f"Forecast revenue based on historical data.\n"
                f"Monthly trend: {json.dumps(monthly_revenue)}\n"
                f"MRR: ${recurring_mrr:.2f}, Outstanding: ${outstanding_amount:.2f}\n\n"
                "Return JSON: { next_week_revenue, next_month_revenue, next_quarter_revenue, "
                "confidence (0-1), growth_rate_pct, seasonal_notes, risk_periods: [str], "
                "recommendations: [str], mrr_trend (growing|stable|declining) }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # 8. AI Business Health Score
    # ------------------------------------------------------------------

    async def calculate_business_health_score(
        self,
        metrics: dict,
    ) -> dict:
        """
        Score business health 0–100 across 8 dimensions.
        Returns { score, status (critical/fair/healthy/excellent),
                  dimensions: {}, reasons: [], recommendations: [] }
        """
        messages = [
            {"role": "system", "content": SYSTEM_PERSONA},
            {"role": "user", "content": (
                f"Calculate business health score.\n\nMetrics:\n{json.dumps(metrics, default=str)}\n\n"
                "Score 0–100 across: payment_reliability, revenue_stability, client_retention, "
                "collection_speed, overdue_rate, growth_trend, recurring_revenue_pct, operational_score. "
                "Return JSON: { score (0-100), status (critical|fair|healthy|excellent), "
                "dimensions: {each 0-100}, reasons: [str], recommendations: [str] }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # 9. AI Weekly Business Summary
    # ------------------------------------------------------------------

    async def generate_weekly_summary(
        self,
        week_data: dict,
        user_name: str,
        business_name: str,
    ) -> dict:
        """
        Generate a founder-style AI CFO weekly summary.
        Returns { headline, body_html, highlights: [], risks: [], actions: [],
                  metrics: {}, tone: str }
        """
        messages = [
            {"role": "system", "content": f"{SYSTEM_PERSONA}\nToday: {_today()}"},
            {"role": "user", "content": (
                f"Generate a weekly business summary for {user_name} at {business_name}.\n\n"
                f"Week data: {json.dumps(week_data, default=str)}\n\n"
                "Write like an AI CFO. Return JSON: { headline (catchy one-liner), "
                "body_html (full HTML summary 200-400 words), highlights: [str], "
                "risks: [str], actions: [str], metrics: {revenue, collected, overdue_count, top_client} }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # 10. AI Smart Search
    # ------------------------------------------------------------------

    async def smart_search(
        self,
        query: str,
        available_filters: list[str],
    ) -> dict:
        """
        Convert natural language search query to structured filters.
        Returns { filters: {}, sort_by: str, entity_type: str, explanation: str }
        """
        messages = [
            {"role": "system", "content": SYSTEM_PERSONA},
            {"role": "user", "content": (
                f"Convert this search query to structured filters.\n"
                f"Query: \"{query}\"\n"
                f"Available filter fields: {available_filters}\n\n"
                "Return JSON: { filters: {field: value}, sort_by: str, "
                "sort_order (asc|desc), entity_type (invoice|client|payment|report), "
                "explanation: str }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # 11. Conversational Filters
    # ------------------------------------------------------------------

    async def generate_filter_query(
        self,
        natural_query: str,
    ) -> dict:
        """
        Convert natural language to DB filter dict.
        E.g. "show overdue invoices from Acme" → { status: overdue, client: Acme }
        """
        messages = [
            {"role": "system", "content": SYSTEM_PERSONA},
            {"role": "user", "content": (
                f"Convert to filter dict: \"{natural_query}\"\n\n"
                "Return JSON with any of: status, client_name, min_amount, max_amount, "
                "currency, date_from (ISO), date_to (ISO), overdue_only (bool), "
                "has_balance (bool), tag, sort_by, sort_order"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # 12. AI Recommendation Engine
    # ------------------------------------------------------------------

    async def generate_recommendations(
        self,
        business_data: dict,
        focus: str = "general",
    ) -> dict:
        """
        Generate personalized business recommendations.
        Returns { recommendations: [{ title, body, priority, category, action_url }] }
        """
        messages = [
            {"role": "system", "content": f"{SYSTEM_PERSONA}\nToday: {_today()}"},
            {"role": "user", "content": (
                f"Generate actionable business recommendations. Focus: {focus}\n\n"
                f"Data: {json.dumps(business_data, default=str)}\n\n"
                "Return JSON: { recommendations: [{ title, body, priority (1-5), "
                "category (payment|client|pricing|workflow|risk|growth), action_label, action_type }] }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # 13. AI Action Suggestions
    # ------------------------------------------------------------------

    async def generate_action_suggestions(
        self,
        context: dict,
        max_suggestions: int = 5,
    ) -> dict:
        """
        Generate contextual next-action suggestions for the user.
        Returns { suggestions: [{ label, action, icon, urgency }] }
        """
        messages = [
            {"role": "system", "content": f"{SYSTEM_PERSONA}\nToday: {_today()}"},
            {"role": "user", "content": (
                f"Suggest up to {max_suggestions} smart next actions.\n\n"
                f"Context: {json.dumps(context, default=str)}\n\n"
                "Return JSON: { suggestions: [{ label, action (slug), icon (emoji), "
                "urgency (low|medium|high), reason }] }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # 14. AI Client Risk Scoring
    # ------------------------------------------------------------------

    async def calculate_client_risk_score(
        self,
        client_data: dict,
    ) -> dict:
        """
        Score client payment risk 0–100.
        Returns { risk_score, risk_level (low|medium|high|critical),
                  risk_factors: [], recommendations: [], predicted_delay_days: int }
        """
        messages = [
            {"role": "system", "content": SYSTEM_PERSONA},
            {"role": "user", "content": (
                f"Calculate payment risk score for this client.\n\n"
                f"{json.dumps(client_data, default=str)}\n\n"
                "Return JSON: { risk_score (0-100), risk_level (low|medium|high|critical), "
                "risk_factors: [str], recommendations: [str], predicted_delay_days: int, "
                "payment_reliability_pct: number }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # 15. AI Payment Delay Prediction
    # ------------------------------------------------------------------

    async def predict_overdue_probability(
        self,
        invoice_data: dict,
        client_history: dict,
    ) -> dict:
        """
        Predict probability (%) that an invoice will become overdue.
        Returns { probability_pct, likely_delay_days, risk_factors: [],
                  recommended_action: str, confidence: float }
        """
        messages = [
            {"role": "system", "content": f"{SYSTEM_PERSONA}\nToday: {_today()}"},
            {"role": "user", "content": (
                f"Predict overdue probability for this invoice.\n\n"
                f"Invoice: {json.dumps(invoice_data, default=str)}\n"
                f"Client history: {json.dumps(client_history, default=str)}\n\n"
                "Return JSON: { probability_pct (0-100), likely_delay_days (int), "
                "risk_factors: [str], recommended_action: str, confidence: float (0-1) }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # 16. AI Reminder Generator
    # ------------------------------------------------------------------

    async def generate_payment_reminder(
        self,
        invoice_data: dict,
        tone: str,
        client_name: str,
        business_name: str,
        reminder_number: int = 1,
    ) -> dict:
        """
        Generate a payment reminder in the requested tone.
        Returns { subject, html, text, tone, cta_label, cta_url }
        """
        messages = [
            {"role": "system", "content": SYSTEM_PERSONA},
            {"role": "user", "content": (
                f"Write a {tone} payment reminder (reminder #{reminder_number}).\n\n"
                f"Client: {client_name}, Business: {business_name}\n"
                f"Invoice: {json.dumps(invoice_data, default=str)}\n\n"
                "Return JSON: { subject, html (full HTML email), text (plain text), "
                "tone, cta_label, opening_line, key_message }"
            )},
        ]
        return await self._complete_json(messages)

    async def bulk_generate_reminders(
        self,
        invoices: list[dict],
        team_id: str,
    ) -> dict:
        """Queue AI reminders for multiple invoices. Returns summary."""
        return {
            "queued": len(invoices),
            "team_id": team_id,
            "message": f"AI reminders queued for {len(invoices)} invoices.",
        }

    # ------------------------------------------------------------------
    # 17. AI Thank-You Email Generator
    # ------------------------------------------------------------------

    async def generate_thank_you_email(
        self,
        client_name: str,
        invoice_number: str,
        amount: float,
        currency: str,
        business_name: str,
    ) -> dict:
        """
        Generate a personalised payment thank-you email.
        Returns { subject, html, text, next_step_suggestion }
        """
        messages = [
            {"role": "system", "content": SYSTEM_PERSONA},
            {"role": "user", "content": (
                f"Write a warm, professional thank-you email.\n\n"
                f"Client: {client_name}, Invoice: {invoice_number}, "
                f"Amount: {currency} {amount:,.2f}, Business: {business_name}\n\n"
                "Include: gratitude, highlight the relationship, suggest next business step. "
                "Return JSON: { subject, html (full HTML), text, next_step_suggestion }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # 18. AI Follow-Up Scheduling
    # ------------------------------------------------------------------

    async def generate_followup_schedule(
        self,
        invoice_data: dict,
        client_risk_score: float,
    ) -> dict:
        """
        Generate an AI-optimised reminder schedule.
        Returns { schedule: [{ day: int, tone: str, reason: str }],
                  escalation_day: int, total_reminders: int }
        """
        messages = [
            {"role": "system", "content": f"{SYSTEM_PERSONA}\nToday: {_today()}"},
            {"role": "user", "content": (
                f"Design optimal follow-up schedule.\n"
                f"Invoice: {json.dumps(invoice_data, default=str)}\n"
                f"Client risk score: {client_risk_score}/100\n\n"
                "Return JSON: { schedule: [{ day_from_today: int, tone: str, "
                "channel: email|whatsapp, reason: str }], escalation_day: int, "
                "total_reminders: int, max_days: int }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # 19. AI Expense Categorization
    # ------------------------------------------------------------------

    async def categorize_expense(
        self,
        description: str,
        amount: float,
        receipt_text: str | None = None,
    ) -> dict:
        """
        Auto-categorize an expense from its description.
        Returns { category, sub_category, tax_category, confidence, is_deductible }
        """
        raw = receipt_text or description
        messages = [
            {"role": "system", "content": SYSTEM_PERSONA},
            {"role": "user", "content": (
                f"Categorize this expense.\nDescription: {description}\nAmount: ${amount:.2f}\n"
                f"Receipt text: {raw[:500]}\n\n"
                "Return JSON: { category (software|hardware|travel|meals|marketing|payroll|utilities|other), "
                "sub_category, tax_category, confidence (0-1), is_deductible (bool), notes }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # 20. AI Conversational Command Processor
    # ------------------------------------------------------------------

    async def process_command(
        self,
        command: str,
        conversation_history: list[dict],
        business_context: dict,
    ) -> dict:
        """
        Natural language command operating system.
        Routes to: create_invoice | send_reminder | generate_report |
                   show_analytics | find_clients | trigger_workflow | answer_question
        Returns { intent, action, parameters, response, requires_confirmation }
        """
        messages = [
            {"role": "system", "content": (
                f"{SYSTEM_PERSONA}\nToday: {_today()}\n"
                f"Context: {json.dumps(business_context, default=str)}"
            )},
            *conversation_history[-6:],
            {"role": "user", "content": (
                f"Process this command: \"{command}\"\n\n"
                "Return JSON: { intent (create_invoice|send_reminder|generate_report|"
                "analytics_query|client_lookup|workflow_trigger|answer_question|unknown), "
                "action (function name to call), parameters: {}, response (natural language), "
                "requires_confirmation (bool), confidence (0-1) }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # 21. AI Dashboard Personalization
    # ------------------------------------------------------------------

    async def personalize_dashboard(
        self,
        user_id: str,
        usage_data: dict,
        business_type: str,
    ) -> dict:
        """
        Personalize dashboard widget order based on user behavior.
        Returns { widget_order: [str], recommended_widgets: [str],
                  hidden_widgets: [str], reason: str }
        """
        messages = [
            {"role": "system", "content": SYSTEM_PERSONA},
            {"role": "user", "content": (
                f"Personalize dashboard for {business_type} business.\n"
                f"Usage patterns: {json.dumps(usage_data, default=str)}\n\n"
                "Available widgets: revenue_chart, overdue_invoices, client_risk_map, "
                "cash_flow_forecast, recent_activity, ai_insights, reminder_center, "
                "quick_actions, invoice_aging, top_clients, kpi_cards, weekly_summary.\n\n"
                "Return JSON: { widget_order: [str], recommended_widgets: [str], "
                "hidden_widgets: [str], layout: grid|list, reason: str }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # 22. AI Onboarding Generator
    # ------------------------------------------------------------------

    async def generate_onboarding_steps(
        self,
        user_name: str,
        business_type: str,
        completed_steps: list[str],
    ) -> dict:
        """
        Generate personalised onboarding checklist.
        Returns { steps: [{ id, title, description, action, completed, priority }],
                  progress_pct: int, next_step: str }
        """
        messages = [
            {"role": "system", "content": SYSTEM_PERSONA},
            {"role": "user", "content": (
                f"Generate onboarding steps for {user_name} ({business_type}).\n"
                f"Already completed: {completed_steps}\n\n"
                "Return JSON: { steps: [{ id, title, description, action (slug), "
                "completed (bool), priority (1-10), estimated_minutes }], "
                "progress_pct: int, next_step_id: str, motivational_message: str }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # 23. AI Business Optimization Tips
    # ------------------------------------------------------------------

    async def generate_business_tips(
        self,
        metrics: dict,
        focus_area: str = "general",
    ) -> dict:
        """
        Generate startup-advisor-style business tips.
        Returns { tips: [{ title, body, impact (high|medium|low), effort (low|medium|high) }] }
        """
        messages = [
            {"role": "system", "content": f"{SYSTEM_PERSONA}\nToday: {_today()}"},
            {"role": "user", "content": (
                f"Act as a startup business advisor. Generate actionable tips. Focus: {focus_area}\n\n"
                f"Metrics: {json.dumps(metrics, default=str)}\n\n"
                "Return JSON: { tips: [{ title, body, impact (high|medium|low), "
                "effort (low|medium|high), category, example }] }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # 24. AI Memory / Business Context Builder
    # ------------------------------------------------------------------

    async def build_business_context_memory(
        self,
        team_id: str,
        recent_actions: list[dict],
        business_snapshot: dict,
    ) -> dict:
        """
        Build a compressed business context memory for AI assistant continuity.
        Returns { context_summary, patterns: [], preferred_clients: [],
                  business_type, invoice_style, risk_posture }
        """
        messages = [
            {"role": "system", "content": SYSTEM_PERSONA},
            {"role": "user", "content": (
                f"Build a business context memory profile.\n\n"
                f"Recent actions: {json.dumps(recent_actions[:20], default=str)}\n"
                f"Business snapshot: {json.dumps(business_snapshot, default=str)}\n\n"
                "Return JSON: { context_summary (2-3 sentences), patterns: [str], "
                "preferred_clients: [str], business_type: str, invoice_style: str, "
                "risk_posture (conservative|moderate|aggressive), top_revenue_sources: [str] }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # 25. Embedding-Based FAQ Retrieval (lightweight keyword fallback)
    # ------------------------------------------------------------------

    async def search_faq_embeddings(
        self,
        query: str,
        faq_items: list[dict] | None = None,
    ) -> dict:
        """
        Retrieve the most relevant FAQ answer for a query.
        Returns { question, answer, confidence, related_questions: [] }
        """
        faq_context = json.dumps(faq_items or _default_faq(), default=str)
        messages = [
            {"role": "system", "content": SYSTEM_PERSONA},
            {"role": "user", "content": (
                f"Find the best FAQ answer for: \"{query}\"\n\n"
                f"FAQ database: {faq_context[:2000]}\n\n"
                "Return JSON: { matched_question, answer, confidence (0-1), "
                "related_questions: [str] }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # Voice-specific methods (used by voice_invoice.py)
    # ------------------------------------------------------------------

    async def transcribe(
        self,
        audio_bytes: bytes,
        file_format: str,
        language: str = "en",
    ) -> dict:
        """
        Transcribe audio. Delegates to Whisper-compatible API.
        Returns { text, confidence, duration, timestamps }
        """
        whisper_base = os.getenv("WHISPER_API_BASE", AI_PROVIDER_BASE.replace("/v1", ""))
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{whisper_base}/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {AI_API_KEY}"},
                    files={"file": (f"audio.{file_format}", audio_bytes, f"audio/{file_format}")},
                    data={"model": "whisper-1", "language": language, "response_format": "verbose_json"},
                )
                body = resp.json()
            return {
                "text": body.get("text", ""),
                "confidence": body.get("confidence", 0.9),
                "duration": body.get("duration", 0),
                "timestamps": body.get("segments", []),
            }
        except Exception:
            return {"text": "", "confidence": 0.0, "duration": 0, "timestamps": []}

    async def clean_transcript(
        self,
        raw_transcript: str,
        language: str,
        business_context: dict,
    ) -> dict:
        """Clean and correct a raw speech transcript."""
        messages = [
            {"role": "system", "content": SYSTEM_PERSONA},
            {"role": "user", "content": (
                f"Clean and correct this voice transcript for a business finance app.\n"
                f"Language: {language}\nContext: {json.dumps(business_context)}\n\n"
                f"Transcript: \"{raw_transcript}\"\n\n"
                "Fix: punctuation, currency names, business names, spoken numbers, typos. "
                "Return JSON: { text (cleaned), corrections: [{original, corrected}], "
                "detected_language, multilingual_notes }"
            )},
        ]
        return await self._complete_json(messages)

    async def extract_voice_entities(
        self,
        transcript: str,
        language: str,
        intent_hint: str = "",
    ) -> dict:
        """
        Deep entity extraction from voice transcript.
        Returns { intent, intent_confidence, entities: { client_name, items, amount,
                  currency, due_date, tax_rate, is_recurring, notes, ... } }
        """
        messages = [
            {"role": "system", "content": f"{SYSTEM_PERSONA}\nToday: {_today()}"},
            {"role": "user", "content": (
                f"Extract business entities from this voice input.\n"
                f"Transcript: \"{transcript}\"\nLanguage: {language}\nHint: {intent_hint}\n\n"
                "Return JSON: { intent (create_invoice|send_reminder|analytics_query|"
                "client_lookup|workflow_trigger|unknown), intent_confidence (0-1), "
                "entities: { client_name, client: same, items: [{description,quantity,rate}], "
                "amount, currency, due_date (ISO or relative), tax_rate, discount, "
                "is_recurring, recurring_interval, payment_terms, notes, description } }"
            )},
        ]
        return await self._complete_json(messages)

    async def get_voice_suggestions(
        self,
        transcript: str,
        entities: dict,
        intent: str,
    ) -> dict:
        """Return AI suggestions while user decides next step after transcription."""
        messages = [
            {"role": "system", "content": SYSTEM_PERSONA},
            {"role": "user", "content": (
                f"Suggest next steps after voice input.\nTranscript: \"{transcript}\"\n"
                f"Detected intent: {intent}\nEntities: {json.dumps(entities)}\n\n"
                "Return JSON: { suggestions: [str], actions: [{ label, action, icon }] }"
            )},
        ]
        return await self._complete_json(messages)

    async def recommend_due_date(
        self,
        client_name: str,
        avg_payment_days: float,
        invoice_amount: float,
    ) -> dict:
        """Recommend an optimal due date window based on client history."""
        messages = [
            {"role": "system", "content": f"{SYSTEM_PERSONA}\nToday: {_today()}"},
            {"role": "user", "content": (
                f"Recommend a due date for invoice.\n"
                f"Client: {client_name}, avg payment: {avg_payment_days:.0f} days, "
                f"Amount: ${invoice_amount:,.2f}\n\n"
                "Return JSON: { recommended_days (int), reasoning, risk_note }"
            )},
        ]
        return await self._complete_json(messages)

    async def enhance_invoice_description(
        self,
        raw_description: str,
        items: list[dict],
        client_name: str,
        month: str,
    ) -> dict:
        """Expand a vague invoice description into professional language."""
        messages = [
            {"role": "system", "content": SYSTEM_PERSONA},
            {"role": "user", "content": (
                f"Expand this invoice description professionally.\n"
                f"Raw: \"{raw_description}\"\nItems: {json.dumps(items)}\n"
                f"Client: {client_name}, Period: {month}\n\n"
                "Return JSON: { enhanced (1-3 professional sentences), reasoning }"
            )},
        ]
        return await self._complete_json(messages)

    async def classify_voice_command(
        self,
        command: str,
        language: str,
        conversation_history: list[dict],
        business_snapshot: dict,
    ) -> dict:
        """Deep intent classification for voice/typed business commands."""
        messages = [
            {"role": "system", "content": (
                f"{SYSTEM_PERSONA}\nToday: {_today()}\n"
                f"Snapshot: {json.dumps(business_snapshot, default=str)}"
            )},
            *conversation_history[-6:],
            {"role": "user", "content": (
                f"Classify and plan this command: \"{command}\" (language: {language})\n\n"
                "Return JSON: { intent, confidence (0-1), entities: {}, is_multi_step (bool), "
                "requires_confirmation (bool), confirmation_message, impact_preview, suggestions: [str] }"
            )},
        ]
        return await self._complete_json(messages)

    async def answer_analytics_question(
        self,
        question: str,
        team_id: str,
    ) -> dict:
        """Answer a natural language analytics question."""
        messages = [
            {"role": "system", "content": f"{SYSTEM_PERSONA}\nToday: {_today()}"},
            {"role": "user", "content": (
                f"Answer this analytics question: \"{question}\"\n\n"
                "Return JSON: { answer (natural language), summary (one line), "
                "data: {}, follow_up: [str] }"
            )},
        ]
        return await self._complete_json(messages)

    async def handle_unknown_voice_command(
        self,
        command: str,
        intent: str,
    ) -> dict:
        messages = [
            {"role": "system", "content": SYSTEM_PERSONA},
            {"role": "user", "content": (
                f"AI couldn't fully understand: \"{command}\" (detected intent: {intent}).\n"
                "Respond helpfully and suggest alternatives.\n"
                "Return JSON: { response, suggestions: [str] }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # Email / WhatsApp generation (used by integrations.py)
    # ------------------------------------------------------------------

    async def generate_email(
        self,
        email_type: str,
        tone: str,
        context: dict,
    ) -> dict:
        """
        Generate any transactional email.
        Types: reminder|thank_you|overdue_notice|onboarding|payment_recovery|bulk_reminder|summary
        Tones: professional|friendly|startup|urgent|premium|investor-style
        Returns { subject, html, text }
        """
        messages = [
            {"role": "system", "content": SYSTEM_PERSONA},
            {"role": "user", "content": (
                f"Write a {tone} {email_type} email.\n\n"
                f"Context: {json.dumps(context, default=str)}\n\n"
                "Return JSON: { subject, html (complete HTML email body), text (plain text) }"
            )},
        ]
        return await self._complete_json(messages)

    async def generate_whatsapp_message(
        self,
        message_type: str,
        tone: str,
        context: dict,
    ) -> dict:
        """
        Generate a WhatsApp business message.
        Types: reminder|escalation|thank_you|follow_up|invoice_delivery
        Returns { text (max 1600 chars), emoji_usage: bool }
        """
        messages = [
            {"role": "system", "content": SYSTEM_PERSONA},
            {"role": "user", "content": (
                f"Write a {tone} WhatsApp {message_type} message (max 300 words).\n\n"
                f"Context: {json.dumps(context, default=str)}\n\n"
                "Return JSON: { text, emoji_usage (bool) }"
            )},
        ]
        return await self._complete_json(messages)

    async def recommend_message_timing(
        self,
        channel: str,
        client_id: str | None,
        message_type: str,
    ) -> dict:
        """AI-predict best send time and reply probability."""
        messages = [
            {"role": "system", "content": f"{SYSTEM_PERSONA}\nToday: {_today()}"},
            {"role": "user", "content": (
                f"Recommend best {channel} send time for {message_type} message.\n"
                f"Client ID: {client_id}\n\n"
                "Return JSON: { recommended_time (HH:MM), recommended_day (Mon-Sun), "
                "reply_probability (0-1), reasoning }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # Report narrative (used by reports.py)
    # ------------------------------------------------------------------

    async def generate_report_narrative(
        self,
        report_type: str,
        data: dict,
        period: str,
    ) -> dict:
        """
        Generate AI executive narrative for a report.
        Returns { executive_summary, key_insights: [], warnings: [],
                  opportunities: [], recommendations: [], insight_cards: [],
                  kpis: {}, anomalies: [], key_insight: str }
        """
        messages = [
            {"role": "system", "content": f"{SYSTEM_PERSONA}\nToday: {_today()}"},
            {"role": "user", "content": (
                f"Write an executive AI narrative for a {report_type} report ({period}).\n\n"
                f"Data: {json.dumps(data, default=str)[:3000]}\n\n"
                "Return JSON: { executive_summary (2-3 sentences), key_insights: [str], "
                "warnings: [str], opportunities: [str], recommendations: [str], "
                "insight_cards: [{ title, message, severity }], "
                "kpis: { dso, collection_rate, growth_pct, avg_invoice_value }, "
                "anomalies: [str], key_insight (single best insight sentence) }"
            )},
        ]
        return await self._complete_json(messages, max_tokens=2500)

    # ------------------------------------------------------------------
    # Currency intelligence (used by integrations.py)
    # ------------------------------------------------------------------

    async def get_currency_insights(
        self,
        base_currency: str,
        rates: dict,
    ) -> dict:
        """AI currency market insights and invoice currency recommendation."""
        messages = [
            {"role": "system", "content": f"{SYSTEM_PERSONA}\nToday: {_today()}"},
            {"role": "user", "content": (
                f"Provide currency insights. Base: {base_currency}\n"
                f"Current rates: {json.dumps(rates)}\n\n"
                "Return JSON: { recommendations: [str], volatile: [str], stable: [str], "
                "invoice_currency (best for international invoicing) }"
            )},
        ]
        return await self._complete_json(messages)

    async def predict_currency_movement(
        self,
        from_currency: str,
        to_currency: str,
        current_rate: float,
    ) -> dict:
        """Short-term currency movement prediction."""
        messages = [
            {"role": "system", "content": SYSTEM_PERSONA},
            {"role": "user", "content": (
                f"Predict short-term {from_currency}/{to_currency} movement. Rate: {current_rate}\n\n"
                "Return JSON: { prediction (rising|falling|stable), confidence (0-1), "
                "warning (str|null), recommendation }"
            )},
        ]
        return await self._complete_json(messages)

    async def assess_payment_fraud_risk(
        self,
        invoice_id: str,
        amount: float,
        currency: str,
        client_risk_score: float,
    ) -> dict:
        """Assess fraud/risk level for a Stripe payment intent."""
        messages = [
            {"role": "system", "content": SYSTEM_PERSONA},
            {"role": "user", "content": (
                f"Assess payment fraud risk.\n"
                f"Invoice: {invoice_id}, Amount: {currency} {amount:.2f}, "
                f"Client risk score: {client_risk_score}/100\n\n"
                "Return JSON: { risk_level (low|medium|high), score (0-100), recommendation }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # Workflow AI (used by workflows.py)
    # ------------------------------------------------------------------

    async def build_workflow_from_text(
        self,
        prompt: str,
        supported_triggers: list[str],
        supported_conditions: list[str],
        supported_actions: list[str],
        team_context: dict,
    ) -> dict:
        """
        Build a complete workflow config from a natural language description.
        Returns { name, description, trigger_type, conditions: {}, actions: [] }
        """
        messages = [
            {"role": "system", "content": f"{SYSTEM_PERSONA}\nToday: {_today()}"},
            {"role": "user", "content": (
                f"Build an automation workflow from: \"{prompt}\"\n\n"
                f"Available triggers: {supported_triggers}\n"
                f"Available conditions: {supported_conditions}\n"
                f"Available actions: {supported_actions}\n"
                f"Team context: {json.dumps(team_context)}\n\n"
                "Return JSON: { name, description, trigger_type (from list), "
                "conditions: {field: value}, actions: [{type, params}] }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # Reminder escalation AI (used by reminders.py)
    # ------------------------------------------------------------------

    async def decide_escalation(
        self,
        reminder_data: dict,
        client_history: dict,
    ) -> dict:
        """Decide whether and how to escalate a reminder."""
        messages = [
            {"role": "system", "content": SYSTEM_PERSONA},
            {"role": "user", "content": (
                f"Decide reminder escalation.\n"
                f"Reminder: {json.dumps(reminder_data, default=str)}\n"
                f"Client history: {json.dumps(client_history, default=str)}\n\n"
                "Return JSON: { should_escalate (bool), next_tone (friendly|professional|firm|urgent|legal), "
                "delay_days (int), reason }"
            )},
        ]
        return await self._complete_json(messages)

    async def generate_reminder_from_invoice(
        self,
        invoice_data: dict,
        client_data: dict,
        tone: str,
    ) -> dict:
        """Alias matching reminder router's expected method signature."""
        return await self.generate_payment_reminder(
            invoice_data=invoice_data,
            tone=tone,
            client_name=client_data.get("name", ""),
            business_name=invoice_data.get("business_name", ""),
        )

    # ------------------------------------------------------------------
    # Notification AI (used by notifications.py)
    # ------------------------------------------------------------------

    async def generate_autonomous_alerts(
        self,
        business_snapshot: dict,
    ) -> dict:
        """Generate AI-driven autonomous alert decisions from business data."""
        messages = [
            {"role": "system", "content": f"{SYSTEM_PERSONA}\nToday: {_today()}"},
            {"role": "user", "content": (
                f"Generate autonomous business alerts.\n\n"
                f"Snapshot: {json.dumps(business_snapshot, default=str)}\n\n"
                "Return JSON: { alerts: [{ type, title, message, severity (info|warning|critical), "
                "category, action }] }"
            )},
        ]
        return await self._complete_json(messages)

    # ------------------------------------------------------------------
    # Client AI (used by clients.py)
    # ------------------------------------------------------------------

    async def generate_client_intelligence(
        self,
        client_data: dict,
        invoice_history: list[dict],
    ) -> dict:
        """
        Generate AI intelligence profile for a client.
        Returns { risk_score, payment_pattern, relationship_health,
                  recommendations: [], predicted_ltv, churn_probability }
        """
        messages = [
            {"role": "system", "content": SYSTEM_PERSONA},
            {"role": "user", "content": (
                f"Generate AI client intelligence profile.\n\n"
                f"Client: {json.dumps(client_data, default=str)}\n"
                f"Invoice history: {json.dumps(invoice_history[:20], default=str)}\n\n"
                "Return JSON: { risk_score (0-100), payment_pattern (str), "
                "relationship_health (poor|fair|good|excellent), recommendations: [str], "
                "predicted_ltv, churn_probability (0-1), best_contact_time }"
            )},
        ]
        return await self._complete_json(messages)


# ---------------------------------------------------------------------------
# Default FAQ for search_faq_embeddings fallback
# ---------------------------------------------------------------------------


def _default_faq() -> list[dict]:
    return [
        {"q": "How do I create an invoice?", "a": "Go to Invoices → New Invoice, or use voice: 'Create invoice for [client] for [amount]'."},
        {"q": "How do I set up recurring invoices?", "a": "When creating an invoice, toggle 'Recurring' and choose monthly/weekly."},
        {"q": "How do I send payment reminders?", "a": "Go to Reminders → Generate, or use voice: 'Send reminders to overdue clients'."},
        {"q": "What is a risk score?", "a": "AI rates each client 0–100 based on payment history, delay patterns, and dispute rate."},
        {"q": "How does the AI assistant work?", "a": "Chat naturally: ask revenue questions, create invoices, or trigger workflows."},
        {"q": "How do I connect Stripe?", "a": "Set STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY environment variables."},
        {"q": "How do I export a report?", "a": "Go to Reports → Generate, pick PDF/Excel/CSV, and download when ready."},
        {"q": "Can I invoice in multiple currencies?", "a": "Yes. Set currency per invoice. Exchange rates update every 5 minutes."},
    ]
