# InvoiceFlow AI – AI Features Documentation

## Overview

InvoiceFlow AI embeds artificial intelligence at every level of the invoicing lifecycle. Rather than bolting AI on as a feature, the system is designed AI-first — every workflow, insight, and interaction is enhanced by machine intelligence.

All AI features use **OpenAI GPT-4o-mini** as the primary model, with graceful fallbacks for offline/degraded scenarios.

---

## 1. AI Invoice Generation

### What it does

Converts a plain-English description into a fully structured invoice — line items, amounts, due dates, client assignment, and a professional description.

### How to use

```
POST /invoices/ai/generate
{
  "prompt": "Invoice Acme Corp for 10 hours web development at $150/hr and 2 hours design at $120/hr, due in 30 days",
  "client_id": "optional-uuid"
}
```

### What the AI extracts

- Line items (description, quantity, rate, amount)
- Due date calculation
- Invoice description
- Priority level (high/medium/low based on amount)
- Currency detection

### Prompt template

Located at `app/ai/prompts/invoice_generation.txt`. The prompt instructs the model to return a strict JSON schema so parsing is deterministic.

### Fallback

If the AI call fails, the system returns a skeleton invoice with the raw prompt as the description.

---

## 2. AI Auto-Fill

### What it does

Analyses an incomplete invoice and fills in missing fields using context from the client's history and similar past invoices.

```
POST /invoices/ai/fill
{ "invoice_id": "uuid", "fields": ["description", "terms", "notes"] }
```

---

## 3. Voice Invoice Creation

### What it does

Accepts a voice transcript (from the frontend speech API) and creates an invoice from natural language.

```
POST /invoices/voice
{ "transcript": "Invoice Nova Digital five hours of logo design at two hundred per hour due next Friday" }
```

The AI parser extracts:

- Client name → fuzzy-matched to existing clients
- Service description and line items
- Rate and quantity
- Due date from relative expressions ("next Friday", "in 2 weeks")

---

## 4. AI Analytics & Business Insights

### Business Health Score (0–100)

Computed by `analytics_service.py` using a weighted formula:

| Factor                   | Weight |
| ------------------------ | ------ |
| Collection rate          | 30%    |
| Overdue ratio (inverted) | 25%    |
| Revenue growth (MoM)     | 20%    |
| DSO (inverted)           | 15%    |
| Client diversity         | 10%    |

Scores:

- **80–100:** Excellent 🟢
- **60–79:** Good 🟡
- **40–59:** Needs attention 🟠
- **0–39:** Critical 🔴

### AI Business Insights

```
POST /analytics/insights/generate
```

The AI analyses the following data points and returns 3–5 actionable insights:

- Revenue trend (last 6 months)
- Top overdue clients
- Payment velocity changes
- Seasonal patterns
- Client concentration risk

Each insight has: `type`, `title`, `content`, `severity` (info/warning/critical), `category`.

### Cash Flow Forecast

```
GET /analytics/cash-flow-forecast
```

Predicts expected cash inflows for 30, 60, and 90 days based on:

- Outstanding invoice due dates
- Client-specific payment probability (from payment behaviour score)
- Historical payment delay patterns

Returns confidence scores per period.

### Revenue Forecasting

```
GET /analytics/revenue-forecast
```

LLM-powered narrative forecast combining quantitative trend data with qualitative business context.

---

## 5. AI Chat Assistant

### What it does

A conversational assistant embedded in the dashboard sidebar. Users can ask questions, trigger actions, and get insights — all in natural language.

### Capabilities

| Intent            | Example                                             | Action                 |
| ----------------- | --------------------------------------------------- | ---------------------- |
| Query invoices    | "Show me overdue invoices this month"               | Fetches + summarises   |
| Create invoice    | "Make an invoice for Acme for 5 hours at $200"      | Calls invoice creation |
| Analytics query   | "What's my collection rate this quarter?"           | Runs analytics query   |
| Send reminder     | "Send a reminder to all clients overdue by 7+ days" | Triggers reminder flow |
| Business question | "Should I offer a discount to slow payers?"         | AI recommendation      |

### Memory & Context

Conversations are stored in `ai_conversations` table with `session_id`. Each new message includes:

1. Last N conversation turns (sliding window)
2. Current business context (team KPIs, recent activity)
3. Relevant FAQ context (injected via `faq_embeddings.py`)

### Streaming

WebSocket endpoint supports token-by-token streaming for a real-time typing effect:

```json
{ "type": "ai_token", "token": "Your", "done": false }
{ "type": "ai_token", "token": " revenue", "done": false }
{ "type": "ai_token", "token": "", "done": true }
```

---

## 6. AI Command Center

A power-user interface for executing complex multi-step commands.

```
POST /ai/command
{ "command": "Create invoices for all clients with outstanding work this month and send them immediately" }
```

The AI:

1. Parses intent and entities
2. Plans the execution steps
3. Calls the appropriate service methods
4. Returns a structured result with each action taken

---

## 7. AI Payment Reminders

### Auto-generated reminder content

```
POST /reminders/ai/generate
{
  "invoice_id": "uuid",
  "tone": "friendly",
  "days_overdue": 5
}
```

The AI generates:

- Email subject line
- Personalised body (using client name, invoice details, payment history)
- Call-to-action text
- Appropriate urgency level

### Tone escalation

| Days Overdue | Tone           | Style                            |
| ------------ | -------------- | -------------------------------- |
| 1–7          | `friendly`     | Warm reminder, assumes oversight |
| 8–14         | `professional` | Clear, business-like             |
| 15–30        | `urgent`       | Explicit consequences            |
| 31+          | `firm`         | Final demand, legal language     |

### AI Thank-You Emails

```
GET /reminders/ai/thank-you/{invoice_id}
```

Generated after payment — personalised thank-you with optional upsell note based on client value.

---

## 8. AI Client Risk Scoring

```
POST /clients/{id}/risk-score
```

The AI analyses:

- Average days to pay
- Overdue frequency (last 12 months)
- Payment trend (improving/worsening)
- Invoice value vs. payment consistency
- Communication responsiveness (if available)

Returns: `risk_level` (low/medium/high), `risk_score` (0.0–1.0), `reasoning`, `recommendations`.

---

## 9. Smart Search

```
POST /ai/search
{ "query": "overdue tech clients owing more than $2000", "entity_types": ["invoice", "client"] }
```

The AI:

1. Parses the natural language query into structured filters
2. Executes database queries with those filters
3. Ranks results by relevance
4. Returns rich result cards with highlighted matches

---

## 10. FAQ Semantic Search (`faq_embeddings.py`)

Powers the AI assistant's knowledge base with a 2-tier search:

1. **Semantic similarity** (via OpenAI `text-embedding-3-small` or pseudo-embedding fallback)
2. **Keyword boosting** (hybrid search)

Used to inject relevant FAQ context into every AI prompt, ensuring the assistant always has platform-specific knowledge without hallucinating.

**Offline fallback:** If embeddings API is unavailable, a deterministic character-hash pseudo-embedding provides degraded-but-functional search.

---

## 11. AI Onboarding

```
GET /ai/onboarding-steps
```

Returns a personalised 5-step onboarding flow. The system also:

- Detects user's business type from registration data
- Pre-fills preferred currency
- Suggests an invoice template based on industry
- Schedules Day 1, Day 3, Day 7 tips

---

## 12. AI Recommendations Panel

```
GET /ai/recommendations
```

Analyses current business state and returns 3–5 prioritised action cards:

Examples:

- "📬 3 invoices are 7+ days overdue. Send a batch reminder."
- "🔄 Client Nova Digital bills monthly — consider a recurring invoice."
- "⚠️ Your DSO has increased 12% this month. Review payment terms."
- "💰 You have $8,400 outstanding due this week. Follow up now."

---

## Configuration

All AI settings are in `app/core/ai_config.py`:

```python
class AIProviderConfig:
    model: str = "gpt-4o-mini"
    max_tokens: int = 1000
    temperature: float = 0.3      # Low for structured outputs
    timeout: int = 15             # seconds
```

Set your API key:

```
OPENAI_API_KEY=sk-...
```

To switch providers (Anthropic, etc.), update `ai_config.py` headers and endpoint.
