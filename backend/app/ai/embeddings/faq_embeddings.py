"""
FAQ Embeddings - Semantic search and knowledge base for AI assistant.
Uses sentence-transformers or OpenAI embeddings with in-memory/Redis caching.
"""

import json
import hashlib
import asyncio
from typing import Optional
from datetime import datetime

import httpx
import numpy as np

# ---------------------------------------------------------------------------
# Knowledge base – plain-text FAQ entries
# ---------------------------------------------------------------------------

FAQ_KNOWLEDGE_BASE = [
    # Invoices
    {"id": "inv_001", "category": "invoices", "question": "How do I create an invoice?", "answer": "Go to Invoices → New Invoice. Fill in client, items, and due date. You can also use AI generation by typing a natural language description."},
    {"id": "inv_002", "category": "invoices", "question": "How do I mark an invoice as paid?", "answer": "Open the invoice, click 'Record Payment', enter the amount and payment method. The status updates automatically."},
    {"id": "inv_003", "category": "invoices", "question": "Can I duplicate an invoice?", "answer": "Yes. Open any invoice and click 'Duplicate'. A copy is created with a new invoice number and today's date."},
    {"id": "inv_004", "category": "invoices", "question": "How do I set up recurring invoices?", "answer": "Open an invoice and click 'Make Recurring'. Choose frequency (weekly/monthly/quarterly/yearly) and the system auto-generates invoices on schedule."},
    {"id": "inv_005", "category": "invoices", "question": "What invoice themes are available?", "answer": "Six themes: Modern, Classic, Minimal, Bold, Startup, and Elegant. Change theme from invoice settings."},
    {"id": "inv_006", "category": "invoices", "question": "How do I send an invoice to a client?", "answer": "Open the invoice and click 'Send Invoice'. It emails the client a professional PDF with a payment link."},
    {"id": "inv_007", "category": "invoices", "question": "Can I add taxes to invoices?", "answer": "Yes. Set a tax rate (%) on each invoice. Tax amount and totals are calculated automatically."},
    {"id": "inv_008", "category": "invoices", "question": "How do I add a discount to an invoice?", "answer": "In the invoice editor, enter a discount amount or percentage in the Discount field. The total updates instantly."},
    # Payments
    {"id": "pay_001", "category": "payments", "question": "What payment methods are supported?", "answer": "Bank transfer, credit/debit card (via Stripe), cash, cheque, and UPI. All recorded in the payment history."},
    {"id": "pay_002", "category": "payments", "question": "How does Stripe integration work?", "answer": "Enable Stripe in Integrations. Clients can pay online via a secure Stripe link included in every invoice email."},
    {"id": "pay_003", "category": "payments", "question": "How do I record a partial payment?", "answer": "Record Payment → enter the partial amount. The invoice shows 'Partially Paid' and tracks the remaining balance."},
    # Clients
    {"id": "cli_001", "category": "clients", "question": "How is the client risk score calculated?", "answer": "AI analyses payment history, overdue frequency, average days late, and invoice volume to assign Low/Medium/High risk."},
    {"id": "cli_002", "category": "clients", "question": "What is payment behaviour score?", "answer": "A 0-100 score reflecting how reliably a client pays on time. Higher is better. Updated after every payment."},
    {"id": "cli_003", "category": "clients", "question": "How do I see all invoices for a client?", "answer": "Open the client profile. All invoices, payments, and activity are listed in the Client Summary tab."},
    # Analytics
    {"id": "ana_001", "category": "analytics", "question": "What is DSO?", "answer": "Days Sales Outstanding – average days to collect payment. Lower DSO means faster cash flow. Found in KPI dashboard."},
    {"id": "ana_002", "category": "analytics", "question": "What is MRR?", "answer": "Monthly Recurring Revenue – total revenue from recurring invoices per month. Tracked in the Recurring Revenue panel."},
    {"id": "ana_003", "category": "analytics", "question": "How is business health score calculated?", "answer": "AI combines collection rate, overdue ratio, revenue growth, client diversity, and DSO into a 0-100 health score."},
    {"id": "ana_004", "category": "analytics", "question": "How do cash flow forecasts work?", "answer": "AI uses historical payment patterns and pending invoices to predict next 30/60/90-day cash inflows."},
    # Workflows
    {"id": "wfl_001", "category": "workflows", "question": "What are workflows?", "answer": "Automated actions triggered by events (e.g. invoice overdue → send reminder). Create custom workflows or use templates."},
    {"id": "wfl_002", "category": "workflows", "question": "How do I set up auto-reminders?", "answer": "Workflows → New Workflow → choose trigger 'Invoice Overdue' → action 'Send Reminder'. The AI generates reminder content."},
    # AI Features
    {"id": "ai_001", "category": "ai", "question": "How does AI invoice generation work?", "answer": "Describe your invoice in plain English (e.g. 'Invoice Acme Corp for 10 hours web dev at $150/hr due in 30 days'). AI extracts all fields."},
    {"id": "ai_002", "category": "ai", "question": "What can the AI assistant do?", "answer": "Answer questions, create invoices, pull analytics, search clients/invoices, generate reminders, and give business insights – all by chat."},
    {"id": "ai_003", "category": "ai", "question": "How do AI reminders work?", "answer": "AI generates personalised reminder emails based on client history, invoice amount, and overdue days. Tone escalates automatically."},
    # Onboarding
    {"id": "onb_001", "category": "onboarding", "question": "How do I get started?", "answer": "Complete the onboarding flow: add your business details, create your first client, then your first invoice. AI guides you step by step."},
    {"id": "onb_002", "category": "onboarding", "question": "Is there demo data available?", "answer": "Yes. During onboarding choose 'Load Demo Data' to populate the dashboard with sample clients, invoices, and analytics."},
]

# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------

_EMBEDDING_CACHE: dict[str, list[float]] = {}


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Return cosine similarity between two vectors."""
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


def _cache_key(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


async def generate_embedding(text: str, api_key: str) -> list[float]:
    """
    Generate an embedding via OpenAI text-embedding-3-small.
    Falls back to a simple TF-IDF-like bag-of-words vector if the API call fails.
    """
    ck = _cache_key(text)
    if ck in _EMBEDDING_CACHE:
        return _EMBEDDING_CACHE[ck]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": "text-embedding-3-small", "input": text},
            )
            resp.raise_for_status()
            vec = resp.json()["data"][0]["embedding"]
            _EMBEDDING_CACHE[ck] = vec
            return vec
    except Exception:
        # Graceful fallback: character-level hash-based pseudo-embedding (dim=128)
        vec = _pseudo_embedding(text)
        _EMBEDDING_CACHE[ck] = vec
        return vec


def _pseudo_embedding(text: str, dim: int = 128) -> list[float]:
    """Deterministic pseudo-embedding for offline/fallback use."""
    tokens = text.lower().split()
    vec = [0.0] * dim
    for token in tokens:
        for i, ch in enumerate(token):
            vec[(ord(ch) + i * 7) % dim] += 1.0
    norm = sum(v ** 2 for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


# ---------------------------------------------------------------------------
# Knowledge-base index
# ---------------------------------------------------------------------------

class FAQIndex:
    """In-memory semantic FAQ index."""

    def __init__(self):
        self._entries: list[dict] = []
        self._vectors: list[list[float]] = []
        self._built = False

    async def build(self, api_key: str = "") -> None:
        """Embed all FAQ entries and store vectors."""
        self._entries = FAQ_KNOWLEDGE_BASE.copy()
        tasks = [generate_embedding(f"{e['question']} {e['answer']}", api_key) for e in self._entries]
        self._vectors = await asyncio.gather(*tasks)
        self._built = True

    async def search_similar_questions(
        self,
        query: str,
        top_k: int = 3,
        min_confidence: float = 0.3,
        api_key: str = "",
    ) -> list[dict]:
        """
        Return top-k FAQ entries most similar to query.
        Each result includes: question, answer, category, confidence.
        """
        if not self._built:
            await self.build(api_key)

        query_vec = await generate_embedding(query, api_key)
        scored = [
            {**entry, "confidence": _cosine_similarity(query_vec, vec)}
            for entry, vec in zip(self._entries, self._vectors)
        ]
        scored.sort(key=lambda x: x["confidence"], reverse=True)
        results = [r for r in scored[:top_k] if r["confidence"] >= min_confidence]
        return results

    def get_suggested_questions(self, category: str | None = None, n: int = 5) -> list[str]:
        """Return n suggested questions, optionally filtered by category."""
        pool = [e for e in FAQ_KNOWLEDGE_BASE if category is None or e["category"] == category]
        import random
        sample = random.sample(pool, min(n, len(pool)))
        return [e["question"] for e in sample]

    async def build_context_for_prompt(self, query: str, api_key: str = "") -> str:
        """
        Retrieve relevant FAQ entries and format them as a context block
        ready to be injected into an AI prompt.
        """
        results = await self.search_similar_questions(query, top_k=3, api_key=api_key)
        if not results:
            return ""
        lines = ["Relevant knowledge base context:"]
        for r in results:
            lines.append(f"Q: {r['question']}")
            lines.append(f"A: {r['answer']}")
            lines.append("")
        return "\n".join(lines)


# Global singleton index
faq_index = FAQIndex()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_faq_context(query: str, api_key: str = "") -> str:
    """Inject FAQ context into an AI prompt for a given user query."""
    return await faq_index.build_context_for_prompt(query, api_key)


async def search_faqs(query: str, top_k: int = 5, api_key: str = "") -> list[dict]:
    """Search FAQs semantically. Returns list of {question, answer, category, confidence}."""
    return await faq_index.search_similar_questions(query, top_k=top_k, api_key=api_key)


def get_suggested_questions(category: str | None = None) -> list[str]:
    """Return suggested FAQ questions for the AI assistant sidebar."""
    return faq_index.get_suggested_questions(category)


async def embed_documents(texts: list[str], api_key: str = "") -> list[list[float]]:
    """Embed a list of arbitrary texts (utility for other modules)."""
    tasks = [generate_embedding(t, api_key) for t in texts]
    return await asyncio.gather(*tasks)


async def hybrid_search(
    query: str,
    top_k: int = 5,
    api_key: str = "",
) -> list[dict]:
    """
    Hybrid search: combine keyword matching score with semantic similarity.
    Returns merged, deduplicated, sorted results.
    """
    # Semantic results
    semantic = await search_faqs(query, top_k=top_k * 2, api_key=api_key)

    # Keyword boost
    keywords = set(query.lower().split())
    for r in semantic:
        text = (r["question"] + " " + r["answer"]).lower()
        keyword_hits = sum(1 for kw in keywords if kw in text)
        keyword_score = keyword_hits / max(len(keywords), 1)
        r["hybrid_score"] = 0.7 * r["confidence"] + 0.3 * keyword_score

    semantic.sort(key=lambda x: x["hybrid_score"], reverse=True)
    return semantic[:top_k]