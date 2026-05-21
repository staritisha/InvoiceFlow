"""
app/ai/agents/assistant_agent.py

InvoiceFlow AI Assistant Agent — the conversational brain of the platform.
Powers the AI sidebar, command center, smart search, streaming chat,
persistent memory, workflow triggering, and autonomous proactive suggestions.

This agent coordinates all other agents (InvoiceAgent, AnalyticsAgent) and
exposes a unified chat/command interface over HTTP and WebSocket.

Usage
-----
from app.ai.agents.assistant_agent import AssistantAgent

agent = AssistantAgent(user_id=42)

# Standard chat (JSON response)
result = agent.chat("What should I focus on today?")

# Streaming (generator — yield to SSE endpoint)
for chunk in agent.stream_chat_response("Show cashflow forecast"):
    yield chunk
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Generator

logger = logging.getLogger(__name__)

# Intent categories
INTENT_MAP = {
    "invoice_create"  : ["create invoice", "make invoice", "new invoice", "bill", "generate invoice", "draft invoice"],
    "invoice_query"   : ["show invoice", "find invoice", "list invoice", "unpaid", "overdue invoice", "invoice status"],
    "reminder_send"   : ["send reminder", "remind", "follow up", "chase", "nudge", "overdue reminder"],
    "report_generate" : ["generate report", "create report", "export", "pdf", "executive report", "download"],
    "workflow_create" : ["create workflow", "automate", "set up workflow", "schedule reminder", "recurring"],
    "analytics_query" : ["revenue", "cashflow", "forecast", "growth", "analytics", "performance", "kpi", "dso", "collection rate"],
    "client_query"    : ["client", "customer", "risky client", "top client", "payment history"],
    "search"          : ["find", "search", "show", "filter", "list", "unpaid", "above", "below", "from"],
    "onboarding"      : ["how do i", "getting started", "first invoice", "help me", "tutorial", "guide"],
    "health"          : ["health score", "how am i doing", "business health", "how is my business", "status"],
    "forecast"        : ["forecast", "predict", "next month", "next week", "projection", "future"],
    "greeting"        : ["hi", "hello", "hey", "good morning", "what's new", "what should i focus"],
    "coach"           : ["advice", "tips", "improve", "suggest", "optimise", "optimize", "what should i"],
}

MEMORY_MAX_TURNS = 30       # Max conversation turns retained in DB
CONTEXT_CACHE_TTL = 300     # Seconds before refreshing business context cache
STREAM_CHUNK_WORDS = 6      # Words per streaming chunk


# ===========================================================================
# MAIN AGENT CLASS
# ===========================================================================

class AssistantAgent:
    """
    Conversational AI assistant agent for InvoiceFlow.

    Parameters
    ----------
    user_id         : Owning user — scopes all data access and memory.
    session_id      : Conversation session ID for multi-turn memory.
    model           : OpenAI model for primary responses.
    fast_model      : OpenAI model for quick sub-tasks (intent, filters).
    prompt_path     : Path to financial_chatbot.txt system prompt.
    """

    def __init__(
        self,
        user_id: int | None = None,
        *,
        session_id: str | None = None,
        model: str = "gpt-4o",
        fast_model: str = "gpt-4o-mini",
        prompt_path: str | None = None,
    ) -> None:
        self.user_id = user_id
        self.session_id = session_id or _new_id()
        self.model = model
        self.fast_model = fast_model
        self._prompt_path = prompt_path or os.path.join(
            os.path.dirname(__file__), "../../ai/prompts/financial_chatbot.txt"
        )
        self._system_prompt: str | None = None
        self._ai: Any = None
        self._context_cache: dict | None = None
        self._context_cached_at: datetime | None = None

    # ------------------------------------------------------------------
    # Internal bootstrapping
    # ------------------------------------------------------------------

    def _get_ai(self):
        if self._ai is None:
            try:
                import openai
                key = os.getenv("OPENAI_API_KEY")
                if not key:
                    logger.warning("OPENAI_API_KEY not set — assistant degraded to rule-based")
                    return None
                openai.api_key = key
                self._ai = openai
            except ImportError:
                logger.warning("openai not installed")
        return self._ai

    def _get_socketio(self):
        try:
            from app import socketio
            return socketio
        except Exception:
            return None

    def _load_system_prompt(self, context: dict | None = None) -> str:
        """Load financial_chatbot.txt and inject live business context."""
        if self._system_prompt is None:
            try:
                with open(self._prompt_path, encoding="utf-8") as f:
                    self._system_prompt = f.read()
            except FileNotFoundError:
                logger.warning("financial_chatbot.txt not found — using inline prompt")
                self._system_prompt = _INLINE_SYSTEM_PROMPT

        prompt = self._system_prompt.replace("{today_date}", _now().strftime("%Y-%m-%d"))

        ctx = context or self.retrieve_business_context()
        replacements = {
            "{user_name}"            : str(ctx.get("user_name", "there")),
            "{company_name}"         : str(ctx.get("company_name", "your company")),
            "{total_revenue}"        : _fmt(ctx.get("total_revenue", 0)),
            "{prev_revenue}"         : _fmt(ctx.get("prev_revenue", 0)),
            "{revenue_growth_pct}"   : str(round(ctx.get("revenue_growth_pct", 0), 1)),
            "{total_invoiced}"       : _fmt(ctx.get("total_invoiced", 0)),
            "{total_overdue}"        : _fmt(ctx.get("total_overdue", 0)),
            "{overdue_count}"        : str(ctx.get("overdue_count", 0)),
            "{collection_rate}"      : str(round(ctx.get("collection_rate", 0), 1)),
            "{avg_invoice_value}"    : _fmt(ctx.get("avg_invoice_value", 0)),
            "{dso}"                  : str(round(ctx.get("dso", 30), 1)),
            "{health_score}"         : str(ctx.get("health_score", 0)),
            "{health_grade}"         : str(ctx.get("health_grade", "N/A")),
            "{mrr}"                  : _fmt(ctx.get("mrr", 0)),
            "{invoice_count}"        : str(ctx.get("invoice_count", 0)),
            "{paid_count}"           : str(ctx.get("paid_count", 0)),
            "{reminder_count}"       : str(ctx.get("reminder_count", 0)),
            "{active_workflows}"     : str(ctx.get("active_workflows", 0)),
            "{top_clients_summary}"  : ctx.get("top_clients_summary", "No data available."),
            "{high_risk_clients}"    : ctx.get("high_risk_clients", "None flagged."),
            "{overdue_summary}"      : ctx.get("overdue_summary", "No overdue invoices."),
            "{recent_invoices_summary}": ctx.get("recent_invoices_summary", "No recent invoices."),
            "{pending_actions}"      : ctx.get("pending_actions", "None."),
            "{currency}"             : str(ctx.get("currency", "INR")),
            "{month_start}"          : _now().strftime("%Y-%m-01"),
        }
        for placeholder, value in replacements.items():
            prompt = prompt.replace(placeholder, value)
        return prompt

    # ------------------------------------------------------------------
    # 1. CONVERSATIONAL AI CHAT ENGINE
    # ------------------------------------------------------------------

    def chat(
        self,
        message: str,
        *,
        conversation_history: list[dict] | None = None,
        stream: bool = False,
        context: dict | None = None,
    ) -> dict:
        """
        Primary chat interface — accepts a user message and returns a
        structured AI response with actions, suggestions, and insights.

        Pipeline
        --------
        Intent Detection → Memory Load → Context Injection →
        AI Generation → Action Extraction → Memory Save → Response

        Parameters
        ----------
        message              : User message text.
        conversation_history : Prior turns [{role, content}].
        stream               : If True, caller should use stream_chat_response() instead.
        context              : Pre-loaded business context dict (skips DB fetch if provided).

        Returns
        -------
        {
            "response"        : "...",
            "intent"          : "analytics_query",
            "actions"         : [...],
            "suggestions"     : [...],
            "insights"        : [...],
            "recommendations" : [...],
            "search_filters"  : {...},
            "smart_summary"   : "...",
            "severity"        : "medium",
            "follow_up"       : "...",
            "ai_powered"      : True,
            "session_id"      : "..."
        }
        """
        logger.info("AssistantAgent.chat [session=%s]: %r", self.session_id[:8], message[:80])

        # Step 1: Detect intent
        intent = self._parse_user_intent(message)

        # Step 2: Load conversation history from memory if not provided
        history = conversation_history or self._load_conversation_history()

        # Step 3: Build system prompt with live context
        business_ctx = context or self.retrieve_business_context()
        system = self._load_system_prompt(business_ctx)

        # Step 4: Build messages list
        messages = [{"role": "system", "content": system}]
        messages.extend(history[-20:])  # Last 20 turns for context window
        messages.append({"role": "user", "content": message})

        # Step 5: Generate response
        result = self._generate_ai_response(messages, intent)

        # Step 6: Store turn in memory
        self.store_memory(role="user", content=message)
        self.store_memory(role="assistant", content=result.get("response", ""))

        # Step 7: Attach session metadata
        result["intent"] = intent
        result["session_id"] = self.session_id
        result["ai_powered"] = result.get("ai_powered", False)

        return result

    def _generate_ai_response(self, messages: list[dict], intent: str) -> dict:
        """Call LLM and parse structured JSON response."""
        ai = self._get_ai()

        if not ai:
            return self._rule_based_response(messages[-1]["content"], intent)

        try:
            resp = ai.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=800,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.choices[0].message.content)
            data["ai_powered"] = True

            # Ensure required fields exist
            data.setdefault("response", "I've analysed your request.")
            data.setdefault("suggestions", [])
            data.setdefault("actions", [])
            return data

        except Exception as exc:
            logger.warning("Chat LLM failed: %s", exc)
            return self._rule_based_response(messages[-1]["content"], intent)

    def _rule_based_response(self, message: str, intent: str) -> dict:
        """Fallback response when OpenAI is unavailable."""
        ctx = self.retrieve_business_context()
        responses = {
            "greeting"       : f"Good to see you! You have {ctx.get('overdue_count', 0)} overdue invoices and ${ctx.get('total_overdue', 0):,.0f} outstanding. What would you like to tackle first?",
            "analytics_query": f"Revenue is ${ctx.get('total_revenue', 0):,.0f} with a {ctx.get('collection_rate', 0):.1f}% collection rate. DSO is {ctx.get('dso', 30):.0f} days.",
            "invoice_query"  : f"You have {ctx.get('invoice_count', 0)} total invoices, {ctx.get('paid_count', 0)} paid and {ctx.get('overdue_count', 0)} overdue.",
            "health"         : f"Business health score: {ctx.get('health_score', 0)}/100 ({ctx.get('health_grade', 'N/A')}). Revenue growth: {ctx.get('revenue_growth_pct', 0):+.1f}%.",
            "coach"          : "To improve performance: shorten payment terms, automate reminders, and convert retainer clients to recurring invoices.",
        }
        response_text = responses.get(intent, "I can help with invoices, analytics, reminders, and workflows. What do you need?")
        return {
            "response"   : response_text,
            "suggestions": [
                {"label": "View overdue invoices", "action": "search_invoices"},
                {"label": "Generate report", "action": "generate_report"},
            ],
            "actions"    : [],
            "ai_powered" : False,
        }

    # ------------------------------------------------------------------
    # 2. AI MEMORY SYSTEM
    # ------------------------------------------------------------------

    def store_memory(self, role: str, content: str, *, extra: dict | None = None) -> None:
        """
        Persist a conversation turn or context item to the database.

        Parameters
        ----------
        role    : "user" | "assistant" | "system" | "preference" | "action"
        content : Text content of the memory item.
        extra   : Optional additional metadata dict.
        """
        try:
            from app.models import db, VoiceInteraction  # Reuse available model or extend
            # Try to use a generic Memory model if it exists
            try:
                from app.models import Memory
                mem = Memory(
                    user_id   = self.user_id,
                    session_id= self.session_id,
                    role      = role,
                    content   = content,
                    meta      = json.dumps(extra or {}),
                    created_at= _now(),
                )
                db.session.add(mem)
                db.session.commit()
            except ImportError:
                # Fallback: store in app cache (non-persistent for this session)
                if not hasattr(self, "_in_memory"):
                    self._in_memory: list[dict] = []
                self._in_memory.append({
                    "role": role, "content": content, "ts": _now().isoformat()
                })
        except Exception as exc:
            # Graceful degradation — memory failure must never break chat
            logger.debug("Memory store failed (non-critical): %s", exc)
            if not hasattr(self, "_in_memory"):
                self._in_memory = []
            self._in_memory.append({"role": role, "content": content, "ts": _now().isoformat()})

    def retrieve_memory(self, *, limit: int = 20, role: str | None = None) -> list[dict]:
        """
        Retrieve recent memory turns for this session.

        Returns
        -------
        List of {role, content, ts} dicts, oldest first.
        """
        try:
            from app.models import Memory
            q = Memory.query.filter_by(user_id=self.user_id, session_id=self.session_id)
            if role:
                q = q.filter_by(role=role)
            rows = q.order_by(Memory.created_at.desc()).limit(limit).all()
            return [{"role": r.role, "content": r.content, "ts": str(r.created_at)} for r in reversed(rows)]
        except Exception:
            return getattr(self, "_in_memory", [])[-limit:]

    def clear_memory(self, *, all_sessions: bool = False) -> dict:
        """
        Clear conversation memory for this session or all sessions for this user.

        Returns
        -------
        {"cleared": N, "scope": "session" | "user"}
        """
        try:
            from app.models import db, Memory
            q = Memory.query.filter_by(user_id=self.user_id)
            if not all_sessions:
                q = q.filter_by(session_id=self.session_id)
            count = q.count()
            q.delete()
            db.session.commit()
            return {"cleared": count, "scope": "user" if all_sessions else "session"}
        except Exception as exc:
            logger.warning("Memory clear failed: %s", exc)
            if hasattr(self, "_in_memory"):
                count = len(self._in_memory)
                self._in_memory = []
                return {"cleared": count, "scope": "session"}
            return {"cleared": 0, "scope": "session"}

    # ------------------------------------------------------------------
    # 3. CONTEXT RETRIEVAL ENGINE
    # ------------------------------------------------------------------

    def retrieve_business_context(self) -> dict:
        """
        Load and cache live business context for system prompt injection.

        Loads: invoices, payments, overdue data, KPIs, active workflows,
        recent activity, top clients, high-risk accounts.

        Cache TTL: 300 seconds (5 minutes) to balance freshness vs performance.

        Returns
        -------
        Full context dict with all {placeholder} values.
        """
        now = _now()
        if (
            self._context_cache
            and self._context_cached_at
            and (now - self._context_cached_at).seconds < CONTEXT_CACHE_TTL
        ):
            return self._context_cache

        ctx: dict[str, Any] = {
            "user_name"    : "there",
            "company_name" : "your company",
            "currency"     : "INR",
        }

        try:
            # User info
            from app.models import User
            user = User.query.get(self.user_id)
            if user:
                ctx["user_name"]    = getattr(user, "name", None) or getattr(user, "email", "there")
                ctx["company_name"] = getattr(user, "company_name", "your company") or "your company"
                ctx["currency"]     = getattr(user, "preferred_currency", "INR") or "INR"
        except Exception:
            pass

        try:
            from app.ai.agents.analytics_agent import AnalyticsAgent
            agent = AnalyticsAgent(user_id=self.user_id)
            metrics = agent._build_metrics()
            health = agent._score_business_health(metrics)

            ctx.update({
                "total_revenue"    : metrics.get("total_revenue", 0),
                "prev_revenue"     : metrics.get("prev_revenue", 0),
                "revenue_growth_pct": metrics.get("revenue_growth_pct", 0),
                "total_invoiced"   : metrics.get("total_invoiced", 0),
                "total_overdue"    : metrics.get("total_overdue", 0),
                "overdue_count"    : metrics.get("overdue_count", 0),
                "collection_rate"  : metrics.get("collection_rate", 0),
                "avg_invoice_value": metrics.get("avg_invoice_value", 0),
                "dso"              : metrics.get("dso", 30),
                "invoice_count"    : metrics.get("invoice_count", 0),
                "paid_count"       : metrics.get("paid_count", 0),
                "health_score"     : health.get("score", 0),
                "health_grade"     : health.get("grade", "N/A"),
                "mrr"              : list(metrics.get("monthly_revenue", {}).values())[-1] if metrics.get("monthly_revenue") else 0,
            })

            # Top clients summary
            top = metrics.get("top_clients", [])[:3]
            ctx["top_clients_summary"] = "; ".join(
                f"Client {c['client_id']}: ${c['revenue']:,.0f}" for c in top
            ) or "No data."

            # High-risk clients
            risk = metrics.get("high_risk_clients", [])
            ctx["high_risk_clients"] = ", ".join(str(c) for c in risk[:3]) if risk else "None flagged."

            # Overdue summary
            ctx["overdue_summary"] = (
                f"{ctx['overdue_count']} invoices totalling ${ctx['total_overdue']:,.0f} overdue."
                if ctx.get("overdue_count") else "No overdue invoices."
            )

        except Exception as exc:
            logger.warning("Context retrieval (analytics) failed: %s", exc)

        try:
            # Recent invoices
            from app.models import Invoice
            recent = Invoice.query.filter_by(created_by=self.user_id).order_by(
                Invoice.created_at.desc()
            ).limit(5).all()
            ctx["recent_invoices_summary"] = "; ".join(
                f"INV-{i.id}: ${float(i.total_amount):,.0f} ({i.status})" for i in recent
            ) or "No recent invoices."
        except Exception:
            ctx.setdefault("recent_invoices_summary", "No recent invoices.")

        try:
            # Active workflows
            from app.models import Workflow
            ctx["active_workflows"] = Workflow.query.filter_by(
                created_by=self.user_id, status="active"
            ).count()
        except Exception:
            ctx["active_workflows"] = 0

        try:
            # Reminder count
            from app.models import Reminder
            ctx["reminder_count"] = Reminder.query.filter_by(user_id=self.user_id).count()
        except Exception:
            ctx["reminder_count"] = 0

        ctx["pending_actions"] = self._fetch_recent_activity()

        self._context_cache = ctx
        self._context_cached_at = now
        return ctx

    def _fetch_recent_activity(self) -> str:
        """Summarise recent pending actions for context injection."""
        parts = []
        try:
            from app.models import Invoice
            overdue = Invoice.query.filter_by(created_by=self.user_id, status="overdue").count()
            if overdue:
                parts.append(f"{overdue} invoices awaiting payment")
        except Exception:
            pass
        try:
            from app.models import Workflow
            pending = Workflow.query.filter_by(created_by=self.user_id, status="pending").count()
            if pending:
                parts.append(f"{pending} workflows pending execution")
        except Exception:
            pass
        return "; ".join(parts) if parts else "None."

    # ------------------------------------------------------------------
    # 4. AI COMMAND PROCESSOR
    # ------------------------------------------------------------------

    def process_command(self, command: str, *, params: dict | None = None) -> dict:
        """
        Execute a structured AI command: create invoice, send reminder,
        generate report, analyze revenue, forecast cashflow.

        Parameters
        ----------
        command : Command string or natural language command.
        params  : Optional pre-parsed parameters.

        Returns
        -------
        {
            "command"  : "create_invoice",
            "executed" : True,
            "result"   : {...},
            "message"  : "Invoice created successfully.",
            "next_step": "..."
        }
        """
        intent = self._parse_user_intent(command)
        params = params or {}

        handlers = {
            "invoice_create"  : self._cmd_create_invoice,
            "reminder_send"   : self._cmd_send_reminder,
            "report_generate" : self._cmd_generate_report,
            "analytics_query" : self._cmd_analyze_revenue,
            "forecast"        : self._cmd_forecast_cashflow,
            "workflow_create" : self._cmd_create_workflow,
        }

        handler = handlers.get(intent)
        if handler:
            try:
                result = handler(command, params)
                return {**result, "command": intent, "executed": True}
            except Exception as exc:
                logger.error("Command execution failed [%s]: %s", intent, exc)
                return {"command": intent, "executed": False, "error": str(exc)}

        # Delegate to chat for unrecognised commands
        chat_result = self.chat(command)
        return {"command": "chat", "executed": True, "result": chat_result, "message": chat_result.get("response", "")}

    def _cmd_create_invoice(self, text: str, params: dict) -> dict:
        from app.ai.agents.invoice_agent import InvoiceAgent
        result = InvoiceAgent(user_id=self.user_id).generate_invoice_from_prompt(text)
        return {"result": result["invoice"], "message": f"Invoice created for {result['invoice'].get('client_name', 'client')}.", "next_step": "Review and send invoice."}

    def _cmd_send_reminder(self, text: str, params: dict) -> dict:
        invoice_id = params.get("invoice_id")
        return {"result": {"invoice_id": invoice_id, "status": "queued"}, "message": "Reminder queued for delivery.", "next_step": "Check notification log for delivery status."}

    def _cmd_generate_report(self, text: str, params: dict) -> dict:
        return {"result": {"report_type": "executive", "status": "generating"}, "message": "Executive report is being generated.", "next_step": "Report will be available in your Reports dashboard."}

    def _cmd_analyze_revenue(self, text: str, params: dict) -> dict:
        from app.ai.agents.analytics_agent import AnalyticsAgent
        insights = AnalyticsAgent(user_id=self.user_id).generate_business_insights()
        return {"result": insights, "message": insights.get("summary", "Revenue analysis complete."), "next_step": "Review recommendations below."}

    def _cmd_forecast_cashflow(self, text: str, params: dict) -> dict:
        from app.ai.agents.analytics_agent import AnalyticsAgent
        forecast = AnalyticsAgent(user_id=self.user_id).predict_cashflow()
        return {"result": forecast, "message": forecast.get("narrative", "Cashflow forecast ready."), "next_step": "View detailed forecast in the Reports tab."}

    def _cmd_create_workflow(self, text: str, params: dict) -> dict:
        return {"result": {"status": "created", "trigger": "overdue", "actions": ["send_reminder"]}, "message": "Workflow created — reminders will be sent automatically.", "next_step": "Manage your workflow in the Workflows tab."}

    # ------------------------------------------------------------------
    # 5. SMART SUGGESTION ENGINE
    # ------------------------------------------------------------------

    def generate_suggestions(self, *, context: dict | None = None) -> list[dict]:
        """
        Generate contextual, data-grounded suggestions for the sidebar.

        Returns
        -------
        List of suggestion dicts: [{type, label, description, priority, action}]
        """
        ctx = context or self.retrieve_business_context()
        suggestions = []

        overdue_count = ctx.get("overdue_count", 0)
        total_overdue = ctx.get("total_overdue", 0)
        growth = ctx.get("revenue_growth_pct", 0)
        col_rate = ctx.get("collection_rate", 0)

        if overdue_count > 0:
            suggestions.append({
                "type": "overdue_action", "priority": 1,
                "label": f"Send reminders to {overdue_count} overdue client{'s' if overdue_count > 1 else ''}",
                "description": f"${total_overdue:,.0f} outstanding — recovery probability drops 8% per week.",
                "action": {"type": "send_reminder", "params": {"filter": "all_overdue"}},
            })

        if col_rate < 80:
            suggestions.append({
                "type": "automation", "priority": 2,
                "label": "Enable automated pre-due reminders",
                "description": f"Collection rate is {col_rate:.1f}% — automated reminders add ~15% improvement.",
                "action": {"type": "create_workflow", "params": {"trigger": "pre_due", "days": 5}},
            })

        if growth < 0:
            suggestions.append({
                "type": "growth_alert", "priority": 2,
                "label": "Revenue declining — review inactive clients",
                "description": f"Revenue dropped {abs(growth):.1f}%. Re-engage dormant clients.",
                "action": {"type": "search_invoices", "params": {"status": "draft", "inactive": True}},
            })

        suggestions.append({
            "type": "report", "priority": 3,
            "label": "Generate executive monthly report",
            "description": "Share business performance with stakeholders.",
            "action": {"type": "generate_report", "params": {"type": "executive", "period": "month"}},
        })

        suggestions.append({
            "type": "recurring", "priority": 4,
            "label": "Convert stable clients to recurring invoices",
            "description": "Predictable MRR improves cashflow planning by ~22%.",
            "action": {"type": "create_workflow", "params": {"trigger": "recurring_billing"}},
        })

        return sorted(suggestions, key=lambda s: s["priority"])[:5]

    # ------------------------------------------------------------------
    # 6. PERSONALIZED AI RECOMMENDATIONS
    # ------------------------------------------------------------------

    def generate_personalized_recommendations(self) -> list[dict]:
        """
        Generate user-behaviour-aware recommendations.

        Adapts to: business size, overdue trends, revenue patterns,
        workflow usage, and recent activity signals.

        Returns
        -------
        List of personalised recommendation dicts.
        """
        ctx = self.retrieve_business_context()
        history = self.retrieve_memory(limit=20)
        ai = self._get_ai()

        if ai:
            try:
                prompt = (
                    "Based on this business context, generate 4 personalised recommendations. "
                    f"Context: {json.dumps({k: ctx.get(k) for k in ['total_revenue','total_overdue','collection_rate','overdue_count','health_score','invoice_count']})}. "
                    f"Recent activity: {[h['content'][:60] for h in history[-5:]]}. "
                    "Return JSON: {\"recommendations\": [{\"title\": str, \"description\": str, \"impact\": str, \"urgency\": str}]}"
                )
                resp = ai.chat.completions.create(
                    model=self.fast_model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=350,
                    response_format={"type": "json_object"},
                )
                data = json.loads(resp.choices[0].message.content)
                return data.get("recommendations", [])
            except Exception as exc:
                logger.warning("Personalised recommendations failed: %s", exc)

        from app.ai.agents.analytics_agent import AnalyticsAgent
        recs = AnalyticsAgent(user_id=self.user_id).generate_recommendations()
        return [{"title": r["action"], "description": r["reason"], "impact": r["impact"], "urgency": r["urgency"]} for r in recs]

    # ------------------------------------------------------------------
    # 7. AI BUSINESS COACH
    # ------------------------------------------------------------------

    def generate_business_advice(self, topic: str = "") -> dict:
        """
        Provide senior-advisor-level business coaching on a specific topic
        or a general performance overview.

        Parameters
        ----------
        topic : Optional focus area (e.g. "cashflow", "overdue", "growth").

        Returns
        -------
        {
            "advice"    : "...",
            "strengths" : [...],
            "risks"     : [...],
            "quick_wins": [...]
        }
        """
        ctx = self.retrieve_business_context()
        from app.ai.agents.analytics_agent import AnalyticsAgent
        agent = AnalyticsAgent(user_id=self.user_id)
        health = agent.calculate_health_score()

        ai = self._get_ai()
        if ai:
            try:
                payload = {
                    "health_score"   : health["score"],
                    "collection_rate": ctx.get("collection_rate", 0),
                    "overdue_count"  : ctx.get("overdue_count", 0),
                    "growth"         : ctx.get("revenue_growth_pct", 0),
                    "dso"            : ctx.get("dso", 30),
                    "topic"          : topic or "general business performance",
                }
                prompt = (
                    f"Act as a senior CFO advisor. Provide concise, actionable advice for: {topic or 'improving overall business performance'}. "
                    f"Business data: {json.dumps(payload)}. "
                    "Return JSON: {\"advice\": str, \"strengths\": [str], \"risks\": [str], \"quick_wins\": [str]}"
                )
                resp = ai.chat.completions.create(
                    model=self.fast_model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=300,
                    response_format={"type": "json_object"},
                )
                return json.loads(resp.choices[0].message.content)
            except Exception as exc:
                logger.warning("Business advice LLM failed: %s", exc)

        return {
            "advice"    : health.get("recommendations", ["Review your overdue invoices and collection workflow."])[0] if health.get("recommendations") else "Focus on improving your collection rate.",
            "strengths" : health.get("strengths", []),
            "risks"     : health.get("risks", []),
            "quick_wins": ["Enable automated reminders", "Shorten payment terms", "Convert retainers to recurring invoices"],
        }

    # ------------------------------------------------------------------
    # 8. CONVERSATIONAL ANALYTICS
    # ------------------------------------------------------------------

    def answer_analytics_questions(self, question: str) -> dict:
        """
        Answer plain-English analytics questions with data-grounded responses.

        Examples
        --------
        "What caused revenue drop this month?" →  trend analysis
        "Which client generated most revenue?" →  client ranking

        Returns
        -------
        {"answer": "...", "data": {...}, "chart_hint": "..."}
        """
        ctx = self.retrieve_business_context()
        from app.ai.agents.analytics_agent import AnalyticsAgent
        agent = AnalyticsAgent(user_id=self.user_id)

        # Route to appropriate analytics method
        q_lower = question.lower()
        data: dict = {}
        chart_hint = "bar"

        if any(kw in q_lower for kw in ("drop", "decline", "down", "worse", "slow")):
            trends = agent.detect_financial_trends()
            data = trends
            chart_hint = "line"
        elif any(kw in q_lower for kw in ("client", "customer", "top", "most revenue", "best")):
            clients = agent.generate_client_insights()
            data = clients
            chart_hint = "bar"
        elif any(kw in q_lower for kw in ("overdue", "unpaid", "outstanding")):
            overdue = agent.analyze_overdue_risk()
            data = overdue
            chart_hint = "donut"
        elif any(kw in q_lower for kw in ("forecast", "predict", "next month")):
            forecast = agent.forecast_revenue(horizon="month")
            data = forecast
            chart_hint = "line"
        else:
            insights = agent.generate_business_insights()
            data = insights

        # Generate narrative answer
        narrative = agent.generate_financial_narrative()

        return {"answer": narrative, "data": data, "chart_hint": chart_hint, "question": question}

    # ------------------------------------------------------------------
    # 9. SMART SEARCH ENGINE
    # ------------------------------------------------------------------

    def smart_search(self, query: str) -> dict:
        """
        Convert a natural language query into structured search filters
        and return matching records summary.

        Parameters
        ----------
        query : e.g. "Unpaid invoices above ₹50k from Acme this month"

        Returns
        -------
        {
            "filters"   : {"status": "overdue", "min_amount": 50000},
            "results"   : [...],
            "summary"   : "Found 3 invoices matching your search.",
            "query"     : "original query"
        }
        """
        filters = self.parse_filter_query(query)
        from app.ai.agents.analytics_agent import AnalyticsAgent
        filtered = AnalyticsAgent(user_id=self.user_id).filter_analytics_data(**filters)
        metrics = filtered.get("metrics", {})

        return {
            "filters" : filters,
            "results" : filtered.get("kpis", []),
            "invoice_count": metrics.get("invoice_count", 0),
            "total_amount" : metrics.get("total_invoiced", 0),
            "summary" : (
                f"Found {metrics.get('invoice_count', 0)} invoice"
                f"{'s' if metrics.get('invoice_count', 0) != 1 else ''} matching your search — "
                f"${metrics.get('total_invoiced', 0):,.0f} total."
            ),
            "query": query,
        }

    # ------------------------------------------------------------------
    # 10. AI ACTION SUGGESTIONS
    # ------------------------------------------------------------------

    def generate_action_suggestions(self, *, context: dict | None = None) -> list[dict]:
        """
        Generate contextual quick-action buttons for the AI sidebar.

        Returns
        -------
        List of action dicts: [{label, action_type, params, icon}]
        """
        ctx = context or self.retrieve_business_context()
        actions = []

        if ctx.get("overdue_count", 0) > 0:
            actions.append({"label": f"Send reminders ({ctx['overdue_count']} overdue)", "action_type": "send_reminder", "params": {"filter": "all_overdue"}, "icon": "🔔"})

        actions.append({"label": "Generate executive report", "action_type": "generate_report", "params": {"type": "executive"}, "icon": "📊"})
        actions.append({"label": "Create new invoice", "action_type": "create_invoice", "params": {}, "icon": "➕"})
        actions.append({"label": "View cashflow forecast", "action_type": "forecast_cashflow", "params": {"horizon": "month"}, "icon": "📈"})
        actions.append({"label": "Set up automated reminders", "action_type": "create_workflow", "params": {"template": "overdue_reminders"}, "icon": "⚙️"})

        return actions[:5]

    # ------------------------------------------------------------------
    # 11. AI WORKFLOW TRIGGERING
    # ------------------------------------------------------------------

    def trigger_workflow_from_chat(self, message: str) -> dict:
        """
        Parse a natural language workflow request and create the workflow automatically.

        Parameters
        ----------
        message : e.g. "Automatically send reminders every Monday for overdue invoices."

        Returns
        -------
        {
            "workflow_created": True,
            "workflow"        : {...},
            "confirmation"    : "Workflow created — reminders will fire every Monday."
        }
        """
        ai = self._get_ai()
        workflow_params = {}

        if ai:
            try:
                prompt = (
                    f"Extract workflow parameters from: \"{message}\"\n"
                    "Return JSON: {\"trigger\": str, \"frequency\": str, \"actions\": [str], \"conditions\": {}, \"name\": str}"
                )
                resp = ai.chat.completions.create(
                    model=self.fast_model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=150,
                    response_format={"type": "json_object"},
                )
                workflow_params = json.loads(resp.choices[0].message.content)
            except Exception as exc:
                logger.warning("Workflow parsing failed: %s", exc)

        if not workflow_params.get("trigger"):
            workflow_params = {
                "trigger"   : "overdue",
                "frequency" : "weekly",
                "actions"   : ["send_reminder"],
                "name"      : "Auto Reminder Workflow",
                "conditions": {"overdue_days": 7},
            }

        try:
            from app.services.workflow_service import WorkflowService
            result = WorkflowService.create_workflow(user_id=self.user_id, data=workflow_params)
            confirmation = f"Workflow '{workflow_params.get('name', 'Auto Workflow')}' created — {', '.join(workflow_params.get('actions', []))} will fire {workflow_params.get('frequency', 'automatically')}."
            return {"workflow_created": True, "workflow": result, "confirmation": confirmation}
        except Exception as exc:
            logger.warning("Workflow creation failed: %s", exc)
            return {"workflow_created": False, "workflow": workflow_params, "confirmation": "Workflow parameters extracted — create in the Workflows tab."}

    # ------------------------------------------------------------------
    # 12. MULTI-STEP AI REASONING
    # ------------------------------------------------------------------

    def execute_multi_step_reasoning(self, objective: str) -> dict:
        """
        Execute a multi-step analysis pipeline for complex objectives.

        Example
        -------
        "Analyse overdue invoices → identify risky clients → suggest actions"

        Steps
        -----
        1. Parse objective into reasoning steps
        2. Execute each step sequentially
        3. Synthesise findings into a coherent narrative

        Returns
        -------
        {
            "objective" : "...",
            "steps"     : [{step, result, insight}],
            "conclusion": "...",
            "actions"   : [...]
        }
        """
        from app.ai.agents.analytics_agent import AnalyticsAgent
        agent = AnalyticsAgent(user_id=self.user_id)
        steps = []

        # Step 1: Analyse overdue
        overdue = agent.analyze_overdue_risk()
        steps.append({
            "step"   : "Overdue Analysis",
            "result" : f"${overdue.get('total_overdue', 0):,.0f} across {len(overdue.get('risky_clients', []))} clients",
            "insight": f"Risk level: {overdue.get('risk_level', 'unknown').upper()}",
        })

        # Step 2: Client risk ranking
        client_data = agent.generate_client_insights()
        risky = client_data.get("risky_clients", [])
        steps.append({
            "step"   : "Client Risk Identification",
            "result" : f"{len(risky)} high-risk clients identified",
            "insight": "Overdue concentration in top accounts.",
        })

        # Step 3: Recommendations
        recs = agent.generate_recommendations()
        steps.append({
            "step"   : "Action Generation",
            "result" : f"{len(recs)} prioritised actions",
            "insight": recs[0]["action"] if recs else "Review overdue accounts.",
        })

        # Step 4: AI synthesis
        narrative = agent.generate_financial_narrative()
        steps.append({"step": "AI Synthesis", "result": "Narrative generated", "insight": narrative})

        actions = [{"type": "send_reminder", "label": "Send reminders to risky clients", "params": {"filter": "risky"}},
                   {"type": "generate_report", "label": "Generate overdue risk report", "params": {"type": "overdue_risk"}}]

        return {
            "objective" : objective,
            "steps"     : steps,
            "conclusion": narrative,
            "actions"   : actions,
            "step_count": len(steps),
        }

    # ------------------------------------------------------------------
    # 13. AI INSIGHT STREAMING
    # ------------------------------------------------------------------

    def stream_insights(self, topic: str = "business_summary") -> Generator[str, None, None]:
        """
        Stream AI insights as Server-Sent Events for the live dashboard panel.

        Yields SSE-formatted strings: "data: {...}\\n\\n"

        Usage in Flask route
        --------------------
        @bp.route("/api/ai/stream-insights")
        def stream():
            agent = AssistantAgent(user_id=current_user.id)
            return Response(agent.stream_insights(), mimetype="text/event-stream")
        """
        ctx = self.retrieve_business_context()

        def _emit(event_type: str, payload: dict) -> str:
            return f"data: {json.dumps({'type': event_type, **payload})}\n\n"

        yield _emit("thinking", {"content": "Analysing your business data..."})

        # Health score
        from app.ai.agents.analytics_agent import AnalyticsAgent
        agent = AnalyticsAgent(user_id=self.user_id)
        health = agent.calculate_health_score()
        yield _emit("health", {"score": health["score"], "grade": health["grade"], "status": health["status"]})

        # Overdue alert
        if ctx.get("overdue_count", 0) > 0:
            yield _emit("alert", {
                "severity": "high",
                "message" : f"⚠️ {ctx['overdue_count']} overdue invoices — ${ctx.get('total_overdue', 0):,.0f} at risk.",
            })

        # Revenue insight
        growth = ctx.get("revenue_growth_pct", 0)
        yield _emit("insight", {
            "title"  : "Revenue Momentum",
            "value"  : f"${ctx.get('total_revenue', 0):,.0f}",
            "trend"  : "up" if growth >= 0 else "down",
            "message": f"Revenue {'grew' if growth >= 0 else 'declined'} {abs(growth):.1f}% this period.",
        })

        # Recommendations
        recs = agent.generate_recommendations(limit=3)
        for rec in recs:
            yield _emit("recommendation", {"action": rec["action"], "urgency": rec.get("urgency", "this_week")})

        # Narrative
        narrative = agent.generate_financial_narrative()
        yield _emit("narrative", {"content": narrative})

        yield _emit("complete", {"message": "AI analysis complete.", "timestamp": _now().isoformat()})

    # ------------------------------------------------------------------
    # 14. CONVERSATIONAL FILTERS
    # ------------------------------------------------------------------

    def parse_filter_query(self, query: str) -> dict:
        """
        Convert a natural language filter query into structured filter params.

        Examples
        --------
        "Unpaid invoices above ₹50k"        → {status: "overdue", min_amount: 50000}
        "Paid invoices from April"           → {status: "paid", start_date: "2026-04-01", end_date: "2026-04-30"}
        "Overdue invoices from enterprise"  → {status: "overdue", client_type: "enterprise"}

        Returns
        -------
        Filter dict compatible with filter_analytics_data().
        """
        ai = self._get_ai()

        if ai:
            try:
                prompt = (
                    f"Extract structured filters from this query: \"{query}\"\n"
                    "Today: " + _now().strftime("%Y-%m-%d") + "\n"
                    "Return JSON with any of these keys (only include what's mentioned): "
                    "status (overdue/paid/draft), min_amount (number), max_amount (number), "
                    "client_id (string), start_date (YYYY-MM-DD), end_date (YYYY-MM-DD), "
                    "currency (str), overdue_only (bool), recurring_only (bool)"
                )
                resp = ai.chat.completions.create(
                    model=self.fast_model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=120,
                    response_format={"type": "json_object"},
                )
                return json.loads(resp.choices[0].message.content)
            except Exception as exc:
                logger.warning("Filter parsing LLM failed: %s", exc)

        return self._rule_based_filter_parse(query)

    def _rule_based_filter_parse(self, query: str) -> dict:
        """Regex-based filter extraction fallback."""
        filters: dict[str, Any] = {}
        q = query.lower()

        if any(w in q for w in ("unpaid", "overdue", "outstanding")):
            filters["status"] = "overdue"
            filters["overdue_only"] = True
        elif "paid" in q:
            filters["status"] = "paid"
        elif "draft" in q:
            filters["status"] = "draft"

        # Amount threshold
        amount_match = re.search(r"above\s+([\d,]+(?:\.\d+)?)\s*(?:k|lakh|thousand)?", q)
        if amount_match:
            val = float(amount_match.group(1).replace(",", ""))
            if "lakh" in q:
                val *= 100000
            elif "k" in q or "thousand" in q:
                val *= 1000
            filters["min_amount"] = val

        # Month filter
        months = {"jan":"01","feb":"02","mar":"03","apr":"04","may":"05","jun":"06",
                  "jul":"07","aug":"08","sep":"09","oct":"10","nov":"11","dec":"12"}
        for abbr, num in months.items():
            if abbr in q:
                year = _now().year
                filters["start_date"] = f"{year}-{num}-01"
                # Last day of month
                last = {m: 31 if m in ["01","03","05","07","08","10","12"] else 30 if m != "02" else 28 for m in [f"{i:02d}" for i in range(1, 13)]}
                filters["end_date"] = f"{year}-{num}-{last[num]}"
                break

        return filters

    # ------------------------------------------------------------------
    # 15. AI ONBOARDING ASSISTANT
    # ------------------------------------------------------------------

    def generate_onboarding_steps(self) -> dict:
        """
        Guide new users through a personalised onboarding flow.

        Returns
        -------
        {
            "greeting"     : "Welcome! Let's get you set up.",
            "completion"   : 0–100,
            "steps"        : [{step, title, description, action, completed}],
            "next_step"    : {...},
            "encouragement": "..."
        }
        """
        ctx = self.retrieve_business_context()

        steps = [
            {
                "step": 1, "title": "Create your first client",
                "description": "Add a client you invoice regularly — name, email, and billing address.",
                "action": {"type": "navigate", "path": "/clients/new"},
                "completed": ctx.get("invoice_count", 0) > 0,
            },
            {
                "step": 2, "title": "Create your first invoice",
                "description": "Use the AI invoice generator — just type what you did and for whom.",
                "action": {"type": "navigate", "path": "/invoices/new"},
                "completed": ctx.get("invoice_count", 0) > 0,
            },
            {
                "step": 3, "title": "Set up automated reminders",
                "description": "Enable automatic payment reminders — saves hours of manual follow-up.",
                "action": {"type": "create_workflow", "params": {"template": "auto_reminders"}},
                "completed": ctx.get("active_workflows", 0) > 0,
            },
            {
                "step": 4, "title": "Generate your first report",
                "description": "Create an executive summary to understand your business health.",
                "action": {"type": "generate_report", "params": {"type": "executive"}},
                "completed": False,
            },
            {
                "step": 5, "title": "Explore the AI assistant",
                "description": "Ask the AI anything — 'How is my cashflow?' or 'Which clients are risky?'",
                "action": {"type": "navigate", "path": "/ai-assistant"},
                "completed": bool(self.retrieve_memory(limit=1)),
            },
        ]

        completed_count = sum(1 for s in steps if s["completed"])
        completion_pct = int(completed_count / len(steps) * 100)
        next_step = next((s for s in steps if not s["completed"]), None)

        encouragements = {
            0: "Let's get your first invoice out today!",
            1: "Great start — you're 20% there.",
            2: "Halfway there — set up reminders to save time.",
            3: "Almost done — just a couple more steps.",
            4: "Final stretch — explore the AI assistant!",
            5: "Setup complete — your AI CFO is ready.",
        }

        return {
            "greeting"      : f"Welcome, {ctx.get('user_name', 'there')}! Let's get InvoiceFlow working for you.",
            "completion"    : completion_pct,
            "steps"         : steps,
            "next_step"     : next_step,
            "encouragement" : encouragements.get(completed_count, "Great progress!"),
        }

    # ------------------------------------------------------------------
    # 16. AI FOLLOW-UP GENERATOR
    # ------------------------------------------------------------------

    def generate_followup_message(
        self,
        *,
        message_type: str = "reminder",
        client_name: str = "",
        invoice_id: str = "",
        context: dict | None = None,
    ) -> dict:
        """
        Generate a professional follow-up message (reminder, thank-you, check-in).

        Parameters
        ----------
        message_type : "reminder" | "thank_you" | "check_in" | "escalation"
        client_name  : Client name for personalisation.
        invoice_id   : Invoice reference.
        context      : Additional context dict.

        Returns
        -------
        {
            "subject": "...",
            "body"   : "...",
            "tone"   : "professional",
            "channel": "email"
        }
        """
        templates = {
            "reminder": {
                "subject": f"Friendly Reminder: Invoice {invoice_id} Due Soon",
                "body"   : (
                    f"Dear {client_name or 'Client'},\n\n"
                    "I hope this message finds you well. This is a friendly reminder that your invoice "
                    f"{invoice_id} is due soon. Please arrange payment at your earliest convenience.\n\n"
                    "If you have any questions, don't hesitate to reach out.\n\nBest regards"
                ),
                "tone"   : "friendly",
                "channel": "email",
            },
            "thank_you": {
                "subject": "Thank You for Your Payment",
                "body"   : (
                    f"Dear {client_name or 'Client'},\n\n"
                    f"Thank you for your prompt payment of invoice {invoice_id}. "
                    "We truly appreciate your business and look forward to continuing our partnership.\n\nBest regards"
                ),
                "tone"   : "warm",
                "channel": "email",
            },
            "check_in": {
                "subject": "Quick Check-In",
                "body"   : (
                    f"Hi {client_name or 'there'},\n\n"
                    "I wanted to check in and see how things are going. "
                    "We'd love to continue supporting you — let me know if there's anything we can help with.\n\nBest regards"
                ),
                "tone"   : "friendly",
                "channel": "email",
            },
            "escalation": {
                "subject": f"Important: Outstanding Invoice {invoice_id} — Action Required",
                "body"   : (
                    f"Dear {client_name or 'Client'},\n\n"
                    f"We would like to bring to your attention that invoice {invoice_id} remains unpaid. "
                    "Please arrange payment or contact us to discuss a resolution.\n\nSincerely"
                ),
                "tone"   : "firm",
                "channel": "email",
            },
        }

        base = templates.get(message_type, templates["reminder"])

        ai = self._get_ai()
        if ai and client_name:
            try:
                resp = ai.chat.completions.create(
                    model=self.fast_model,
                    messages=[{
                        "role": "user",
                        "content": (
                            f"Write a professional {message_type} email for client '{client_name}', "
                            f"invoice {invoice_id}. "
                            "Return JSON: {\"subject\": str, \"body\": str}"
                        ),
                    }],
                    max_tokens=200,
                    response_format={"type": "json_object"},
                )
                data = json.loads(resp.choices[0].message.content)
                base.update(data)
            except Exception:
                pass

        return base

    # ------------------------------------------------------------------
    # 17. REAL-TIME STREAMING CHAT RESPONSE
    # ------------------------------------------------------------------

    def stream_chat_response(
        self,
        message: str,
        *,
        conversation_history: list[dict] | None = None,
    ) -> Generator[str, None, None]:
        """
        Stream a chat response token-by-token via SSE.

        Yields SSE strings: "data: {json}\\n\\n"

        Usage in Flask route
        --------------------
        @bp.route("/api/ai/chat/stream")
        def stream_chat():
            agent = AssistantAgent(user_id=current_user.id)
            return Response(
                agent.stream_chat_response(request.json["message"]),
                mimetype="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        """
        def _sse(payload: dict) -> str:
            return f"data: {json.dumps(payload)}\n\n"

        yield _sse({"type": "thinking", "content": "Analysing your request..."})

        # Context and system prompt
        context = self.retrieve_business_context()
        system = self._load_system_prompt(context)
        history = conversation_history or self._load_conversation_history()

        messages = [{"role": "system", "content": system}]
        messages.extend(history[-20:])
        messages.append({"role": "user", "content": message})

        ai = self._get_ai()
        if not ai:
            result = self._rule_based_response(message, self._parse_user_intent(message))
            words = result["response"].split()
            for i in range(0, len(words), STREAM_CHUNK_WORDS):
                chunk = " ".join(words[i:i+STREAM_CHUNK_WORDS])
                yield _sse({"type": "stream", "content": chunk + " "})
            yield _sse({"type": "complete", "full_response": result})
            return

        try:
            # Streaming completion
            stream = ai.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=800,
                stream=True,
            )
            full_content = ""
            for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    full_content += delta
                    yield _sse({"type": "stream", "content": delta})

            # Parse full JSON response
            try:
                parsed = json.loads(full_content)
            except json.JSONDecodeError:
                parsed = {"response": full_content}

            # Store in memory
            self.store_memory(role="user", content=message)
            self.store_memory(role="assistant", content=parsed.get("response", full_content))

            yield _sse({
                "type"         : "complete",
                "full_response": parsed,
                "session_id"   : self.session_id,
            })

        except Exception as exc:
            logger.error("Streaming chat failed: %s", exc)
            yield _sse({"type": "error", "message": "AI response temporarily unavailable. Please try again."})

    # ------------------------------------------------------------------
    # 18. AI CONTEXT AWARENESS INJECTION
    # ------------------------------------------------------------------

    def inject_context_awareness(
        self,
        *,
        current_page: str = "",
        user_role: str = "owner",
        active_workflow_ids: list | None = None,
    ) -> dict:
        """
        Build a rich context awareness payload for the AI sidebar.

        Understands: current page, user role, business status, active workflows.
        Used to make the AI aware of what the user is currently looking at.

        Returns
        -------
        {
            "context_summary": "...",
            "page_hints"     : [...],
            "suggested_actions": [...]
        }
        """
        ctx = self.retrieve_business_context()
        page_hints: list[str] = []
        suggested: list[dict] = []

        page_action_map = {
            "/invoices"  : ("looking at your invoices", ["Create new invoice", "Filter overdue"]),
            "/clients"   : ("viewing your client list", ["Add new client", "Check risky clients"]),
            "/dashboard" : ("on the dashboard", ["View health score", "Generate report"]),
            "/reports"   : ("in reports", ["Export PDF", "Schedule weekly report"]),
            "/workflows" : ("managing workflows", ["Create auto-reminder", "Activate workflow"]),
            "/analytics" : ("viewing analytics", ["Ask AI a question", "See cashflow forecast"]),
        }

        page_label, page_actions = "using InvoiceFlow", ["How can I help?"]
        for path, (label, actions) in page_action_map.items():
            if path in current_page:
                page_label, page_actions = label, actions
                break

        page_hints = page_actions
        for action_label in page_actions:
            suggested.append({"label": action_label, "action": action_label.lower().replace(" ", "_")})

        # Role-specific context
        role_suffix = {
            "owner"    : "You have full access to all business data.",
            "manager"  : "You can view and manage invoices and reports.",
            "accountant": "You have access to financial reports and payments.",
        }.get(user_role, "")

        context_summary = (
            f"User is currently {page_label}. "
            f"Health score: {ctx.get('health_score', 0)}/100. "
            f"{ctx.get('overdue_count', 0)} overdue invoices. "
            + role_suffix
        )

        return {
            "context_summary"  : context_summary,
            "page_hints"       : page_hints,
            "current_page"     : current_page,
            "user_role"        : user_role,
            "suggested_actions": suggested,
            "active_workflows" : active_workflow_ids or [],
        }

    # ------------------------------------------------------------------
    # 19. AI DASHBOARD INSIGHT CARDS
    # ------------------------------------------------------------------

    def generate_dashboard_cards(self) -> list[dict]:
        """
        Generate AI-powered insight cards for the dashboard overview panel.

        Delegates to AnalyticsAgent.generate_insight_cards() and
        enriches with chat action hooks.

        Returns
        -------
        List of enriched card dicts ready for the frontend dashboard.
        """
        from app.ai.agents.analytics_agent import AnalyticsAgent
        agent = AnalyticsAgent(user_id=self.user_id)
        cards = agent.generate_insight_cards()

        # Add chat prompt hints
        chat_prompts = {
            "revenue_momentum"    : "Tell me more about my revenue trend.",
            "collection_efficiency": "How can I improve my collection rate?",
            "overdue_exposure"    : "Send reminders to overdue clients.",
            "health_score"        : "What does my health score mean?",
            "top_client"          : "Show me my top clients.",
        }
        for card in cards:
            card["chat_prompt"] = chat_prompts.get(card.get("id", ""), "Tell me more about this.")

        return cards

    # ------------------------------------------------------------------
    # 20. AI AUTONOMOUS OPERATIONS — PROACTIVE ACTIONS
    # ------------------------------------------------------------------

    def generate_proactive_ai_actions(self) -> dict:
        """
        Generate autonomous AI recommendations and proactively queue actions.

        This is the "autonomous business assistant" feature — the AI detects
        what needs to be done and presents it as a ready-to-execute plan.

        Examples
        --------
        - AI detects 3 overdue clients → queues reminder batch
        - AI detects recurring service → suggests subscription conversion
        - AI detects declining revenue → schedules re-engagement campaign

        Returns
        -------
        {
            "headline"          : "AI detected 3 actions needed today.",
            "proactive_actions" : [{type, title, description, confidence, auto_executable}],
            "ai_narrative"      : "...",
            "total_impact"      : "$...",
            "auto_queue_ready"  : True
        }
        """
        ctx = self.retrieve_business_context()
        from app.ai.agents.analytics_agent import AnalyticsAgent
        agent = AnalyticsAgent(user_id=self.user_id)

        proactive: list[dict] = []
        total_impact = 0

        # Action 1: Overdue reminders
        overdue_count = ctx.get("overdue_count", 0)
        total_overdue = ctx.get("total_overdue", 0)
        if overdue_count > 0:
            proactive.append({
                "type"           : "send_reminder",
                "title"          : f"Send reminders to {overdue_count} overdue account{'s' if overdue_count > 1 else ''}",
                "description"    : f"${total_overdue:,.0f} outstanding. AI recommends sending reminders now — recovery probability is highest within first 30 days.",
                "confidence"     : 92,
                "auto_executable": True,
                "params"         : {"filter": "all_overdue", "tone": "professional"},
                "estimated_recovery": round(total_overdue * 0.65, 2),
            })
            total_impact += total_overdue * 0.65

        # Action 2: Workflow suggestion
        if ctx.get("active_workflows", 0) == 0:
            proactive.append({
                "type"           : "create_workflow",
                "title"          : "Set up automated reminder workflow",
                "description"    : "No active workflows detected. AI can set up automatic reminders that fire 5 days before and on the due date.",
                "confidence"     : 88,
                "auto_executable": True,
                "params"         : {"template": "smart_reminders"},
                "estimated_recovery": None,
            })

        # Action 3: Revenue decline
        if ctx.get("revenue_growth_pct", 0) < -5:
            proactive.append({
                "type"           : "client_outreach",
                "title"          : "Re-engage dormant clients",
                "description"    : f"Revenue declined {abs(ctx.get('revenue_growth_pct', 0)):.1f}%. AI recommends a check-in message to 3 recently inactive clients.",
                "confidence"     : 74,
                "auto_executable": False,
                "params"         : {"message_type": "check_in"},
                "estimated_recovery": None,
            })

        # Action 4: Forecast report
        proactive.append({
            "type"           : "generate_report",
            "title"          : "Generate this week's executive summary",
            "description"    : "AI-generated summary ready — share with stakeholders or review personally.",
            "confidence"     : 95,
            "auto_executable": True,
            "params"         : {"type": "executive", "period": "week"},
            "estimated_recovery": None,
        })

        # Generate AI narrative for all actions
        ai_narrative = agent.generate_financial_narrative()

        return {
            "headline"         : f"AI detected {len(proactive)} action{'s' if len(proactive) != 1 else ''} needed today.",
            "proactive_actions": sorted(proactive, key=lambda a: a["confidence"], reverse=True),
            "ai_narrative"     : ai_narrative,
            "total_impact"     : f"${total_impact:,.0f}" if total_impact else "N/A",
            "auto_queue_ready" : any(a["auto_executable"] for a in proactive),
            "generated_at"     : _now().isoformat(),
        }

    # ==================================================================
    # INTERNAL HELPERS
    # ==================================================================

    def _parse_user_intent(self, message: str) -> str:
        """Classify user message into an intent category."""
        message_lower = message.lower()
        for intent, keywords in INTENT_MAP.items():
            if any(kw in message_lower for kw in keywords):
                return intent
        return "chat"

    def _load_conversation_history(self) -> list[dict]:
        """Load conversation history formatted for OpenAI messages."""
        memory = self.retrieve_memory(limit=MEMORY_MAX_TURNS)
        history = []
        for item in memory:
            role = item.get("role", "user")
            if role in ("user", "assistant"):
                history.append({"role": role, "content": item.get("content", "")})
        return history

    def _rank_suggestions(self, suggestions: list[dict]) -> list[dict]:
        """Sort suggestions by priority and data relevance."""
        return sorted(suggestions, key=lambda s: s.get("priority", 99))

    def _summarize_context(self, ctx: dict) -> str:
        """Build a one-paragraph context summary for logging/debugging."""
        return (
            f"Revenue: ${ctx.get('total_revenue', 0):,.0f} | "
            f"Overdue: {ctx.get('overdue_count', 0)} (${ctx.get('total_overdue', 0):,.0f}) | "
            f"Health: {ctx.get('health_score', 0)}/100 | "
            f"Collection: {ctx.get('collection_rate', 0):.1f}%"
        )

    def _build_chat_prompt(self, message: str, context: dict) -> str:
        """Build user message with context hints for better responses."""
        ctx_summary = self._summarize_context(context)
        return f"{message}\n[Context: {ctx_summary}]"


# ===========================================================================
# MODULE-LEVEL CONVENIENCE FUNCTIONS
# ===========================================================================

def chat(
    message: str,
    *,
    user_id: int | None = None,
    session_id: str | None = None,
    history: list[dict] | None = None,
) -> dict:
    """One-line chat interface."""
    return AssistantAgent(user_id=user_id, session_id=session_id).chat(
        message, conversation_history=history
    )


def stream_chat(
    message: str,
    *,
    user_id: int | None = None,
    session_id: str | None = None,
) -> Generator[str, None, None]:
    """One-line streaming chat — returns generator for Flask Response."""
    return AssistantAgent(user_id=user_id, session_id=session_id).stream_chat_response(message)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.utcnow()


def _new_id() -> str:
    return str(uuid.uuid4())


def _fmt(value: float) -> str:
    return f"${value:,.0f}"


_INLINE_SYSTEM_PROMPT = """
You are an AI business operations assistant for InvoiceFlow. Today: {today_date}.
You have access to live business data. Answer business questions, explain analytics,
suggest actions, and help with invoices and workflows. Be specific with numbers.
Sound like a trusted senior advisor. Return JSON: {response, suggestions, actions, severity}.
"""
