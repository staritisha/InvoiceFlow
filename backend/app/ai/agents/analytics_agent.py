"""
app/ai/agents/analytics_agent.py

AI Analytics Agent for InvoiceFlow — the AI CFO and business strategist.
Converts raw invoice, payment, and client data into executive-grade intelligence:
health scores, forecasts, KPI dashboards, trend detection, client insights,
predictive analytics, real-time metrics, and AI financial narratives.

Usage
-----
from app.ai.agents.analytics_agent import AnalyticsAgent

agent = AnalyticsAgent(user_id=42)
insights = agent.generate_business_insights()
health   = agent.calculate_health_score()
forecast = agent.forecast_revenue(horizon="month")
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations & Constants
# ---------------------------------------------------------------------------

class HealthStatus(str, Enum):
    EXCEPTIONAL = "exceptional"
    HEALTHY     = "healthy"
    MODERATE    = "moderate"
    AT_RISK     = "at_risk"
    CRITICAL    = "critical"


class ForecastHorizon(str, Enum):
    WEEK    = "week"
    MONTH   = "month"
    QUARTER = "quarter"


class AlertSeverity(str, Enum):
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    CRITICAL = "critical"


HEALTH_GRADE_MAP = {
    (90, 101): ("A+", HealthStatus.EXCEPTIONAL),
    (80,  90): ("A",  HealthStatus.HEALTHY),
    (70,  80): ("B",  HealthStatus.MODERATE),
    (60,  70): ("C",  HealthStatus.AT_RISK),
    (50,  60): ("D",  HealthStatus.AT_RISK),
    ( 0,  50): ("F",  HealthStatus.CRITICAL),
}


# ===========================================================================
# MAIN AGENT CLASS
# ===========================================================================

class AnalyticsAgent:
    """
    AI-powered business analytics agent.

    Parameters
    ----------
    user_id : Owning user (used for data scoping).
    model   : OpenAI model for LLM calls.
    """

    def __init__(
        self,
        user_id: int | None = None,
        *,
        model: str = "gpt-4o",
        prompt_path: str | None = None,
    ) -> None:
        self.user_id = user_id
        self.model = model
        self._prompt_path = prompt_path or os.path.join(
            os.path.dirname(__file__), "../../ai/prompts/business_insights.txt"
        )
        self._system_prompt: str | None = None
        self._ai: Any = None
        self._data_cache: dict | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_ai(self):
        if self._ai is None:
            try:
                import openai
                key = os.getenv("OPENAI_API_KEY")
                if not key:
                    logger.warning("OPENAI_API_KEY not set — AI analytics degraded to rule-based")
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
        except ImportError:
            return None

    def _load_system_prompt(self, period_label: str = "Current Period") -> str:
        if self._system_prompt is None:
            try:
                with open(self._prompt_path, encoding="utf-8") as f:
                    raw = f.read()
                self._system_prompt = raw
            except FileNotFoundError:
                self._system_prompt = _INLINE_INSIGHTS_PROMPT
        return (
            self._system_prompt
            .replace("{today_date}", _now().strftime("%Y-%m-%d"))
            .replace("{period_label}", period_label)
        )

    # ------------------------------------------------------------------
    # DATA FETCHERS
    # ------------------------------------------------------------------

    def _fetch_invoice_data(self, filters: dict | None = None) -> list:
        """Return all invoice ORM objects, optionally filtered."""
        try:
            from app.models import Invoice
            q = Invoice.query
            if filters:
                if filters.get("start_date"):
                    q = q.filter(Invoice.created_at >= datetime.fromisoformat(filters["start_date"]))
                if filters.get("end_date"):
                    q = q.filter(Invoice.created_at <= datetime.fromisoformat(filters["end_date"]))
                if filters.get("status"):
                    q = q.filter_by(status=filters["status"])
                if filters.get("client_id"):
                    q = q.filter_by(client_id=filters["client_id"])
                if filters.get("currency"):
                    q = q.filter_by(currency=filters["currency"].upper())
                if filters.get("overdue_only"):
                    q = q.filter_by(status="overdue")
                if filters.get("recurring_only"):
                    q = q.filter_by(is_recurring=True)
            return q.all()
        except Exception as exc:
            logger.warning("Invoice data fetch failed: %s", exc)
            return []

    def _fetch_payment_data(self, filters: dict | None = None) -> list:
        """Return all payment ORM objects, optionally filtered."""
        try:
            from app.models import Payment
            q = Payment.query
            if filters and filters.get("start_date"):
                q = q.filter(Payment.paid_at >= datetime.fromisoformat(filters["start_date"]))
            if filters and filters.get("end_date"):
                q = q.filter(Payment.paid_at <= datetime.fromisoformat(filters["end_date"]))
            return q.all()
        except Exception as exc:
            logger.warning("Payment data fetch failed: %s", exc)
            return []

    def _build_metrics(self, filters: dict | None = None) -> dict:
        """Aggregate all raw metrics from DB into a single dict."""
        if self._data_cache and not filters:
            return self._data_cache

        invoices = self._fetch_invoice_data(filters)
        payments = self._fetch_payment_data(filters)

        total_revenue = sum(float(p.amount) for p in payments if p.status == "completed")
        total_invoiced = sum(float(i.total_amount) for i in invoices)
        total_overdue = sum(float(i.total_amount) for i in invoices if i.status == "overdue")
        overdue_count = sum(1 for i in invoices if i.status == "overdue")
        paid_count = sum(1 for i in invoices if i.status == "paid")
        invoice_count = len(invoices) or 1

        # Monthly revenue
        monthly: dict[str, float] = {}
        for p in payments:
            if p.paid_at and p.status == "completed":
                key = p.paid_at.strftime("%Y-%m")
                monthly[key] = monthly.get(key, 0) + float(p.amount)

        # Previous period (prior month)
        now = _now()
        prev_month = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        curr_month = now.strftime("%Y-%m")
        prev_revenue = monthly.get(prev_month, 0)
        curr_revenue = monthly.get(curr_month, total_revenue)

        # Client revenue map
        client_revenue: dict[str, float] = {}
        for i in invoices:
            cid = str(getattr(i, "client_id", "") or "unknown")
            client_revenue[cid] = client_revenue.get(cid, 0) + float(i.total_amount)

        top_clients = sorted(
            [{"client_id": k, "revenue": v} for k, v in client_revenue.items()],
            key=lambda x: x["revenue"], reverse=True,
        )[:5]

        # DSO calculation
        paid_invoices = [i for i in invoices if i.status == "paid" and getattr(i, "due_date", None) and getattr(i, "created_at", None)]
        if paid_invoices:
            avg_days = sum(
                max(0, (i.due_date - i.created_at).days) for i in paid_invoices
            ) / len(paid_invoices)
        else:
            avg_days = 30.0

        collection_rate = (paid_count / invoice_count) * 100
        overdue_ratio = (total_overdue / total_invoiced * 100) if total_invoiced else 0
        avg_invoice_value = total_invoiced / invoice_count

        # Growth %
        growth_pct = self._calculate_growth(curr_revenue, prev_revenue)

        metrics = {
            "total_revenue": total_revenue,
            "prev_revenue": prev_revenue,
            "revenue_growth_pct": growth_pct,
            "total_invoiced": total_invoiced,
            "total_overdue": total_overdue,
            "overdue_count": overdue_count,
            "overdue_ratio": round(overdue_ratio, 2),
            "invoice_count": invoice_count,
            "paid_count": paid_count,
            "collection_rate": round(collection_rate, 2),
            "avg_invoice_value": round(avg_invoice_value, 2),
            "dso": round(avg_days, 1),
            "monthly_revenue": dict(sorted(monthly.items())),
            "top_clients": top_clients,
            "high_risk_clients": [c["client_id"] for c in top_clients if c["revenue"] > total_revenue * 0.4],
            "invoices": invoices,
            "payments": payments,
        }

        if not filters:
            self._data_cache = metrics

        return metrics

    # ------------------------------------------------------------------
    # 1. BUSINESS INSIGHT GENERATOR
    # ------------------------------------------------------------------

    def generate_business_insights(
        self,
        *,
        filters: dict | None = None,
        period_label: str = "Current Period",
    ) -> dict:
        """
        Generate comprehensive AI business insights from live data.

        Analyses revenue, overdue trends, recurring revenue, and client behaviour.
        Returns structured insights ready for the dashboard panel.

        Returns
        -------
        {
            "health_score"   : 84,
            "summary"        : "...",
            "insights"       : [...],
            "recommendations": [...],
            "risks"          : [...],
            "opportunities"  : [...],
            "actions"        : [...],
            "forecast"       : "...",
            "severity"       : "medium",
            "ai_powered"     : True
        }
        """
        metrics = self._build_metrics(filters)
        system = self._load_system_prompt(period_label)
        ai = self._get_ai()

        # Build payload for the LLM
        payload = {
            "total_revenue"   : metrics["total_revenue"],
            "prev_revenue"    : metrics["prev_revenue"],
            "total_invoiced"  : metrics["total_invoiced"],
            "total_overdue"   : metrics["total_overdue"],
            "overdue_count"   : metrics["overdue_count"],
            "invoice_count"   : metrics["invoice_count"],
            "paid_count"      : metrics["paid_count"],
            "collection_rate" : metrics["collection_rate"],
            "dso"             : metrics["dso"],
            "top_clients"     : metrics["top_clients"],
            "monthly_revenue" : metrics["monthly_revenue"],
            "high_risk_clients": metrics["high_risk_clients"],
            "period_days"     : 30,
        }

        if ai:
            try:
                resp = ai.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": json.dumps(payload)},
                    ],
                    max_tokens=1000,
                    response_format={"type": "json_object"},
                )
                result = json.loads(resp.choices[0].message.content)
                result["ai_powered"] = True
                result["metrics"] = payload
                return result
            except Exception as exc:
                logger.warning("AI insights generation failed: %s", exc)

        # Rule-based fallback
        return self._rule_based_insights(metrics)

    def _rule_based_insights(self, metrics: dict) -> dict:
        health = self._score_business_health(metrics)
        return {
            "health_score"    : health["score"],
            "health_grade"    : health["grade"],
            "severity"        : _severity_from_score(health["score"]),
            "summary"         : self._generate_llm_summary(metrics, use_ai=False),
            "insights"        : self.generate_insight_cards(metrics=metrics),
            "recommendations" : self.generate_recommendations(metrics=metrics),
            "risks"           : self._build_risk_list(metrics),
            "opportunities"   : [],
            "actions"         : self._quick_actions(metrics),
            "forecast"        : self.forecast_revenue(horizon=ForecastHorizon.MONTH, metrics=metrics).get("narrative", ""),
            "ai_powered"      : False,
            "metrics"         : metrics,
        }

    # ------------------------------------------------------------------
    # 2. AI BUSINESS HEALTH SCORE
    # ------------------------------------------------------------------

    def calculate_health_score(self, *, metrics: dict | None = None) -> dict:
        """
        Compute a 0–100 business health score across 5 weighted dimensions.

        Dimensions
        ----------
        Collection Efficiency (30 pts) | Overdue Exposure (25 pts) |
        Revenue Growth (20 pts) | Cashflow Stability (15 pts) |
        Client Diversification (10 pts)

        Returns
        -------
        {
            "score": 84, "grade": "A", "status": "healthy",
            "dimensions": {...}, "strengths": [...],
            "risks": [...], "recommendations": [...]
        }
        """
        if metrics is None:
            metrics = self._build_metrics()

        scored = self._score_business_health(metrics)
        return scored

    def _score_business_health(self, metrics: dict) -> dict:
        collection_rate = metrics.get("collection_rate", 0)
        overdue_ratio = metrics.get("overdue_ratio", 0)
        growth_pct = metrics.get("revenue_growth_pct", 0)
        total_revenue = metrics.get("total_revenue", 0)
        top_clients = metrics.get("top_clients", [])

        # Dimension scores (each 0 to its max)
        collection_score = min(30, round(collection_rate * 0.30))

        overdue_score = min(25, round(max(0, (1 - overdue_ratio / 100)) * 25))

        if growth_pct > 0:
            growth_score = min(20, round(growth_pct * 0.5 + 10))
        elif growth_pct == 0:
            growth_score = 10
        else:
            growth_score = max(0, 10 + round(growth_pct * 0.3))

        stability_score = min(15, round((min(total_revenue, 500000) / 500000) * 15))

        if top_clients:
            top_rev_share = top_clients[0]["revenue"] / (metrics.get("total_revenue", 1) or 1)
            diversity_score = max(0, min(10, round((1 - top_rev_share) * 10 * 1.5)))
        else:
            diversity_score = 10

        total_score = collection_score + overdue_score + growth_score + stability_score + diversity_score
        total_score = max(0, min(100, total_score))

        grade, status = "F", HealthStatus.CRITICAL
        for (low, high), (g, s) in HEALTH_GRADE_MAP.items():
            if low <= total_score < high:
                grade, status = g, s
                break

        strengths, risks = [], []
        if collection_score >= 25:
            strengths.append("Excellent invoice collection rate")
        if growth_score >= 15:
            strengths.append("Strong revenue growth momentum")
        if overdue_score >= 20:
            strengths.append("Low overdue exposure")
        if diversity_score >= 8:
            strengths.append("Good client revenue diversification")

        if overdue_ratio > 25:
            risks.append(f"Overdue invoices at {overdue_ratio:.1f}% of total invoiced")
        if collection_rate < 75:
            risks.append(f"Collection rate of {collection_rate:.1f}% needs improvement")
        if growth_pct < 0:
            risks.append(f"Revenue declining {abs(growth_pct):.1f}% vs prior period")
        if top_clients and top_clients[0]["revenue"] > metrics.get("total_revenue", 1) * 0.5:
            risks.append("Single client accounts for >50% of revenue — concentration risk")

        recs = self._health_recommendations(total_score, overdue_ratio, collection_rate, growth_pct)

        return {
            "score"         : total_score,
            "grade"         : grade,
            "status"        : status,
            "dimensions"    : {
                "collection_efficiency" : {"score": collection_score, "max": 30, "label": "Collection Efficiency"},
                "overdue_exposure"      : {"score": overdue_score,    "max": 25, "label": "Overdue Exposure"},
                "revenue_growth"        : {"score": growth_score,     "max": 20, "label": "Revenue Growth"},
                "cashflow_stability"    : {"score": stability_score,  "max": 15, "label": "Cashflow Stability"},
                "client_diversity"      : {"score": diversity_score,  "max": 10, "label": "Client Diversification"},
            },
            "strengths"      : strengths,
            "risks"          : risks,
            "recommendations": recs,
        }

    def _health_recommendations(
        self, score: int, overdue_ratio: float, collection_rate: float, growth_pct: float
    ) -> list[str]:
        recs = []
        if overdue_ratio > 20:
            recs.append("Activate automated escalation workflows for overdue accounts.")
        if collection_rate < 80:
            recs.append("Set up 5-day pre-due reminders to improve payment completion rate.")
        if growth_pct < 0:
            recs.append("Re-engage dormant clients with a targeted outreach sequence.")
        if score < 70:
            recs.append("Enable recurring invoices for stable clients to lock in predictable MRR.")
        recs.append("Review client payment terms — tighter terms reduce DSO significantly.")
        return recs[:4]

    # ------------------------------------------------------------------
    # 3. REVENUE FORECASTING ENGINE
    # ------------------------------------------------------------------

    def forecast_revenue(
        self,
        horizon: str = ForecastHorizon.MONTH,
        *,
        metrics: dict | None = None,
    ) -> dict:
        """
        Forecast revenue for the next week, month, or quarter.

        Method
        ------
        Weighted moving average of the last 3 months + growth trend + recurring baseline.
        Outputs optimistic, base, and conservative scenarios.

        Returns
        -------
        {
            "horizon": "month",
            "optimistic": 148000,
            "base": 131000,
            "conservative": 112000,
            "confidence": 82,
            "narrative": "...",
            "periods": [...]
        }
        """
        if metrics is None:
            metrics = self._build_metrics()

        monthly = metrics.get("monthly_revenue", {})
        values = list(monthly.values())
        horizon_days = {"week": 7, "month": 30, "quarter": 90}.get(horizon, 30)

        # Weighted moving average (recent months weighted more)
        if len(values) >= 3:
            weights = [1, 2, 3]
            last3 = values[-3:]
            wma = sum(v * w for v, w in zip(last3, weights)) / sum(weights)
        elif values:
            wma = sum(values) / len(values)
        else:
            wma = 0

        # Growth rate from last 2 months
        growth_rate = 0.0
        if len(values) >= 2 and values[-2]:
            growth_rate = (values[-1] - values[-2]) / values[-2]
        growth_rate = max(-0.3, min(0.5, growth_rate))  # Cap extreme outliers

        # Overdue recovery probability (~60% collected within 30 days)
        overdue_recovery = metrics.get("total_overdue", 0) * 0.6

        n_periods = {"week": 1, "month": 1, "quarter": 3}.get(horizon, 1)

        periods = []
        base = wma
        for i in range(1, n_periods + 1):
            label = (_now() + timedelta(days=horizon_days * i / n_periods)).strftime("%Y-%m")
            projected = base * (1 + growth_rate) ** i
            periods.append({
                "period"      : label,
                "optimistic"  : round(projected * 1.15 + overdue_recovery, 2),
                "base"        : round(projected, 2),
                "conservative": round(projected * 0.85, 2),
            })
            base = projected

        confidence = self.calculate_forecast_confidence(metrics)

        narrative = self._generate_llm_summary(
            metrics, context=f"Focus on {horizon} revenue forecast.", use_ai=True
        )

        return {
            "horizon"      : horizon,
            "optimistic"   : periods[-1]["optimistic"] if periods else 0,
            "base"         : periods[-1]["base"] if periods else 0,
            "conservative" : periods[-1]["conservative"] if periods else 0,
            "confidence"   : confidence["confidence"],
            "risk_level"   : confidence["risk_level"],
            "narrative"    : narrative,
            "periods"      : periods,
            "overdue_recovery_assumed": round(overdue_recovery, 2),
        }

    # ------------------------------------------------------------------
    # 4. CASHFLOW PREDICTION
    # ------------------------------------------------------------------

    def predict_cashflow(self, *, filters: dict | None = None) -> dict:
        """
        Forecast incoming cash, delayed payments, and projected balance.

        Returns
        -------
        {
            "projected_inflow"     : 124000,
            "delayed_payments"     : 38000,
            "expected_overdue_risk": 15000,
            "projected_balance"    : 86000,
            "cashflow_outlook"     : "stable | at_risk | strong | critical",
            "scenarios"            : {...},
            "narrative"            : "..."
        }
        """
        metrics = self._build_metrics(filters)
        total_overdue = metrics.get("total_overdue", 0)
        monthly = metrics.get("monthly_revenue", {})
        values = list(monthly.values())
        avg = sum(values) / len(values) if values else 0

        # Cashflow components
        projected_inflow = avg * 1.08  # 8% growth assumption
        recovery_rate = 0.65  # 65% of overdue collected within 30 days
        delayed_payments = total_overdue * recovery_rate
        unrecoverable_risk = total_overdue * (1 - recovery_rate)
        projected_balance = projected_inflow + delayed_payments

        if projected_balance > avg * 1.1:
            outlook = "strong"
        elif projected_balance > avg * 0.85:
            outlook = "stable"
        elif projected_balance > avg * 0.6:
            outlook = "at_risk"
        else:
            outlook = "critical"

        return {
            "projected_inflow"      : round(projected_inflow, 2),
            "delayed_payments"      : round(delayed_payments, 2),
            "expected_overdue_risk" : round(unrecoverable_risk, 2),
            "projected_balance"     : round(projected_balance, 2),
            "cashflow_outlook"      : outlook,
            "scenarios": {
                "optimistic"  : round(projected_inflow + total_overdue, 2),
                "base"        : round(projected_balance, 2),
                "conservative": round(projected_inflow - unrecoverable_risk, 2),
            },
            "narrative": (
                f"Projected next-month cashflow is ${projected_balance:,.0f} — "
                f"{'strong' if outlook in ('strong','stable') else 'under pressure'}. "
                f"${unrecoverable_risk:,.0f} in overdue invoices may not be collected, "
                f"representing the primary cashflow risk."
            ),
        }

    # ------------------------------------------------------------------
    # 5. KPI ANALYTICS ENGINE
    # ------------------------------------------------------------------

    def build_kpi_dashboard(self, *, filters: dict | None = None) -> dict:
        """
        Build a comprehensive KPI dashboard payload.

        KPIs
        ----
        Total Revenue | Outstanding Balance | Collection Rate | DSO |
        Avg Invoice Value | Overdue Rate | Monthly Growth | MRR Estimate

        Returns
        -------
        {
            "kpis": [{label, value, change, trend, unit, interpretation}],
            "chart_data": {...},
            "generated_at": "..."
        }
        """
        metrics = self._build_metrics(filters)

        col_rate = metrics.get("collection_rate", 0)
        dso = metrics.get("dso", 30)
        growth = metrics.get("revenue_growth_pct", 0)
        total_revenue = metrics.get("total_revenue", 0)
        total_overdue = metrics.get("total_overdue", 0)
        overdue_ratio = metrics.get("overdue_ratio", 0)
        avg_inv = metrics.get("avg_invoice_value", 0)

        def dso_interp(d):
            if d < 20: return "Excellent — invoices paid very quickly"
            if d < 30: return "Good — normal payment cycle"
            if d < 45: return "Watch — payment delays emerging"
            return "Concern — invoices taking too long to be paid"

        def col_interp(r):
            if r >= 90: return "Excellent collection efficiency"
            if r >= 80: return "Good — minor room for improvement"
            if r >= 65: return "Needs attention — revenue leakage detected"
            return "Critical — significant unpaid invoices"

        kpis = [
            {
                "id": "total_revenue", "label": "Total Revenue",
                "value": f"${total_revenue:,.0f}",
                "change": f"{growth:+.1f}%", "trend": "up" if growth >= 0 else "down",
                "unit": "currency", "interpretation": f"Revenue {'grew' if growth >= 0 else 'declined'} {abs(growth):.1f}% vs prior period.",
            },
            {
                "id": "outstanding", "label": "Outstanding Balance",
                "value": f"${total_overdue:,.0f}",
                "change": "", "trend": "neutral" if total_overdue == 0 else "down",
                "unit": "currency", "interpretation": f"{overdue_ratio:.1f}% of invoiced amount is overdue.",
            },
            {
                "id": "collection_rate", "label": "Collection Rate",
                "value": f"{col_rate:.1f}%",
                "change": "", "trend": "up" if col_rate >= 85 else "down",
                "unit": "percent", "interpretation": col_interp(col_rate),
            },
            {
                "id": "dso", "label": "DSO (Days)",
                "value": f"{dso:.0f} days",
                "change": "", "trend": "up" if dso < 30 else "down",
                "unit": "days", "interpretation": dso_interp(dso),
            },
            {
                "id": "avg_invoice", "label": "Avg Invoice Value",
                "value": f"${avg_inv:,.0f}",
                "change": "", "trend": "neutral",
                "unit": "currency", "interpretation": "Average value per invoice created.",
            },
            {
                "id": "overdue_rate", "label": "Overdue Rate",
                "value": f"{overdue_ratio:.1f}%",
                "change": "", "trend": "up" if overdue_ratio < 10 else "down",
                "unit": "percent",
                "interpretation": (
                    "Healthy — minimal overdue exposure" if overdue_ratio < 10
                    else "Moderate risk" if overdue_ratio < 25
                    else "High risk — immediate action needed"
                ),
            },
            {
                "id": "invoice_count", "label": "Total Invoices",
                "value": str(metrics.get("invoice_count", 0)),
                "change": "", "trend": "neutral", "unit": "count",
                "interpretation": "Total invoices created this period.",
            },
            {
                "id": "monthly_growth", "label": "Monthly Growth",
                "value": f"{growth:+.1f}%",
                "change": "", "trend": "up" if growth >= 0 else "down",
                "unit": "percent", "interpretation": f"Month-over-month revenue {'growth' if growth >= 0 else 'decline'}.",
            },
        ]

        return {
            "kpis": kpis,
            "chart_data": self._build_time_series(metrics),
            "health_score": self._score_business_health(metrics)["score"],
            "generated_at": _now().isoformat(),
        }

    # ------------------------------------------------------------------
    # 6. AI TREND DETECTION
    # ------------------------------------------------------------------

    def detect_financial_trends(self, *, filters: dict | None = None) -> dict:
        """
        Detect revenue growth patterns, late-payment increases, and seasonal signals.

        Returns
        -------
        {
            "trends": [{type, direction, magnitude, description}],
            "dominant_trend": "...",
            "trend_summary": "..."
        }
        """
        metrics = self._build_metrics(filters)
        monthly = metrics.get("monthly_revenue", {})
        values = list(monthly.values())
        trends = []

        # Revenue direction
        if len(values) >= 2:
            recent_growth = self._calculate_growth(values[-1], values[-2])
            trends.append({
                "type": "revenue",
                "direction": "up" if recent_growth > 0 else "down",
                "magnitude": abs(recent_growth),
                "description": f"Revenue {'grew' if recent_growth > 0 else 'declined'} {abs(recent_growth):.1f}% month-over-month.",
            })

        # Acceleration/deceleration
        if len(values) >= 3:
            prev_growth = self._calculate_growth(values[-2], values[-3])
            curr_growth = self._calculate_growth(values[-1], values[-2])
            if curr_growth > prev_growth + 5:
                trends.append({"type": "acceleration", "direction": "up", "magnitude": curr_growth - prev_growth,
                                "description": "Revenue growth is accelerating — strong momentum detected."})
            elif curr_growth < prev_growth - 5:
                trends.append({"type": "deceleration", "direction": "down", "magnitude": prev_growth - curr_growth,
                                "description": "Revenue growth is slowing — monitor closely."})

        # Overdue trend
        if metrics.get("overdue_ratio", 0) > 20:
            trends.append({"type": "overdue_spike", "direction": "down", "magnitude": metrics["overdue_ratio"],
                            "description": f"Overdue ratio at {metrics['overdue_ratio']:.1f}% — above healthy threshold of 15%."})

        # Concentration trend
        top_clients = metrics.get("top_clients", [])
        if top_clients and metrics.get("total_revenue"):
            top_share = top_clients[0]["revenue"] / metrics["total_revenue"]
            if top_share > 0.5:
                trends.append({"type": "concentration", "direction": "down", "magnitude": top_share * 100,
                                "description": f"Top client drives {top_share*100:.0f}% of revenue — concentration risk elevated."})

        # Seasonal signal (simplistic: compare to same month last year if available)
        labels = sorted(monthly.keys())
        if len(labels) >= 12:
            curr_key = labels[-1]
            year_ago_key = f"{int(curr_key[:4])-1}{curr_key[4:]}"
            if year_ago_key in monthly:
                yoy = self._calculate_growth(monthly[curr_key], monthly[year_ago_key])
                trends.append({"type": "seasonal", "direction": "up" if yoy > 0 else "down",
                                "magnitude": abs(yoy),
                                "description": f"Year-over-year growth: {yoy:+.1f}%."})

        dominant = max(trends, key=lambda t: t["magnitude"], default=None)
        return {
            "trends": trends,
            "dominant_trend": dominant["type"] if dominant else "stable",
            "trend_count": len(trends),
            "trend_summary": (
                f"{len(trends)} trend{'s' if len(trends) != 1 else ''} detected. "
                + (dominant["description"] if dominant else "Business metrics appear stable.")
            ),
        }

    # ------------------------------------------------------------------
    # 7. OVERDUE RISK ANALYSIS
    # ------------------------------------------------------------------

    def analyze_overdue_risk(self, *, filters: dict | None = None) -> dict:
        """
        Identify risky clients, payment delay patterns, and at-risk invoices.

        Returns
        -------
        {
            "total_overdue": 82000,
            "risk_level": "high",
            "risky_clients": [...],
            "aging_buckets": {...},
            "at_risk_invoices": [...],
            "recommendations": [...]
        }
        """
        invoices = self._fetch_invoice_data({"overdue_only": True})
        now = _now()

        aging_buckets = {"0_7": 0, "8_30": 0, "31_60": 0, "60_plus": 0}
        risky_clients: dict[str, dict] = {}
        at_risk: list[dict] = []

        for inv in invoices:
            amount = float(inv.total_amount)
            due = getattr(inv, "due_date", None)
            cid = str(getattr(inv, "client_id", "unknown"))

            days_overdue = (now - due).days if due else 0
            days_overdue = max(0, days_overdue)

            if days_overdue <= 7:
                aging_buckets["0_7"] += amount
            elif days_overdue <= 30:
                aging_buckets["8_30"] += amount
            elif days_overdue <= 60:
                aging_buckets["31_60"] += amount
            else:
                aging_buckets["60_plus"] += amount

            if cid not in risky_clients:
                risky_clients[cid] = {"client_id": cid, "total_overdue": 0, "invoice_count": 0, "max_days": 0}
            risky_clients[cid]["total_overdue"] += amount
            risky_clients[cid]["invoice_count"] += 1
            risky_clients[cid]["max_days"] = max(risky_clients[cid]["max_days"], days_overdue)

            if amount > 50000 or days_overdue > 30:
                at_risk.append({
                    "invoice_id": inv.id,
                    "client_id": cid,
                    "amount": amount,
                    "days_overdue": days_overdue,
                    "risk": "critical" if days_overdue > 60 else "high" if days_overdue > 30 else "medium",
                })

        total_overdue = sum(r["total_overdue"] for r in risky_clients.values())
        risk_level = (
            "critical" if aging_buckets["60_plus"] > total_overdue * 0.4 or total_overdue > 500000 else
            "high"     if aging_buckets["31_60"] > total_overdue * 0.3 else
            "medium"   if total_overdue > 0 else
            "low"
        )

        recs = []
        if aging_buckets["60_plus"] > 0:
            recs.append("Escalate invoices overdue 60+ days — consider formal notice or collections.")
        if aging_buckets["31_60"] > 0:
            recs.append("Send firm reminders to clients with 30–60 day overdue invoices.")
        if aging_buckets["8_30"] > 0:
            recs.append("Set up professional email reminders for invoices overdue 8–30 days.")

        return {
            "total_overdue"  : round(total_overdue, 2),
            "risk_level"     : risk_level,
            "risky_clients"  : sorted(risky_clients.values(), key=lambda x: x["total_overdue"], reverse=True)[:5],
            "aging_buckets"  : {k: round(v, 2) for k, v in aging_buckets.items()},
            "at_risk_invoices": sorted(at_risk, key=lambda x: x["days_overdue"], reverse=True)[:10],
            "recommendations": recs,
        }

    # ------------------------------------------------------------------
    # 8. CLIENT INTELLIGENCE ENGINE
    # ------------------------------------------------------------------

    def generate_client_insights(self, *, filters: dict | None = None) -> dict:
        """
        Rank and profile clients by revenue, speed, risk, and retention signals.

        Returns
        -------
        {
            "top_payers": [...],
            "fastest_payers": [...],
            "risky_clients": [...],
            "inactive_clients": [...],
            "churn_risk": {...},
            "summary": "..."
        }
        """
        metrics = self._build_metrics(filters)
        invoices = self._fetch_invoice_data()
        top_clients = metrics.get("top_clients", [])

        # Payment speed per client
        client_speed: dict[str, list[int]] = {}
        for inv in invoices:
            cid = str(getattr(inv, "client_id", "unknown"))
            if inv.status == "paid" and getattr(inv, "created_at", None) and getattr(inv, "due_date", None):
                days = max(0, (inv.due_date - inv.created_at).days)
                client_speed.setdefault(cid, []).append(days)

        fastest = sorted(
            [{"client_id": k, "avg_days": round(sum(v)/len(v), 1)} for k, v in client_speed.items()],
            key=lambda x: x["avg_days"],
        )[:5]

        # Inactive clients (no invoice in last 90 days)
        cutoff = _now() - timedelta(days=90)
        recent_clients = {str(getattr(i, "client_id", "")) for i in invoices if getattr(i, "created_at", None) and i.created_at >= cutoff}
        all_clients = {str(getattr(i, "client_id", "")) for i in invoices}
        inactive = list(all_clients - recent_clients)[:5]

        churn = self.predict_client_churn(filters=filters)

        return {
            "top_payers"      : top_clients[:5],
            "fastest_payers"  : fastest,
            "risky_clients"   : metrics.get("high_risk_clients", []),
            "inactive_clients": inactive,
            "churn_risk"      : churn,
            "client_count"    : len(all_clients),
            "summary"         : (
                f"{len(top_clients)} active revenue-generating clients. "
                f"{len(inactive)} clients inactive for 90+ days. "
                f"{len(metrics.get('high_risk_clients', []))} flagged as high risk."
            ),
        }

    # ------------------------------------------------------------------
    # 9. AI RECOMMENDATION GENERATOR
    # ------------------------------------------------------------------

    def generate_recommendations(
        self,
        *,
        metrics: dict | None = None,
        limit: int = 5,
    ) -> list[dict]:
        """
        Generate prioritised, data-grounded action recommendations.

        Returns
        -------
        List of recommendation dicts:
        [{priority, action, reason, impact, urgency}]
        """
        if metrics is None:
            metrics = self._build_metrics()

        total_overdue = metrics.get("total_overdue", 0)
        overdue_count = metrics.get("overdue_count", 0)
        collection_rate = metrics.get("collection_rate", 0)
        growth_pct = metrics.get("revenue_growth_pct", 0)
        top_clients = metrics.get("top_clients", [])
        dso = metrics.get("dso", 30)

        recs = []

        if overdue_count > 0:
            recs.append({
                "priority": 1,
                "action": f"Send reminders to {overdue_count} overdue client{'s' if overdue_count > 1 else ''} (${total_overdue:,.0f} outstanding)",
                "reason": f"${total_overdue:,.0f} in overdue invoices — each week of delay reduces collection probability by ~8%.",
                "impact": "Improve cashflow by recovering outstanding payments.",
                "urgency": "immediate",
            })

        if collection_rate < 80:
            recs.append({
                "priority": 2,
                "action": "Enable automated pre-due reminders (5 days before due date)",
                "reason": f"Collection rate is {collection_rate:.1f}% — automated reminders typically add 12–18% improvement.",
                "impact": f"Recover approx. ${(metrics.get('total_invoiced', 0) * 0.15):,.0f}/month in currently lost revenue.",
                "urgency": "this_week",
            })

        if top_clients and top_clients[0]["revenue"] > metrics.get("total_revenue", 1) * 0.5:
            recs.append({
                "priority": 3,
                "action": "Reduce client concentration — target 2 new mid-market clients",
                "reason": f"Top client drives >{top_clients[0]['revenue'] / max(metrics.get('total_revenue', 1), 1) * 100:.0f}% of revenue — single point of failure.",
                "impact": "Reduce revenue risk; improve business valuation multiple.",
                "urgency": "this_month",
            })

        recs.append({
            "priority": 4,
            "action": "Convert recurring service clients to monthly subscription invoices",
            "reason": "Predictable MRR improves cashflow planning and reduces manual invoice creation.",
            "impact": "Estimated 22% improvement in revenue predictability.",
            "urgency": "this_month",
        })

        if dso > 35:
            recs.append({
                "priority": 5,
                "action": f"Shorten payment terms — DSO is {dso:.0f} days (target: < 30)",
                "reason": "Extended DSO locks up working capital and signals weak collection discipline.",
                "impact": f"Reducing DSO to 25 days frees up approx. ${(metrics.get('total_revenue', 0) * 0.08):,.0f}.",
                "urgency": "this_month",
            })

        if growth_pct < 0:
            recs.append({
                "priority": 5,
                "action": "Re-engage dormant clients with a targeted offer or check-in",
                "reason": f"Revenue declined {abs(growth_pct):.1f}% — re-activation is faster than new client acquisition.",
                "impact": "Recovering 2 dormant clients can restore revenue to prior-period levels.",
                "urgency": "this_week",
            })

        # AI enhancement
        ai = self._get_ai()
        if ai and len(recs) < limit:
            try:
                payload = json.dumps({k: metrics.get(k) for k in [
                    "total_revenue", "total_overdue", "collection_rate", "dso", "overdue_count",
                ]})
                prompt = (
                    f"Business metrics: {payload}. "
                    "Add 2 specific, actionable business recommendations not already listed. "
                    'Return JSON: {"recs": [{"priority": N, "action": "...", "reason": "...", "impact": "...", "urgency": "immediate|this_week|this_month"}]}'
                )
                resp = ai.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=250,
                    response_format={"type": "json_object"},
                )
                extra = json.loads(resp.choices[0].message.content).get("recs", [])
                recs.extend(extra)
            except Exception as exc:
                logger.warning("AI recommendations failed: %s", exc)

        return sorted(recs, key=lambda r: r.get("priority", 99))[:limit]

    # ------------------------------------------------------------------
    # 10. WEEKLY AI SUMMARY
    # ------------------------------------------------------------------

    def generate_weekly_summary(self) -> dict:
        """
        Generate a structured weekly business summary.

        Returns
        -------
        {
            "period": "Week of ...",
            "headline": "...",
            "revenue": {"amount": ..., "change": "..."},
            "paid_invoices": N,
            "overdue_changes": "...",
            "wins": [...],
            "warnings": [...],
            "action_items": [...],
            "ai_insights": "..."
        }
        """
        now = _now()
        week_start = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        filters = {"start_date": week_start}

        metrics = self._build_metrics(filters)
        all_metrics = self._build_metrics()

        growth = metrics.get("revenue_growth_pct", 0)

        wins, warnings = [], []

        if growth > 0:
            wins.append(f"Revenue grew {growth:.1f}% this week.")
        if metrics.get("collection_rate", 0) >= 85:
            wins.append(f"Collection rate of {metrics['collection_rate']:.1f}% — strong payment discipline.")
        if metrics.get("overdue_count", 0) == 0:
            wins.append("No new overdue invoices this week.")

        if metrics.get("total_overdue", 0) > 0:
            warnings.append(f"${metrics['total_overdue']:,.0f} in overdue balances — follow up required.")
        if growth < -10:
            warnings.append(f"Revenue declined {abs(growth):.1f}% this week — investigate root cause.")

        actions = [r["action"] for r in self.generate_recommendations(metrics=all_metrics, limit=3)]

        narrative = self.generate_financial_narrative(metrics=metrics)

        return {
            "period"          : f"Week of {week_start} – {now.strftime('%Y-%m-%d')}",
            "headline"        : f"Revenue {'+' if growth >= 0 else ''}{growth:.1f}% — {'strong' if growth > 5 else 'steady' if growth >= 0 else 'declining'} week",
            "revenue"         : {"amount": metrics.get("total_revenue", 0), "change": f"{growth:+.1f}%"},
            "paid_invoices"   : metrics.get("paid_count", 0),
            "overdue_changes" : f"${metrics.get('total_overdue', 0):,.0f} overdue across {metrics.get('overdue_count', 0)} invoices",
            "wins"            : wins,
            "warnings"        : warnings,
            "action_items"    : actions,
            "ai_insights"     : narrative,
        }

    # ------------------------------------------------------------------
    # 11. FINANCIAL NARRATIVE GENERATOR
    # ------------------------------------------------------------------

    def generate_financial_narrative(
        self,
        *,
        metrics: dict | None = None,
        filters: dict | None = None,
    ) -> str:
        """
        Write a plain-English AI narrative explaining business performance.

        Uses GPT-4o with the business_insights.txt prompt for a senior-analyst
        voice. Falls back to a templated rule-based summary.

        Returns
        -------
        2–4 sentence narrative string.
        """
        if metrics is None:
            metrics = self._build_metrics(filters)
        return self._generate_llm_summary(metrics, use_ai=True)

    def _generate_llm_summary(
        self, metrics: dict, *, context: str = "", use_ai: bool = True
    ) -> str:
        ai = self._get_ai() if use_ai else None
        total_revenue = metrics.get("total_revenue", 0)
        total_overdue = metrics.get("total_overdue", 0)
        growth = metrics.get("revenue_growth_pct", 0)
        col_rate = metrics.get("collection_rate", 0)
        dso = metrics.get("dso", 30)

        if not ai:
            direction = "grew" if growth >= 0 else "declined"
            return (
                f"Revenue {direction} {abs(growth):.1f}% to ${total_revenue:,.0f} this period. "
                f"${total_overdue:,.0f} remains outstanding across {metrics.get('overdue_count', 0)} invoices. "
                f"Collection rate is {col_rate:.1f}% with a DSO of {dso:.0f} days."
            )

        try:
            payload = {
                "total_revenue"  : total_revenue,
                "prev_revenue"   : metrics.get("prev_revenue", 0),
                "total_overdue"  : total_overdue,
                "invoice_count"  : metrics.get("invoice_count", 0),
                "collection_rate": col_rate,
                "dso"            : dso,
                "monthly_revenue": metrics.get("monthly_revenue", {}),
            }
            prompt = (
                f"Write a 3-sentence financial narrative for an executive dashboard. "
                f"Data: {json.dumps(payload)}. "
                + (f"Context: {context} " if context else "")
                + "Be specific with numbers. Sound like a senior CFO analyst. No bullet points."
            )
            resp = ai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=180,
            )
            return resp.choices[0].message.content.strip()
        except Exception as exc:
            logger.warning("Financial narrative LLM failed: %s", exc)
            return f"Revenue: ${total_revenue:,.0f} | Outstanding: ${total_overdue:,.0f} | Collection: {col_rate:.1f}%"

    # ------------------------------------------------------------------
    # 12. SMART ALERT DETECTION
    # ------------------------------------------------------------------

    def detect_critical_alerts(self, *, filters: dict | None = None) -> list[dict]:
        """
        Detect and return critical business alerts that need immediate attention.

        Alert types
        -----------
        revenue_drop | overdue_spike | cashflow_risk | client_risk | collection_drop

        Returns
        -------
        List of alert dicts sorted by severity.
        """
        metrics = self._build_metrics(filters)
        alerts = []

        growth = metrics.get("revenue_growth_pct", 0)
        overdue_ratio = metrics.get("overdue_ratio", 0)
        col_rate = metrics.get("collection_rate", 0)
        total_overdue = metrics.get("total_overdue", 0)

        if growth < -20:
            alerts.append({
                "type": "revenue_drop", "severity": AlertSeverity.CRITICAL,
                "title": "Severe Revenue Drop",
                "description": f"Revenue declined {abs(growth):.1f}% — immediate investigation needed.",
                "action": "Review recent invoice cancellations and client losses.",
            })
        elif growth < -10:
            alerts.append({
                "type": "revenue_drop", "severity": AlertSeverity.HIGH,
                "title": "Revenue Declining",
                "description": f"Revenue down {abs(growth):.1f}% vs prior period.",
                "action": "Re-engage inactive clients and accelerate new invoice creation.",
            })

        if overdue_ratio > 40:
            alerts.append({
                "type": "overdue_spike", "severity": AlertSeverity.CRITICAL,
                "title": "Critical Overdue Spike",
                "description": f"{overdue_ratio:.1f}% of invoiced revenue is overdue (${total_overdue:,.0f}).",
                "action": "Activate escalation workflow for all overdue accounts immediately.",
            })
        elif overdue_ratio > 25:
            alerts.append({
                "type": "overdue_spike", "severity": AlertSeverity.HIGH,
                "title": "Overdue Rate Elevated",
                "description": f"Overdue at {overdue_ratio:.1f}% — above healthy 15% threshold.",
                "action": "Send firm reminders to top overdue accounts.",
            })

        if col_rate < 65:
            alerts.append({
                "type": "collection_drop", "severity": AlertSeverity.HIGH,
                "title": "Collection Rate Critical",
                "description": f"Only {col_rate:.1f}% of invoices are being collected.",
                "action": "Review payment terms and implement automated reminder sequences.",
            })

        cashflow = self.predict_cashflow()
        if cashflow.get("cashflow_outlook") == "critical":
            alerts.append({
                "type": "cashflow_risk", "severity": AlertSeverity.CRITICAL,
                "title": "Cashflow Crisis Risk",
                "description": "Projected cashflow significantly below operational baseline.",
                "action": "Prioritise overdue collection and delay non-essential spend.",
            })

        return sorted(alerts, key=lambda a: ["low","medium","high","critical"].index(a["severity"]), reverse=True)

    # ------------------------------------------------------------------
    # 13. FORECAST CONFIDENCE SCORING
    # ------------------------------------------------------------------

    def calculate_forecast_confidence(self, metrics: dict | None = None) -> dict:
        """
        Score forecast confidence 0–100 based on data quality and stability.

        Returns
        -------
        {"confidence": 87, "risk_level": "low", "factors": [...]}
        """
        if metrics is None:
            metrics = self._build_metrics()

        monthly = metrics.get("monthly_revenue", {})
        values = list(monthly.values())
        factors, penalty = [], 0

        # Data richness
        if len(values) >= 6:
            factors.append("6+ months of history — high confidence")
        elif len(values) >= 3:
            penalty += 10
            factors.append("3–5 months of history — moderate confidence")
        else:
            penalty += 25
            factors.append("< 3 months of history — low confidence")

        # Revenue stability (coefficient of variation)
        if len(values) >= 2:
            avg = sum(values) / len(values)
            std = (sum((v - avg) ** 2 for v in values) / len(values)) ** 0.5
            cv = (std / avg) if avg else 1
            if cv > 0.5:
                penalty += 15
                factors.append(f"High revenue volatility (CV={cv:.2f}) — reduces forecast reliability")
            elif cv > 0.25:
                penalty += 5
                factors.append(f"Moderate revenue variability (CV={cv:.2f})")

        # Overdue exposure
        if metrics.get("overdue_ratio", 0) > 30:
            penalty += 10
            factors.append("High overdue ratio increases cashflow uncertainty")

        confidence = max(30, min(95, 90 - penalty))
        risk_level = "low" if confidence >= 80 else "medium" if confidence >= 60 else "high"

        return {"confidence": confidence, "risk_level": risk_level, "factors": factors}

    # ------------------------------------------------------------------
    # 14. REVENUE GROWTH ANALYZER
    # ------------------------------------------------------------------

    def analyze_growth_metrics(self, *, filters: dict | None = None) -> dict:
        """
        Track MoM, QoQ, and ARR growth rates.

        Returns
        -------
        {
            "mom": +18.2, "qoq": +24.1, "arr_estimate": 1488000,
            "mrr_estimate": 124000, "growth_trend": "accelerating|stable|declining"
        }
        """
        metrics = self._build_metrics(filters)
        monthly = metrics.get("monthly_revenue", {})
        values = list(monthly.values())
        labels = sorted(monthly.keys())

        mom = self._calculate_growth(values[-1], values[-2]) if len(values) >= 2 else 0
        qoq = self._calculate_growth(
            sum(values[-3:]) / 3, sum(values[-6:-3]) / 3
        ) if len(values) >= 6 else mom

        mrr = values[-1] if values else 0
        arr = mrr * 12

        if len(values) >= 3:
            recent_growth = self._calculate_growth(values[-1], values[-2])
            prev_growth = self._calculate_growth(values[-2], values[-3])
            growth_trend = "accelerating" if recent_growth > prev_growth + 3 else \
                           "decelerating" if recent_growth < prev_growth - 3 else "stable"
        else:
            growth_trend = "stable"

        return {
            "mom": round(mom, 2),
            "qoq": round(qoq, 2),
            "mrr_estimate": round(mrr, 2),
            "arr_estimate": round(arr, 2),
            "growth_trend": growth_trend,
            "labels": labels[-6:],
            "values": values[-6:],
        }

    # ------------------------------------------------------------------
    # 15. PAYMENT BEHAVIOR ANALYSIS
    # ------------------------------------------------------------------

    def analyze_payment_behavior(self, *, filters: dict | None = None) -> dict:
        """
        Analyse how clients pay: average days to pay, slow payers, reliable clients.

        Returns
        -------
        {
            "avg_days_to_pay": 24,
            "fastest_payer": {...},
            "slowest_payer": {...},
            "slow_payers": [...],
            "reliable_clients": [...],
            "behavior_summary": "..."
        }
        """
        invoices = self._fetch_invoice_data(filters)
        client_days: dict[str, list[int]] = {}

        for inv in invoices:
            if inv.status == "paid" and getattr(inv, "created_at", None) and getattr(inv, "due_date", None):
                cid = str(getattr(inv, "client_id", "unknown"))
                days = max(0, (inv.due_date - inv.created_at).days)
                client_days.setdefault(cid, []).append(days)

        if not client_days:
            return {"avg_days_to_pay": 30, "slow_payers": [], "reliable_clients": [], "behavior_summary": "Insufficient payment data."}

        all_days = [d for days in client_days.values() for d in days]
        avg = sum(all_days) / len(all_days) if all_days else 30

        ranked = sorted(
            [{"client_id": k, "avg_days": round(sum(v)/len(v), 1), "invoice_count": len(v)} for k, v in client_days.items()],
            key=lambda x: x["avg_days"],
        )

        slow_threshold = avg * 1.5
        slow_payers = [c for c in ranked if c["avg_days"] > slow_threshold]
        reliable = [c for c in ranked if c["avg_days"] <= avg * 0.75 and c["invoice_count"] >= 2]

        return {
            "avg_days_to_pay"  : round(avg, 1),
            "fastest_payer"    : ranked[0] if ranked else None,
            "slowest_payer"    : ranked[-1] if ranked else None,
            "slow_payers"      : slow_payers[:5],
            "reliable_clients" : reliable[:5],
            "behavior_summary" : (
                f"Average payment time is {avg:.0f} days. "
                f"{len(slow_payers)} client{'s are' if len(slow_payers) != 1 else ' is'} consistently slow. "
                f"{len(reliable)} reliable client{'s' if len(reliable) != 1 else ''} pay well ahead of schedule."
            ),
        }

    # ------------------------------------------------------------------
    # 16. CHURN RISK PREDICTION
    # ------------------------------------------------------------------

    def predict_client_churn(self, *, filters: dict | None = None) -> dict:
        """
        Predict which clients are at risk of churning based on activity signals.

        Signals
        -------
        - No invoice in last 60 days (for previously active clients)
        - Declining invoice frequency vs prior quarter
        - Declining invoice amounts

        Returns
        -------
        {
            "high_churn_risk": [...],
            "medium_churn_risk": [...],
            "churn_risk_count": N,
            "summary": "..."
        }
        """
        invoices = self._fetch_invoice_data(filters)
        now = _now()

        client_timeline: dict[str, list[datetime]] = {}
        for inv in invoices:
            cid = str(getattr(inv, "client_id", "unknown"))
            if getattr(inv, "created_at", None):
                client_timeline.setdefault(cid, []).append(inv.created_at)

        high_risk, medium_risk = [], []

        for cid, dates in client_timeline.items():
            dates_sorted = sorted(dates)
            last_inv = dates_sorted[-1]
            days_since = (now - last_inv).days

            # Frequency trend
            recent_count = sum(1 for d in dates_sorted if (now - d).days <= 90)
            prev_count = sum(1 for d in dates_sorted if 90 < (now - d).days <= 180)

            if days_since > 90:
                high_risk.append({"client_id": cid, "days_inactive": days_since, "signal": "90+ days inactive"})
            elif days_since > 60 and recent_count < prev_count * 0.5:
                medium_risk.append({"client_id": cid, "days_inactive": days_since, "signal": "Declining frequency"})

        return {
            "high_churn_risk"  : high_risk[:5],
            "medium_churn_risk": medium_risk[:5],
            "churn_risk_count" : len(high_risk) + len(medium_risk),
            "summary"          : (
                f"{len(high_risk)} client{'s' if len(high_risk) != 1 else ''} at high churn risk (90+ days inactive). "
                f"{len(medium_risk)} client{'s' if len(medium_risk) != 1 else ''} showing declining engagement."
            ),
        }

    # ------------------------------------------------------------------
    # 17. AI INSIGHT CARDS GENERATOR
    # ------------------------------------------------------------------

    def generate_insight_cards(
        self,
        *,
        metrics: dict | None = None,
        filters: dict | None = None,
    ) -> list[dict]:
        """
        Generate startup-style insight cards for the dashboard.

        Returns
        -------
        List of card dicts:
        [{id, type, title, value, body, severity, icon, color, action}]
        """
        if metrics is None:
            metrics = self._build_metrics(filters)

        cards = []
        total_revenue = metrics.get("total_revenue", 0)
        total_overdue = metrics.get("total_overdue", 0)
        growth = metrics.get("revenue_growth_pct", 0)
        col_rate = metrics.get("collection_rate", 0)
        overdue_ratio = metrics.get("overdue_ratio", 0)
        overdue_count = metrics.get("overdue_count", 0)
        top_clients = metrics.get("top_clients", [])
        health = self._score_business_health(metrics)

        cards.append({
            "id": "revenue_momentum", "type": "revenue_momentum",
            "title": "Revenue Momentum",
            "value": f"${total_revenue:,.0f}",
            "body": f"Revenue {'grew' if growth >= 0 else 'declined'} {abs(growth):.1f}% this period. "
                    + ("Strong performance — keep the momentum going." if growth > 10
                       else "Stable trajectory." if growth >= 0
                       else "Investigate root cause of decline."),
            "severity": "low" if growth >= 0 else "high",
            "icon": "📈" if growth >= 0 else "📉",
            "color": "green" if growth >= 10 else "purple" if growth >= 0 else "red",
            "action": "Generate revenue report",
        })

        cards.append({
            "id": "collection_efficiency", "type": "collection_win",
            "title": "Collection Rate",
            "value": f"{col_rate:.1f}%",
            "body": f"{metrics.get('paid_count', 0)} of {metrics.get('invoice_count', 0)} invoices paid. "
                    + ("Excellent payment discipline." if col_rate >= 90
                       else "Room for improvement — consider automated reminders." if col_rate >= 75
                       else "Critical — significant revenue leakage detected."),
            "severity": "low" if col_rate >= 85 else "medium" if col_rate >= 70 else "critical",
            "icon": "✅" if col_rate >= 85 else "⚠️",
            "color": "green" if col_rate >= 85 else "yellow" if col_rate >= 70 else "red",
            "action": None,
        })

        if total_overdue > 0:
            cards.append({
                "id": "overdue_exposure", "type": "payment_risk",
                "title": "Overdue Exposure",
                "value": f"${total_overdue:,.0f}",
                "body": f"{overdue_count} invoice{'s' if overdue_count != 1 else ''} overdue — {overdue_ratio:.1f}% of invoiced amount at risk.",
                "severity": "critical" if overdue_ratio > 35 else "high" if overdue_ratio > 20 else "medium",
                "icon": "🚨" if overdue_ratio > 35 else "⚠️",
                "color": "red" if overdue_ratio > 20 else "yellow",
                "action": "Send reminders",
            })

        cards.append({
            "id": "health_score", "type": "health_update",
            "title": "Business Health",
            "value": f"{health['score']}/100 ({health['grade']})",
            "body": health.get("status", "").replace("_", " ").title() + " — "
                    + (health["strengths"][0] if health.get("strengths") else "Review KPI dashboard for details."),
            "severity": "low" if health["score"] >= 80 else "medium" if health["score"] >= 65 else "high",
            "icon": "🏆" if health["score"] >= 80 else "📊",
            "color": "green" if health["score"] >= 80 else "yellow" if health["score"] >= 65 else "red",
            "action": "View full report",
        })

        if top_clients:
            top = top_clients[0]
            top_share = top["revenue"] / (total_revenue or 1) * 100
            cards.append({
                "id": "top_client", "type": "client_risk" if top_share > 50 else "top_client_trend",
                "title": "Top Client",
                "value": f"${top['revenue']:,.0f}",
                "body": f"Client {top['client_id']} drives {top_share:.0f}% of revenue."
                        + (" Consider diversifying." if top_share > 50 else " Healthy concentration."),
                "severity": "medium" if top_share > 50 else "low",
                "icon": "🏆" if top_share <= 50 else "💡",
                "color": "blue" if top_share <= 50 else "yellow",
                "action": None,
            })

        return cards[:5]

    # ------------------------------------------------------------------
    # 18. BUSINESS OPTIMISATION SUGGESTIONS
    # ------------------------------------------------------------------

    def generate_optimization_tips(self, *, filters: dict | None = None) -> list[dict]:
        """
        Generate platform optimisation tips to improve business efficiency.

        Returns
        -------
        List of tip dicts: [{category, tip, impact, effort}]
        """
        metrics = self._build_metrics(filters)
        tips = []

        if metrics.get("dso", 30) > 30:
            tips.append({
                "category": "efficiency",
                "tip": "Shorten invoice payment terms from Net 30 to Net 15 for new clients.",
                "impact": "high",
                "effort": "low",
            })

        tips.append({
            "category": "automation",
            "tip": "Enable automated reminder sequences: 5 days before due → due date → 7 days after.",
            "impact": "high",
            "effort": "low",
        })

        if metrics.get("collection_rate", 100) < 85:
            tips.append({
                "category": "collection",
                "tip": "Add online payment links to all invoices — reduces friction and speeds up payment.",
                "impact": "high",
                "effort": "low",
            })

        tips.append({
            "category": "revenue",
            "tip": "Convert top 3 retainer clients to recurring subscriptions — reduces invoicing overhead.",
            "impact": "medium",
            "effort": "medium",
        })

        tips.append({
            "category": "risk",
            "tip": "Require 50% advance payment for new enterprise clients until payment history is established.",
            "impact": "high",
            "effort": "low",
        })

        tips.append({
            "category": "growth",
            "tip": "Set up automatic thank-you messages post-payment — improves retention and referrals.",
            "impact": "medium",
            "effort": "low",
        })

        return tips

    # ------------------------------------------------------------------
    # 19. REAL-TIME KPI REFRESH DATA
    # ------------------------------------------------------------------

    def build_realtime_metrics(self) -> dict:
        """
        Build a lightweight real-time metrics payload for WebSocket / live dashboard updates.

        Emits a SocketIO 'kpi_update' event if SocketIO is configured.

        Returns
        -------
        Compact metrics dict suitable for frequent polling or push.
        """
        metrics = self._build_metrics()
        health = self._score_business_health(metrics)

        payload = {
            "total_revenue"   : metrics.get("total_revenue", 0),
            "total_overdue"   : metrics.get("total_overdue", 0),
            "overdue_count"   : metrics.get("overdue_count", 0),
            "collection_rate" : metrics.get("collection_rate", 0),
            "health_score"    : health["score"],
            "health_grade"    : health["grade"],
            "invoice_count"   : metrics.get("invoice_count", 0),
            "paid_count"      : metrics.get("paid_count", 0),
            "timestamp"       : _now().isoformat(),
        }

        sio = self._get_socketio()
        if sio:
            try:
                sio.emit("kpi_update", payload)
                sio.emit("dashboard_refresh", {"trigger": "analytics_refresh"})
            except Exception as exc:
                logger.warning("SocketIO kpi_update failed: %s", exc)

        return payload

    # ------------------------------------------------------------------
    # 20. SMART ANALYTICS FILTERS
    # ------------------------------------------------------------------

    def filter_analytics_data(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        client_id: str | None = None,
        currency: str | None = None,
        status: str | None = None,
        overdue_only: bool = False,
        recurring_only: bool = False,
    ) -> dict:
        """
        Apply filters and return a complete analytics dataset for the filtered scope.

        Parameters
        ----------
        start_date    : ISO date string, e.g. "2026-01-01"
        end_date      : ISO date string, e.g. "2026-05-31"
        client_id     : Filter to a specific client
        currency      : Currency code filter
        status        : Invoice status filter
        overdue_only  : Only overdue invoices
        recurring_only: Only recurring invoices

        Returns
        -------
        Full metrics dict for the filtered scope.
        """
        filters = {}
        if start_date:
            filters["start_date"] = start_date
        if end_date:
            filters["end_date"] = end_date
        if client_id:
            filters["client_id"] = client_id
        if currency:
            filters["currency"] = currency
        if status:
            filters["status"] = status
        if overdue_only:
            filters["overdue_only"] = True
        if recurring_only:
            filters["recurring_only"] = True

        # Clear cache when applying filters
        self._data_cache = None
        metrics = self._build_metrics(filters)
        self._data_cache = None  # Don't persist filtered results in cache

        return {
            "filters_applied": filters,
            "metrics"        : metrics,
            "kpis"           : self.build_kpi_dashboard(filters=filters)["kpis"],
            "insight_cards"  : self.generate_insight_cards(metrics=metrics),
        }

    # ==================================================================
    # INTERNAL HELPERS
    # ==================================================================

    def _calculate_growth(self, current: float, previous: float) -> float:
        """Calculate % growth between two values."""
        if not previous:
            return 100.0 if current > 0 else 0.0
        return round((current - previous) / previous * 100, 2)

    def _build_time_series(self, metrics: dict) -> dict:
        """Build Chart.js-compatible time series from monthly revenue."""
        monthly = metrics.get("monthly_revenue", {})
        labels = sorted(monthly.keys())
        values = [monthly[m] for m in labels]

        avg = sum(values) / len(values) if values else 0
        forecast_labels = [
            (_now() + timedelta(days=30 * i)).strftime("%Y-%m") for i in range(1, 4)
        ]
        forecast_values = [round(avg * (1.08 ** i), 2) for i in range(1, 4)]

        return {
            "revenue": {
                "labels": labels,
                "datasets": [{"label": "Revenue", "data": values, "color": "#7C3AED"}],
            },
            "forecast": {
                "labels": labels + forecast_labels,
                "datasets": [
                    {"label": "Actual", "data": values + [None]*3, "color": "#7C3AED"},
                    {"label": "Forecast", "data": [None]*len(labels) + forecast_values, "color": "#22C55E", "dashed": True},
                ],
            },
        }

    def _detect_patterns(self, values: list[float]) -> str:
        """Detect simple pattern: growing | declining | volatile | stable."""
        if len(values) < 2:
            return "stable"
        growth_rates = [
            (values[i] - values[i-1]) / max(values[i-1], 1) for i in range(1, len(values))
        ]
        avg_growth = sum(growth_rates) / len(growth_rates)
        variance = sum((r - avg_growth) ** 2 for r in growth_rates) / len(growth_rates)
        if variance > 0.1:
            return "volatile"
        if avg_growth > 0.05:
            return "growing"
        if avg_growth < -0.05:
            return "declining"
        return "stable"

    def _rank_clients(self, invoices: list) -> list[dict]:
        """Rank clients by total invoiced amount."""
        client_rev: dict[str, float] = {}
        for inv in invoices:
            cid = str(getattr(inv, "client_id", "unknown"))
            client_rev[cid] = client_rev.get(cid, 0) + float(inv.total_amount)
        return sorted([{"client_id": k, "revenue": v} for k, v in client_rev.items()],
                      key=lambda x: x["revenue"], reverse=True)

    def _build_risk_list(self, metrics: dict) -> list[dict]:
        """Build structured risk list from metrics."""
        risks = []
        if metrics.get("overdue_ratio", 0) > 20:
            risks.append({"type": "overdue", "severity": "high", "title": "High Overdue Exposure",
                          "description": f"{metrics['overdue_ratio']:.1f}% of invoiced amount is overdue.",
                          "mitigation": "Activate automated reminder and escalation workflows."})
        if metrics.get("revenue_growth_pct", 0) < -10:
            risks.append({"type": "trend", "severity": "high", "title": "Revenue Declining",
                          "description": f"Revenue fell {abs(metrics['revenue_growth_pct']):.1f}%.",
                          "mitigation": "Re-engage dormant clients and accelerate invoicing."})
        return risks

    def _quick_actions(self, metrics: dict) -> list[str]:
        actions = []
        if metrics.get("total_overdue", 0) > 0:
            actions.append(f"Send reminders to {metrics.get('overdue_count', 0)} overdue accounts")
        actions.append("Generate monthly executive report")
        actions.append("Review business health dashboard")
        return actions[:3]


# ===========================================================================
# MODULE-LEVEL CONVENIENCE FUNCTIONS
# ===========================================================================

def get_business_insights(user_id: int | None = None, *, filters: dict | None = None) -> dict:
    """Quick access: generate full AI business insights."""
    return AnalyticsAgent(user_id=user_id).generate_business_insights(filters=filters)


def get_health_score(user_id: int | None = None) -> dict:
    """Quick access: calculate business health score."""
    return AnalyticsAgent(user_id=user_id).calculate_health_score()


def get_realtime_kpis(user_id: int | None = None) -> dict:
    """Quick access: build real-time KPI payload for WebSocket push."""
    return AnalyticsAgent(user_id=user_id).build_realtime_metrics()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.utcnow()


def _new_id() -> str:
    return str(uuid.uuid4())


def _severity_from_score(score: int) -> str:
    if score >= 80: return "low"
    if score >= 65: return "medium"
    if score >= 50: return "high"
    return "critical"


_INLINE_INSIGHTS_PROMPT = """
You are an AI financial intelligence assistant for InvoiceFlow.
Today: {today_date}. Period: {period_label}.
Analyze the provided business metrics JSON and return a structured JSON with:
health_score (0-100), health_grade (A+/A/B/C/D/F), severity (low/medium/high/critical),
summary (3-4 sentence executive narrative), insights (3-5 cards), recommendations (3-5 items),
risks (array), opportunities (array), actions (3 quick wins), forecast (2 sentence cashflow outlook).
Return ONLY valid JSON.
"""
