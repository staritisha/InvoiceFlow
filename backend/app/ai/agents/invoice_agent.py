"""
app/ai/agents/invoice_agent.py

AI Invoice Agent for InvoiceFlow.
Converts natural language prompts, voice transcripts, and partial data into
fully structured, validated, enriched invoice objects — ready to save or render.

Pipeline
--------
User Prompt → LLM Extraction → Validation → Auto-fill → Calculations →
Risk Analysis → Enhancement → Final Structured Invoice

Usage
-----
from app.ai.agents.invoice_agent import InvoiceAgent

agent = InvoiceAgent(user_id=42)
result = agent.generate_invoice_from_prompt(
    "Create invoice for Netflix for AI consulting and dashboard setup, ₹1.5 lakh, due next Friday."
)
invoice = result["invoice"]
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants & Enumerations
# ---------------------------------------------------------------------------

class InvoicePriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class InvoiceTheme(str, Enum):
    STARTUP = "startup"
    CORPORATE = "corporate"
    ELEGANT = "elegant"
    MINIMAL = "minimal"
    DARK_PREMIUM = "dark-premium"
    MODERN_GLASS = "modern-glass"


class InvoiceTone(str, Enum):
    PREMIUM = "premium"
    FRIENDLY = "friendly"
    EXECUTIVE = "executive"
    STARTUP = "startup"


SUPPORTED_CURRENCIES = {"INR", "USD", "EUR", "GBP", "SGD", "AED", "JPY"}

CURRENCY_SYMBOLS = {
    "INR": "₹", "USD": "$", "EUR": "€",
    "GBP": "£", "SGD": "S$", "AED": "د.إ", "JPY": "¥",
}

# Tax rules by currency/region
TAX_RULES = {
    "INR": {"name": "GST", "default_rate": 18.0, "rates": [0, 5, 12, 18, 28]},
    "USD": {"name": "Sales Tax", "default_rate": 0.0, "rates": []},
    "EUR": {"name": "VAT", "default_rate": 20.0, "rates": [0, 5, 10, 20]},
    "GBP": {"name": "VAT", "default_rate": 20.0, "rates": [0, 5, 20]},
    "SGD": {"name": "GST", "default_rate": 9.0, "rates": [0, 9]},
    "AED": {"name": "VAT", "default_rate": 5.0, "rates": [0, 5]},
    "JPY": {"name": "Consumption Tax", "default_rate": 10.0, "rates": [0, 8, 10]},
}

HIGH_RISK_KEYWORDS = {"overdue", "late", "delayed", "pending", "unpaid", "chase", "reminder"}

INVOICE_NUMBER_PREFIX = "INV"


# ===========================================================================
# MAIN AGENT CLASS
# ===========================================================================

class InvoiceAgent:
    """
    AI-powered invoice generation and enhancement agent.

    Parameters
    ----------
    user_id    : Owning user (used for invoice numbering and risk context).
    model      : OpenAI model to use for LLM calls.
    prompt_path: Path to the invoice_generation.txt prompt file.
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
            os.path.dirname(__file__), "../../ai/prompts/invoice_generation.txt"
        )
        self._system_prompt: str | None = None
        self._ai: Any = None

    # ------------------------------------------------------------------
    # Internal: lazy-load OpenAI client and system prompt
    # ------------------------------------------------------------------

    def _get_ai(self):
        if self._ai is None:
            try:
                import openai
                key = os.getenv("OPENAI_API_KEY")
                if not key:
                    logger.warning("OPENAI_API_KEY not set — AI features degraded to rule-based")
                    return None
                openai.api_key = key
                self._ai = openai
            except ImportError:
                logger.warning("openai package not installed")
        return self._ai

    def _load_system_prompt(self) -> str:
        """Load and cache the invoice_generation.txt prompt."""
        if self._system_prompt is None:
            try:
                with open(self._prompt_path, encoding="utf-8") as f:
                    raw = f.read()
                self._system_prompt = raw.replace(
                    "{today_date}", datetime.utcnow().strftime("%Y-%m-%d")
                )
            except FileNotFoundError:
                logger.warning("Prompt file not found at %s — using inline fallback", self._prompt_path)
                self._system_prompt = _INLINE_SYSTEM_PROMPT.replace(
                    "{today_date}", datetime.utcnow().strftime("%Y-%m-%d")
                )
        return self._system_prompt

    # ------------------------------------------------------------------
    # 1. NATURAL LANGUAGE INVOICE GENERATOR  (main entry point)
    # ------------------------------------------------------------------

    def generate_invoice_from_prompt(self, prompt: str) -> dict:
        """
        Convert a natural language prompt into a fully structured invoice.

        Full pipeline
        -------------
        Prompt → LLM extraction → validation → auto-fill →
        calculations → risk analysis → enhancement → final invoice

        Parameters
        ----------
        prompt : Free-form text, e.g.
            "Create invoice for Netflix for AI consulting, ₹1.5 lakh, due next Friday."

        Returns
        -------
        {
            "invoice"         : {...},   # Complete structured invoice
            "validation"      : {...},   # Validation results and warnings
            "risk_analysis"   : {...},   # Client risk + collection probability
            "suggestions"     : [...],   # Smart next-action suggestions
            "ai_enhanced"     : bool,    # Whether GPT was used
        }
        """
        logger.info("InvoiceAgent.generate_invoice_from_prompt: %r", prompt[:80])

        # Step 1: Extract raw data from prompt
        raw = self._call_llm(prompt)
        ai_enhanced = bool(raw.get("_ai_used"))

        # Step 2: Validate
        validation = self.validate_invoice_data(raw)

        # Step 3: Auto-fill missing fields
        filled = self.auto_fill_missing_fields(raw)

        # Step 4: Recalculate totals
        filled = self.calculate_invoice_totals(filled)

        # Step 5: Assign invoice number
        filled.setdefault("number", self._generate_invoice_number())
        filled.setdefault("id", _new_id())
        filled.setdefault("status", "draft")
        filled.setdefault("source", "ai_agent")
        filled.setdefault("created_by", self.user_id)
        filled["created_at"] = _now().isoformat()

        # Step 6: Risk analysis
        risk = self.inject_client_risk_analysis(filled)
        filled["risk_analysis"] = risk

        # Step 7: Enhancement
        filled = self.enhance_invoice(filled)

        # Step 8: Theme and tone
        filled["theme"] = self.recommend_invoice_theme(filled)
        filled["tone"] = self.optimize_invoice_tone(filled).get("tone", InvoiceTone.PROFESSIONAL)

        # Step 9: Recurring suggestion
        recurring_suggestion = self.suggest_recurring_invoice(filled)
        filled["recurring_suggestion"] = recurring_suggestion

        # Step 10: Smart suggestions
        suggestions = self._build_final_suggestions(filled, risk, recurring_suggestion)

        return {
            "invoice": filled,
            "validation": validation,
            "risk_analysis": risk,
            "suggestions": suggestions,
            "ai_enhanced": ai_enhanced,
        }

    # ------------------------------------------------------------------
    # LLM CALL + FALLBACK
    # ------------------------------------------------------------------

    def _call_llm(self, prompt: str) -> dict:
        """Call the LLM with the invoice generation prompt, with rule-based fallback."""
        ai = self._get_ai()
        system = self._load_system_prompt()

        if not ai:
            data = self._rule_based_extraction(prompt)
            data["_ai_used"] = False
            return data

        try:
            resp = ai.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=600,
                response_format={"type": "json_object"},
            )
            raw = json.loads(resp.choices[0].message.content)
            raw["_ai_used"] = True
            return self._clean_invoice_json(raw)
        except Exception as exc:
            logger.warning("LLM call failed: %s — falling back to rule-based", exc)
            data = self._rule_based_extraction(prompt)
            data["_ai_used"] = False
            return data

    def _clean_invoice_json(self, raw: dict) -> dict:
        """Normalise and sanitise LLM response."""
        # Ensure numeric types
        for field in ("subtotal", "tax_rate", "tax_amount", "total", "amount"):
            if field in raw:
                try:
                    raw[field] = float(raw[field])
                except (TypeError, ValueError):
                    raw.pop(field, None)

        # Ensure items is a list
        if not isinstance(raw.get("items"), list):
            raw["items"] = []

        # Clean item amounts
        for item in raw.get("items", []):
            for k in ("quantity", "rate", "amount"):
                if k in item:
                    try:
                        item[k] = float(item[k])
                    except (TypeError, ValueError):
                        item[k] = 0.0

        # Remove internal LLM keys
        raw.pop("_ai_used", None)

        return raw

    # ------------------------------------------------------------------
    # 2. AI INVOICE DESCRIPTION GENERATOR
    # ------------------------------------------------------------------

    def generate_invoice_description(self, items: list[dict], client_name: str = "") -> str:
        """
        Generate a professional one-sentence invoice description from line items.

        Parameters
        ----------
        items       : Invoice line items with 'description' keys.
        client_name : Optional client name for personalisation.

        Returns
        -------
        Professional description string.
        """
        ai = self._get_ai()
        item_labels = [i.get("description", "") for i in items if i.get("description")]

        if not item_labels:
            return "Professional services rendered as per agreement."

        if not ai:
            joined = " and ".join(item_labels[:3])
            suffix = f" for {client_name}" if client_name else ""
            return f"Professional {joined.lower()} services delivered{suffix}."

        try:
            prompt = (
                f"Write a single professional invoice description sentence for these services: "
                f"{', '.join(item_labels)}."
                + (f" Client: {client_name}." if client_name else "")
                + " Use formal business language. Max 20 words."
            )
            resp = ai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=60,
            )
            return resp.choices[0].message.content.strip().strip('"')
        except Exception as exc:
            logger.warning("Description generation failed: %s", exc)
            return f"Professional {', '.join(item_labels[:2]).lower()} services."

    # ------------------------------------------------------------------
    # 3. SMART INVOICE ITEM EXTRACTION
    # ------------------------------------------------------------------

    def extract_invoice_items(self, text: str) -> list[dict]:
        """
        Extract structured line items from a free-form text description.

        Handles quantity × rate patterns, vague service names,
        and implicit amounts.

        Returns
        -------
        List of item dicts: {description, quantity, rate, amount}
        """
        ai = self._get_ai()

        if ai:
            try:
                prompt = (
                    f"Extract invoice line items from this text: \"{text}\"\n"
                    "Return JSON: {\"items\": [{\"description\": str, \"quantity\": num, \"rate\": num, \"amount\": num}]}\n"
                    "k/K = ×1000, lakh = ×100000. If total given with multiple items, split proportionally."
                )
                resp = ai.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=300,
                    response_format={"type": "json_object"},
                )
                data = json.loads(resp.choices[0].message.content)
                return self._clean_invoice_json({"items": data.get("items", [])})["items"]
            except Exception as exc:
                logger.warning("Item extraction LLM failed: %s", exc)

        return self._rule_based_item_extraction(text)

    def _rule_based_item_extraction(self, text: str) -> list[dict]:
        """Regex-based item extraction fallback."""
        items = []

        # "N [service] at/for X each/each"
        pattern = re.findall(
            r"(\d+(?:\.\d+)?)\s+([a-zA-Z][a-zA-Z\s]{2,30}?)\s+(?:at|for|@)\s+([\d,]+(?:\.\d+)?)\s*(?:k|thousand|each)?",
            text, re.IGNORECASE,
        )
        for qty, desc, rate in pattern:
            qty_f = float(qty)
            rate_f = float(rate.replace(",", ""))
            if "k" in text.lower() or "thousand" in text.lower():
                rate_f *= 1000
            items.append({
                "description": desc.strip().title(),
                "quantity": qty_f,
                "rate": rate_f,
                "amount": round(qty_f * rate_f, 2),
            })

        # Fallback: one generic item
        if not items:
            amount = self._extract_amount(text)
            items = [{
                "description": "Professional Services",
                "quantity": 1.0,
                "rate": amount,
                "amount": amount,
            }]

        return items

    # ------------------------------------------------------------------
    # 4. AUTOMATIC TOTAL CALCULATOR
    # ------------------------------------------------------------------

    def calculate_invoice_totals(self, invoice: dict) -> dict:
        """
        Compute subtotal, tax_amount, total, and balance_due from line items.

        If 'total' is already set and items are empty, preserves the total.
        Always reconciles: subtotal + tax_amount = total.

        Returns
        -------
        Invoice dict with subtotal, tax_rate, tax_amount, total, balance_due.
        """
        items = invoice.get("items", [])

        if items:
            subtotal = sum(
                float(i.get("quantity", 1)) * float(i.get("rate", 0))
                for i in items
            )
            # Recalculate per-item amounts for consistency
            for item in items:
                item["amount"] = round(float(item.get("quantity", 1)) * float(item.get("rate", 0)), 2)
        else:
            subtotal = float(invoice.get("subtotal") or invoice.get("total") or 0)

        tax_rate = float(invoice.get("tax_rate") or 0)
        discount = float(invoice.get("discount") or 0)
        tax_amount = round(subtotal * tax_rate / 100, 2)
        total = round(subtotal - discount + tax_amount, 2)
        amount_paid = float(invoice.get("amount_paid") or 0)
        balance_due = round(total - amount_paid, 2)

        invoice.update({
            "subtotal": round(subtotal, 2),
            "tax_rate": tax_rate,
            "tax_amount": tax_amount,
            "discount": discount,
            "total": total,
            "amount_paid": amount_paid,
            "balance_due": balance_due,
            "items": items,
        })
        return invoice

    # ------------------------------------------------------------------
    # 5. AI PRIORITY DETECTION
    # ------------------------------------------------------------------

    def detect_invoice_priority(self, invoice: dict) -> str:
        """
        Classify invoice priority: low | medium | high | urgent.

        Factors
        -------
        - Invoice total amount
        - Due date proximity
        - Urgency keywords in notes/description
        - Client type (enterprise signals)
        - Client risk score

        Returns
        -------
        Priority string.
        """
        total = float(invoice.get("total") or invoice.get("amount") or 0)
        due_date_str = invoice.get("due_date")
        client = str(invoice.get("client_name") or invoice.get("client") or "").lower()
        notes = str(invoice.get("notes") or invoice.get("description") or "").lower()
        risk = str(invoice.get("risk_analysis", {}).get("risk_level") or "low").lower()

        # Urgency keywords
        if any(kw in notes for kw in ("urgent", "asap", "immediately", "critical", "rush")):
            return InvoicePriority.URGENT

        # Due date proximity
        days_until_due = None
        if due_date_str:
            try:
                due_dt = datetime.fromisoformat(due_date_str)
                days_until_due = (due_dt - _now()).days
                if days_until_due <= 2:
                    return InvoicePriority.URGENT
                if days_until_due <= 7:
                    return InvoicePriority.HIGH
            except ValueError:
                pass

        # Amount thresholds (INR defaults; scale for other currencies)
        currency = invoice.get("currency", "INR")
        high_threshold = 100000 if currency == "INR" else 1200
        medium_threshold = 25000 if currency == "INR" else 300
        urgent_threshold = 500000 if currency == "INR" else 5000

        if total >= urgent_threshold and days_until_due is not None and days_until_due <= 3:
            return InvoicePriority.URGENT
        if total >= high_threshold:
            return InvoicePriority.HIGH
        if total >= medium_threshold:
            return InvoicePriority.MEDIUM

        # Enterprise client signal
        enterprise_signals = ("corp", "ltd", "inc", "technologies", "solutions", "group", "enterprise")
        if any(s in client for s in enterprise_signals):
            return InvoicePriority.HIGH

        # Client risk
        if risk in ("high", "critical"):
            return InvoicePriority.HIGH

        return InvoicePriority.LOW

    # ------------------------------------------------------------------
    # 6. INVOICE VALIDATION ENGINE
    # ------------------------------------------------------------------

    def validate_invoice_data(self, invoice: dict) -> dict:
        """
        Run comprehensive validation checks on an invoice dict.

        Checks
        ------
        - Required fields present
        - Non-negative and realistic amounts
        - Item totals reconcile with subtotal
        - Due date is valid and in the future
        - No duplicate item descriptions
        - Currency is supported

        Returns
        -------
        {
            "valid": True | False,
            "errors": [...],
            "warnings": [...]
        }
        """
        errors: list[str] = []
        warnings: list[str] = []

        # Client name
        client = invoice.get("client_name") or invoice.get("client")
        if not client:
            errors.append("Client name is required.")

        # Items
        items = invoice.get("items", [])
        if not items:
            warnings.append("No line items found — invoice may be incomplete.")

        # Totals
        total = float(invoice.get("total") or 0)
        subtotal = float(invoice.get("subtotal") or 0)

        if total < 0:
            errors.append("Invoice total cannot be negative.")
        if total == 0:
            warnings.append("Invoice total is zero — please verify the amount.")
        if total > 10_000_000:
            warnings.append("Invoice total is unusually large (> ₹1 crore). Please verify.")

        # Tax rate
        tax_rate = float(invoice.get("tax_rate") or 0)
        if tax_rate > 30:
            warnings.append(f"Tax rate of {tax_rate}% is unusually high. Please verify.")

        # Item sum reconciliation
        if items and subtotal > 0:
            item_sum = sum(float(i.get("amount") or (float(i.get("quantity", 1)) * float(i.get("rate", 0)))) for i in items)
            if abs(item_sum - subtotal) > 1:
                warnings.append(f"Item totals (${item_sum:,.2f}) do not match subtotal (${subtotal:,.2f}).")

        # Due date
        due_str = invoice.get("due_date")
        if due_str:
            try:
                due_dt = datetime.fromisoformat(due_str)
                if due_dt.date() < _now().date():
                    warnings.append("Due date is in the past.")
            except ValueError:
                errors.append(f"Invalid due date format: {due_str!r}. Use YYYY-MM-DD.")
        else:
            warnings.append("No due date specified — defaulting to Net 30.")

        # Duplicate items
        descriptions = [i.get("description", "").lower() for i in items if i.get("description")]
        if len(descriptions) != len(set(descriptions)):
            warnings.append("Possible duplicate line items detected.")

        # Currency
        currency = invoice.get("currency", "INR")
        if currency.upper() not in SUPPORTED_CURRENCIES:
            errors.append(f"Unsupported currency: {currency!r}. Supported: {', '.join(SUPPORTED_CURRENCIES)}.")

        # Negative item rates
        for i, item in enumerate(items):
            if float(item.get("rate") or 0) < 0:
                errors.append(f"Item {i+1} has a negative rate.")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # 7. AI AUTO-FILL MISSING FIELDS
    # ------------------------------------------------------------------

    def auto_fill_missing_fields(self, invoice: dict) -> dict:
        """
        Intelligently fill in missing invoice fields using context and defaults.

        Fields auto-filled
        ------------------
        due_date, notes, payment_terms, description, currency, tax_rate,
        priority, items (if empty with a total provided)
        """
        # Due date
        if not invoice.get("due_date"):
            invoice["due_date"] = self.recommend_due_date(invoice)

        # Currency
        if not invoice.get("currency"):
            invoice["currency"] = "INR"

        # Payment terms
        if not invoice.get("payment_terms"):
            invoice["payment_terms"] = self.generate_payment_terms(invoice)

        # Notes
        if not invoice.get("notes"):
            invoice["notes"] = self.generate_invoice_notes(
                client_name=invoice.get("client_name") or invoice.get("client", ""),
                tone=invoice.get("tone", InvoiceTone.PROFESSIONAL),
            )

        # Description
        if not invoice.get("description"):
            invoice["description"] = self.generate_invoice_description(
                items=invoice.get("items", []),
                client_name=invoice.get("client_name") or invoice.get("client", ""),
            )

        # Priority
        if not invoice.get("priority"):
            invoice["priority"] = self.detect_invoice_priority(invoice)

        # Tax rate (auto-detect from currency if not set)
        if not invoice.get("tax_rate"):
            tax_info = self.detect_tax_requirements(invoice.get("currency", "INR"))
            invoice["tax_rate"] = 0  # Default 0 — let user opt in to tax

        # Fallback items if total is set but items are empty
        if not invoice.get("items") and (invoice.get("total") or invoice.get("amount")):
            total = float(invoice.get("total") or invoice.get("amount") or 0)
            invoice["items"] = [{
                "description": invoice.get("description") or "Professional Services",
                "quantity": 1.0,
                "rate": total,
                "amount": total,
            }]

        return invoice

    # ------------------------------------------------------------------
    # 8. SMART DUE DATE RECOMMENDATION
    # ------------------------------------------------------------------

    def recommend_due_date(self, invoice: dict) -> str:
        """
        Recommend an appropriate due date based on context.

        Logic
        -----
        - Large amount (> ₹1L) → Net 15 (sooner, protects cashflow)
        - Enterprise client → Net 30 (industry standard)
        - Recurring invoice → Net 7 (tight recurring cycle)
        - Startup/low-risk  → Net 30
        - Default           → Net 30

        Returns
        -------
        ISO date string (YYYY-MM-DD).
        """
        now = _now()
        total = float(invoice.get("total") or invoice.get("amount") or 0)
        currency = invoice.get("currency", "INR")
        client = str(invoice.get("client_name") or invoice.get("client") or "").lower()
        description = str(invoice.get("description") or "").lower()

        high_threshold = 100000 if currency == "INR" else 1200

        enterprise = any(s in client for s in ("corp", "ltd", "inc", "technologies", "solutions"))
        recurring = any(kw in description for kw in ("retainer", "monthly", "subscription", "recurring"))

        if recurring:
            days = 7
        elif total > high_threshold:
            days = 15
        elif enterprise:
            days = 30
        else:
            days = 30

        return (now + timedelta(days=days)).strftime("%Y-%m-%d")

    # ------------------------------------------------------------------
    # 9. AI PAYMENT TERMS GENERATOR
    # ------------------------------------------------------------------

    def generate_payment_terms(self, invoice: dict) -> str:
        """
        Generate appropriate payment terms based on invoice context.

        Returns strings like: "Net 7", "Net 15", "Net 30", "Due on receipt",
        "50% advance, 50% on delivery" for large milestone projects.
        """
        total = float(invoice.get("total") or invoice.get("amount") or 0)
        due_str = invoice.get("due_date")
        currency = invoice.get("currency", "INR")
        large_threshold = 500000 if currency == "INR" else 5000

        if due_str:
            try:
                due_dt = datetime.fromisoformat(due_str)
                days = (due_dt.date() - _now().date()).days
                if days <= 0:
                    return "Due on receipt"
                if days <= 7:
                    return "Net 7"
                if days <= 15:
                    return "Net 15"
                if days <= 30:
                    return "Net 30"
                if days <= 45:
                    return "Net 45"
                return "Net 60"
            except ValueError:
                pass

        if total >= large_threshold:
            return "50% advance, balance due on delivery"

        return "Net 30"

    # ------------------------------------------------------------------
    # 10. AI NOTES GENERATOR
    # ------------------------------------------------------------------

    def generate_invoice_notes(
        self,
        client_name: str = "",
        tone: str = InvoiceTone.PROFESSIONAL,
    ) -> str:
        """
        Generate a professional, tone-matched payment note for the invoice.

        Tone variants
        -------------
        premium    : Formal, high-end language
        friendly   : Warm, approachable
        executive  : Concise and corporate
        startup    : Modern, casual-professional
        """
        notes_by_tone = {
            InvoiceTone.PREMIUM: (
                f"Thank you for your valued partnership{', ' + client_name if client_name else ''}. "
                "We trust the services delivered have met your expectations. "
                "Kindly arrange payment by the due date stated above. "
                "Please do not hesitate to contact us for any queries regarding this invoice."
            ),
            InvoiceTone.FRIENDLY: (
                f"Thanks so much for working with us{', ' + client_name if client_name else ''}! "
                "We really appreciate your business. "
                "Please complete payment by the due date — feel free to reach out if you have any questions."
            ),
            InvoiceTone.EXECUTIVE: (
                "Payment due by the date indicated above. "
                "For billing queries, contact our accounts team. "
                "We appreciate your prompt attention to this invoice."
            ),
            InvoiceTone.STARTUP: (
                f"Hey{', ' + client_name if client_name else ''}! "
                "Thanks for the project — loved working on it. "
                "Please process payment by the due date. Ping us if anything needs clarification!"
            ),
        }
        return notes_by_tone.get(tone, notes_by_tone[InvoiceTone.PROFESSIONAL])

    # ------------------------------------------------------------------
    # 11. MULTI-CURRENCY OPTIMISATION
    # ------------------------------------------------------------------

    def optimize_currency_display(self, invoice: dict) -> dict:
        """
        Normalise and format currency fields for display.

        Adds: currency_symbol, formatted_total, formatted_subtotal,
        formatted_tax_amount to the invoice dict.
        """
        currency = self._validate_currency(invoice.get("currency", "INR"))
        invoice["currency"] = currency
        symbol = CURRENCY_SYMBOLS.get(currency, currency)
        invoice["currency_symbol"] = symbol

        def fmt(amount: float) -> str:
            if currency == "INR":
                # Indian number format: ₹1,50,000
                s = f"{amount:,.0f}"
                parts = s.split(",")
                if len(parts) > 2:
                    first = parts[0]
                    rest = ",".join(parts[1:])
                    s = f"{first},{rest}"
                return f"{symbol}{s}"
            return f"{symbol}{amount:,.2f}"

        invoice["formatted_total"] = fmt(float(invoice.get("total") or 0))
        invoice["formatted_subtotal"] = fmt(float(invoice.get("subtotal") or 0))
        invoice["formatted_tax_amount"] = fmt(float(invoice.get("tax_amount") or 0))
        invoice["formatted_balance_due"] = fmt(float(invoice.get("balance_due") or invoice.get("total") or 0))

        return invoice

    # ------------------------------------------------------------------
    # 12. RECURRING INVOICE SUGGESTION
    # ------------------------------------------------------------------

    def suggest_recurring_invoice(self, invoice: dict) -> dict:
        """
        Detect whether this invoice should be converted to a recurring schedule.

        Signals
        -------
        - Description contains: retainer, monthly, subscription, maintenance, hosting
        - Invoice title or notes contain recurring keywords
        - Client has sent similar invoices before (placeholder — extend with DB query)

        Returns
        -------
        {
            "should_recur": True,
            "suggested_frequency": "monthly",
            "reason": "Retainer service detected",
            "suggested_cta": "Convert to monthly recurring invoice"
        }
        """
        desc = str(invoice.get("description") or "").lower()
        notes = str(invoice.get("notes") or "").lower()
        items_text = " ".join(i.get("description", "").lower() for i in invoice.get("items", []))
        combined = f"{desc} {notes} {items_text}"

        recurring_signals = {
            "monthly retainer": ("monthly", "Retainer service detected"),
            "retainer": ("monthly", "Retainer service detected"),
            "subscription": ("monthly", "Subscription service detected"),
            "monthly": ("monthly", "Monthly service pattern detected"),
            "maintenance": ("monthly", "Maintenance contract detected"),
            "hosting": ("monthly", "Hosting service — typically billed monthly"),
            "support": ("monthly", "Support contract — typically recurring"),
            "weekly": ("weekly", "Weekly service pattern detected"),
            "quarterly": ("quarterly", "Quarterly billing pattern detected"),
            "annual": ("yearly", "Annual contract detected"),
            "yearly": ("yearly", "Annual contract detected"),
        }

        for keyword, (frequency, reason) in recurring_signals.items():
            if keyword in combined:
                return {
                    "should_recur": True,
                    "suggested_frequency": frequency,
                    "reason": reason,
                    "suggested_cta": f"Convert to {frequency} recurring invoice",
                }

        return {
            "should_recur": False,
            "suggested_frequency": None,
            "reason": None,
            "suggested_cta": None,
        }

    # ------------------------------------------------------------------
    # 13. AI CLIENT RISK AWARENESS
    # ------------------------------------------------------------------

    def inject_client_risk_analysis(self, invoice: dict) -> dict:
        """
        Assess collection risk for this invoice based on available signals.

        Factors
        -------
        - Invoice amount (higher = more risk)
        - Due date proximity
        - Client keywords (new, enterprise, etc.)
        - High-risk keywords in notes

        Returns
        -------
        {
            "risk_level": "low | medium | high | critical",
            "overdue_probability": "low | medium | high",
            "collection_difficulty": "easy | moderate | hard | very_hard",
            "recommended_tone": "friendly | professional | firm",
            "notes": "..."
        }
        """
        total = float(invoice.get("total") or 0)
        currency = invoice.get("currency", "INR")
        client = str(invoice.get("client_name") or invoice.get("client") or "").lower()
        notes = str(invoice.get("notes") or invoice.get("description") or "").lower()
        due_str = invoice.get("due_date")

        risk_score = 0

        # Amount risk
        high_threshold = 100000 if currency == "INR" else 1200
        if total > high_threshold * 5:
            risk_score += 3
        elif total > high_threshold:
            risk_score += 2
        elif total > high_threshold * 0.25:
            risk_score += 1

        # High-risk keywords
        if any(kw in notes for kw in HIGH_RISK_KEYWORDS):
            risk_score += 2

        # New client (no payment history signal)
        if any(kw in client for kw in ("new", "first", "trial")):
            risk_score += 1

        # Enterprise clients often pay slower
        if any(s in client for s in ("corp", "ltd", "inc", "enterprise")):
            risk_score += 1

        # Due date proximity risk
        if due_str:
            try:
                days = (datetime.fromisoformat(due_str).date() - _now().date()).days
                if days <= 3:
                    risk_score += 2
                elif days <= 7:
                    risk_score += 1
            except ValueError:
                pass

        if risk_score >= 6:
            level, overdue_prob, difficulty, tone = "critical", "high", "very_hard", "firm"
        elif risk_score >= 4:
            level, overdue_prob, difficulty, tone = "high", "high", "hard", "firm"
        elif risk_score >= 2:
            level, overdue_prob, difficulty, tone = "medium", "medium", "moderate", "professional"
        else:
            level, overdue_prob, difficulty, tone = "low", "low", "easy", "friendly"

        notes_text = {
            "critical": "High-value invoice with multiple risk signals. Set up escalation workflow immediately.",
            "high": "Elevated risk — enable automated reminder sequence from day 1.",
            "medium": "Moderate risk — send friendly reminder 3 days before due date.",
            "low": "Low risk — standard reminder on due date is sufficient.",
        }[level]

        return {
            "risk_level": level,
            "overdue_probability": overdue_prob,
            "collection_difficulty": difficulty,
            "recommended_reminder_tone": tone,
            "notes": notes_text,
            "risk_score": risk_score,
        }

    # ------------------------------------------------------------------
    # 14. INVOICE THEME RECOMMENDATION
    # ------------------------------------------------------------------

    def recommend_invoice_theme(self, invoice: dict) -> str:
        """
        Recommend an invoice theme based on client type and invoice value.

        Returns
        -------
        InvoiceTheme value string.
        """
        client = str(invoice.get("client_name") or invoice.get("client") or "").lower()
        total = float(invoice.get("total") or 0)
        currency = invoice.get("currency", "INR")
        priority = invoice.get("priority", InvoicePriority.LOW)

        enterprise = any(s in client for s in ("corp", "ltd", "inc", "enterprise", "technologies"))
        high_value = total > (500000 if currency == "INR" else 5000)

        if high_value or priority in (InvoicePriority.URGENT, InvoicePriority.HIGH):
            if enterprise:
                return InvoiceTheme.CORPORATE
            return InvoiceTheme.DARK_PREMIUM

        if enterprise:
            return InvoiceTheme.ELEGANT

        startup_signals = ("startup", "tech", "app", "digital", "studio", "labs", "io")
        if any(s in client for s in startup_signals):
            return InvoiceTheme.MODERN_GLASS

        return InvoiceTheme.STARTUP

    # ------------------------------------------------------------------
    # 15. AI TONE OPTIMISATION
    # ------------------------------------------------------------------

    def optimize_invoice_tone(self, invoice: dict) -> dict:
        """
        Select and apply the optimal tone for invoice communication.

        Returns
        -------
        {
            "tone": "premium | friendly | executive | startup",
            "rationale": "..."
        }
        """
        client = str(invoice.get("client_name") or invoice.get("client") or "").lower()
        total = float(invoice.get("total") or 0)
        priority = invoice.get("priority", InvoicePriority.LOW)
        currency = invoice.get("currency", "INR")
        large = total > (500000 if currency == "INR" else 5000)

        enterprise = any(s in client for s in ("corp", "ltd", "inc", "enterprise", "solutions"))
        startup = any(s in client for s in ("startup", "labs", "studio", "io", "tech"))

        if large and enterprise:
            return {"tone": InvoiceTone.EXECUTIVE, "rationale": "Large enterprise invoice warrants executive tone."}
        if large:
            return {"tone": InvoiceTone.PREMIUM, "rationale": "High-value invoice — premium tone builds confidence."}
        if startup:
            return {"tone": InvoiceTone.STARTUP, "rationale": "Startup client — modern friendly tone preferred."}
        if enterprise:
            return {"tone": InvoiceTone.EXECUTIVE, "rationale": "Enterprise client — professional corporate tone."}
        return {"tone": InvoiceTone.FRIENDLY, "rationale": "Standard client — warm, professional tone."}

    # ------------------------------------------------------------------
    # 16. VOICE INVOICE PARSING
    # ------------------------------------------------------------------

    def parse_voice_invoice(self, transcript: str) -> dict:
        """
        Convert a raw voice transcript into a structured invoice.

        Delegates to the full generate_invoice_from_prompt pipeline
        after cleaning the transcript.

        Parameters
        ----------
        transcript : Raw voice-to-text string, possibly with filler words.

        Returns
        -------
        Full invoice generation result dict.
        """
        # Clean transcript filler words
        filler_words = ["um", "uh", "like", "you know", "actually", "basically", "so"]
        cleaned = transcript
        for word in filler_words:
            cleaned = re.sub(rf"\b{word}\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()

        logger.info("Voice invoice parse: %r → %r", transcript[:60], cleaned[:60])

        result = self.generate_invoice_from_prompt(cleaned)
        result["invoice"]["source"] = "voice"
        return result

    # ------------------------------------------------------------------
    # 17. AI INVOICE ENHANCEMENT
    # ------------------------------------------------------------------

    def enhance_invoice(self, invoice: dict) -> dict:
        """
        Improve an existing invoice's professional quality.

        Improvements
        ------------
        - Capitalise item descriptions
        - Ensure description is professional
        - Format currency display
        - Add missing payment terms
        - Ensure notes are warm and professional
        - Trim whitespace from all string fields

        Returns
        -------
        Enhanced invoice dict.
        """
        # Clean string fields
        for field in ("client_name", "client", "description", "notes", "payment_terms"):
            if invoice.get(field) and isinstance(invoice[field], str):
                invoice[field] = invoice[field].strip()

        # Capitalise item descriptions
        for item in invoice.get("items", []):
            if item.get("description"):
                item["description"] = item["description"].strip().title()

        # Ensure description is professional-grade
        if invoice.get("description") and len(invoice["description"]) < 15:
            invoice["description"] = self.generate_invoice_description(
                invoice.get("items", []),
                client_name=invoice.get("client_name") or invoice.get("client", ""),
            )

        # Currency display
        invoice = self.optimize_currency_display(invoice)

        # Missing payment terms
        if not invoice.get("payment_terms"):
            invoice["payment_terms"] = self.generate_payment_terms(invoice)

        return invoice

    # ------------------------------------------------------------------
    # 18. SMART TAX DETECTION
    # ------------------------------------------------------------------

    def detect_tax_requirements(self, currency: str) -> dict:
        """
        Return applicable tax rules for the given currency/region.

        Returns
        -------
        {
            "tax_name": "GST",
            "default_rate": 18.0,
            "available_rates": [0, 5, 12, 18, 28],
            "note": "..."
        }
        """
        currency = self._validate_currency(currency)
        rules = TAX_RULES.get(currency, {"name": "Tax", "default_rate": 0.0, "rates": []})

        notes_map = {
            "INR": "GST applies. Standard rates: 0%, 5%, 12%, 18%, 28%. B2B invoices require GSTIN.",
            "USD": "US sales tax varies by state. Typically 0% for B2B services.",
            "EUR": "EU VAT applies. Reverse charge may apply for B2B cross-border transactions.",
            "GBP": "UK VAT applies. Standard rate 20%. Check MTD compliance for registered businesses.",
            "SGD": "Singapore GST 9% applies for GST-registered businesses.",
            "AED": "UAE VAT 5% applies for VAT-registered businesses.",
            "JPY": "Japan Consumption Tax 10% (8% for food). Required for registered businesses.",
        }

        return {
            "tax_name": rules["name"],
            "default_rate": rules["default_rate"],
            "available_rates": rules["rates"],
            "note": notes_map.get(currency, ""),
        }

    # ------------------------------------------------------------------
    # 19. AI FRAUD / ANOMALY DETECTION
    # ------------------------------------------------------------------

    def detect_invoice_anomalies(self, invoice: dict) -> dict:
        """
        Detect suspicious patterns or data errors in an invoice.

        Checks
        ------
        - Unusually large amounts
        - Negative amounts
        - Zero-amount items
        - Abnormal tax rates
        - Due date in the distant future (> 180 days)
        - Duplicate item descriptions
        - Missing client with large amount

        Returns
        -------
        {
            "anomalies_detected": True | False,
            "flags": [{"type": "...", "severity": "...", "detail": "..."}]
        }
        """
        flags = []
        total = float(invoice.get("total") or 0)
        currency = invoice.get("currency", "INR")
        items = invoice.get("items", [])
        client = invoice.get("client_name") or invoice.get("client")
        tax_rate = float(invoice.get("tax_rate") or 0)

        large_threshold = 10_000_000 if currency == "INR" else 100_000
        if total > large_threshold:
            flags.append({"type": "large_amount", "severity": "high",
                          "detail": f"Invoice total {total:,.2f} {currency} is unusually large."})

        if total < 0:
            flags.append({"type": "negative_total", "severity": "critical",
                          "detail": "Invoice total is negative."})

        if total == 0:
            flags.append({"type": "zero_total", "severity": "medium",
                          "detail": "Invoice total is zero."})

        if tax_rate > 30:
            flags.append({"type": "high_tax", "severity": "medium",
                          "detail": f"Tax rate of {tax_rate}% is unusually high."})

        for i, item in enumerate(items):
            if float(item.get("rate") or 0) < 0:
                flags.append({"type": "negative_item", "severity": "high",
                              "detail": f"Item {i+1} ({item.get('description')}) has a negative rate."})
            if float(item.get("amount") or 0) == 0:
                flags.append({"type": "zero_item", "severity": "low",
                              "detail": f"Item {i+1} ({item.get('description')}) has a zero amount."})

        desc_list = [i.get("description", "").lower() for i in items if i.get("description")]
        if len(desc_list) != len(set(desc_list)):
            flags.append({"type": "duplicate_items", "severity": "medium",
                          "detail": "Duplicate line item descriptions detected."})

        if not client and total > (50000 if currency == "INR" else 600):
            flags.append({"type": "missing_client_high_value", "severity": "high",
                          "detail": "High-value invoice has no client name specified."})

        due_str = invoice.get("due_date")
        if due_str:
            try:
                days = (datetime.fromisoformat(due_str).date() - _now().date()).days
                if days > 180:
                    flags.append({"type": "distant_due_date", "severity": "low",
                                  "detail": f"Due date is {days} days away — unusually far in the future."})
                if days < 0:
                    flags.append({"type": "past_due_date", "severity": "medium",
                                  "detail": f"Due date is {abs(days)} days in the past."})
            except ValueError:
                flags.append({"type": "invalid_date", "severity": "medium",
                              "detail": f"Due date {due_str!r} is not a valid ISO date."})

        return {
            "anomalies_detected": len(flags) > 0,
            "flags": flags,
            "highest_severity": (
                "critical" if any(f["severity"] == "critical" for f in flags) else
                "high" if any(f["severity"] == "high" for f in flags) else
                "medium" if any(f["severity"] == "medium" for f in flags) else
                "low" if flags else "none"
            ),
        }

    # ------------------------------------------------------------------
    # 20. ONE-CLICK SMART INVOICE DUPLICATION
    # ------------------------------------------------------------------

    def smart_duplicate_invoice(self, original: dict) -> dict:
        """
        Intelligently duplicate an invoice with updated dates and numbering.

        Updates
        -------
        - New invoice ID and number
        - Issue date → today
        - Due date → same offset from today as original
        - Status reset to 'draft'
        - Clears amount_paid and balance_due
        - Recalculates recurring fields

        Returns
        -------
        New invoice dict ready for saving.
        """
        import copy
        duplicate = copy.deepcopy(original)

        # New identity
        duplicate["id"] = _new_id()
        duplicate["number"] = self._generate_invoice_number()
        duplicate["status"] = "draft"
        duplicate["source"] = "duplicated"
        duplicate["created_at"] = _now().isoformat()
        duplicate["amount_paid"] = 0
        duplicate["balance_due"] = duplicate.get("total", 0)

        # Recalculate due date preserving the original interval
        original_created = original.get("created_at")
        original_due = original.get("due_date")
        if original_created and original_due:
            try:
                orig_dt = datetime.fromisoformat(original_created[:10])
                due_dt = datetime.fromisoformat(original_due)
                interval = (due_dt - orig_dt).days
                duplicate["due_date"] = (_now() + timedelta(days=interval)).strftime("%Y-%m-%d")
            except ValueError:
                duplicate["due_date"] = self.recommend_due_date(duplicate)
        else:
            duplicate["due_date"] = self.recommend_due_date(duplicate)

        # Update payment terms to match new due date
        duplicate["payment_terms"] = self.generate_payment_terms(duplicate)

        # Strip old risk analysis — will be recalculated when needed
        duplicate.pop("risk_analysis", None)

        logger.info("Smart duplicate: %s → %s", original.get("number"), duplicate["number"])

        return duplicate

    # ==================================================================
    # INTERNAL HELPERS
    # ==================================================================

    def _build_prompt(self, user_input: str, context: dict | None = None) -> str:
        """Build the user message for the LLM call."""
        msg = user_input
        if context:
            msg += f"\n\nAdditional context: {json.dumps(context)}"
        return msg

    def _parse_llm_response(self, content: str) -> dict:
        """Parse and clean a raw LLM JSON response string."""
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Strip markdown fences if present
            match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", content)
            if match:
                return json.loads(match.group(1))
            raise

    def _validate_currency(self, currency: str) -> str:
        """Validate and normalise a currency code."""
        normalised = str(currency or "INR").upper().strip()
        return normalised if normalised in SUPPORTED_CURRENCIES else "INR"

    def _format_invoice_output(self, invoice: dict) -> dict:
        """Apply final formatting and ordering to the invoice dict."""
        key_order = [
            "id", "number", "status", "source", "client_name", "client",
            "currency", "currency_symbol", "items", "subtotal", "discount",
            "tax_rate", "tax_amount", "total", "amount_paid", "balance_due",
            "formatted_total", "formatted_balance_due",
            "due_date", "payment_terms", "priority", "description", "notes",
            "theme", "tone", "risk_analysis", "recurring_suggestion",
            "created_by", "created_at",
        ]
        ordered = {k: invoice[k] for k in key_order if k in invoice}
        # Append any extra keys not in the predefined order
        for k, v in invoice.items():
            if k not in ordered:
                ordered[k] = v
        return ordered

    def _generate_invoice_number(self) -> str:
        """Generate a unique invoice number: INV-YYYYMM-XXXX."""
        suffix = _new_id()[:4].upper()
        return f"{INVOICE_NUMBER_PREFIX}-{_now().strftime('%Y%m')}-{suffix}"

    def _build_final_suggestions(
        self,
        invoice: dict,
        risk: dict,
        recurring: dict,
    ) -> list[str]:
        """Compile smart suggestions based on the generated invoice."""
        suggestions = []

        if recurring.get("should_recur"):
            suggestions.append(recurring["suggested_cta"])

        risk_level = risk.get("risk_level", "low")
        if risk_level in ("high", "critical"):
            suggestions.append("Enable automated reminder workflow from day 1 — client is high risk.")
        else:
            suggestions.append("Set up a reminder 3 days before the due date.")

        priority = invoice.get("priority")
        if priority in (InvoicePriority.HIGH, InvoicePriority.URGENT):
            suggestions.append("Send invoice immediately — high priority requires prompt delivery.")
        else:
            suggestions.append("Send invoice via email after reviewing the preview.")

        suggestions.append("Generate a PDF version for your records.")

        return suggestions[:4]

    def _extract_amount(self, text: str) -> float:
        """Extract the first numeric amount from text with multiplier support."""
        patterns = [
            (r"([\d,]+(?:\.\d+)?)\s*lakh", 100000),
            (r"([\d,]+(?:\.\d+)?)\s*(?:k|thousand|hazaar)", 1000),
            (r"(?:₹|\$|£|€|rs\.?)\s*([\d,]+(?:\.\d+)?)", 1),
            (r"([\d,]+(?:\.\d+)?)\s*(?:rupees?|dollars?|euros?|pounds?)", 1),
            (r"([\d,]+)", 1),
        ]
        for pattern, multiplier in patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                try:
                    return float(m.group(1).replace(",", "")) * multiplier
                except ValueError:
                    continue
        return 0.0

    def _rule_based_extraction(self, text: str) -> dict:
        """Rule-based invoice extraction fallback when LLM is unavailable."""
        # Client: "for [Name]"
        client_match = re.search(
            r"\bfor\s+([A-Z][a-zA-Z\s&\.]{1,40}?)(?:\s+(?:for|due|at|worth|\d|₹|\$|,)|$)",
            text, re.IGNORECASE,
        )
        client = client_match.group(1).strip().title() if client_match else None

        amount = self._extract_amount(text)

        # Currency detection
        currency = "INR"
        if re.search(r"\$|usd|dollar", text, re.IGNORECASE):
            currency = "USD"
        elif re.search(r"£|gbp|pound", text, re.IGNORECASE):
            currency = "GBP"
        elif re.search(r"€|eur|euro", text, re.IGNORECASE):
            currency = "EUR"

        # Due date
        from app.services.voice_service import parse_relative_dates
        due_date = None
        date_phrases = [
            "next monday", "next tuesday", "next wednesday", "next thursday",
            "next friday", "next saturday", "next sunday",
            "tomorrow", "next week", "end of month", "in 7 days", "in 14 days", "in 30 days",
        ]
        for phrase in date_phrases:
            if phrase in text.lower():
                due_date = parse_relative_dates(phrase)
                break

        items = self.extract_invoice_items(text) if amount else []
        if not items and amount:
            items = [{"description": "Professional Services", "quantity": 1.0, "rate": amount, "amount": amount}]

        return {
            "client_name": client,
            "currency": currency,
            "items": items,
            "subtotal": amount,
            "total": amount,
            "due_date": due_date,
            "warnings": [] if client else ["Client name not specified"],
        }


# ===========================================================================
# MODULE-LEVEL CONVENIENCE FUNCTION
# ===========================================================================

def generate_invoice_from_prompt(
    prompt: str,
    *,
    user_id: int | None = None,
    model: str = "gpt-4o",
) -> dict:
    """
    Module-level shortcut for one-shot invoice generation.

    Usage
    -----
    from app.ai.agents.invoice_agent import generate_invoice_from_prompt

    result = generate_invoice_from_prompt(
        "Create invoice for Acme for ₹25,000, UI design, due next Friday.",
        user_id=42,
    )
    """
    agent = InvoiceAgent(user_id=user_id, model=model)
    return agent.generate_invoice_from_prompt(prompt)


# ===========================================================================
# HELPERS
# ===========================================================================

def _now() -> datetime:
    return datetime.utcnow()


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Inline system prompt fallback (used when invoice_generation.txt not found)
# ---------------------------------------------------------------------------

_INLINE_SYSTEM_PROMPT = """
You are an AI invoice generation assistant for InvoiceFlow. Today: {today_date}.
Convert the user's request into a structured invoice JSON with these fields:
client_name, currency (INR/USD/EUR/GBP/SGD/AED),
items ([{description, quantity, rate, amount}]),
subtotal, tax_rate (0 if not mentioned), tax_amount, total,
due_date (YYYY-MM-DD), priority (low/medium/high/urgent),
description (one professional sentence), notes, payment_terms, warnings ([]).
k = ×1000, lakh = ×100000. Return ONLY valid JSON.
"""
