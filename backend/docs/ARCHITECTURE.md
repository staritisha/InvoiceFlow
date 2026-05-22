# InvoiceFlow AI – System Architecture

## Overview

InvoiceFlow AI is a modern, AI-first SaaS invoicing platform built on a fully async Python backend with real-time capabilities. The system is designed around three core pillars: **AI intelligence**, **automation workflows**, and **real-time collaboration**.

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Clients                            │
│         Next.js Frontend  ·  Mobile  ·  API Consumers   │
└──────────────┬──────────────────────┬───────────────────┘
               │ HTTPS                │ WebSocket (wss://)
┌──────────────▼──────────────────────▼───────────────────┐
│                    Nginx / Load Balancer                 │
│              Rate limiting · TLS · Static files         │
└──────────────┬──────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────┐
│                 FastAPI Application                      │
│                                                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐ │
│  │  REST API   │  │  WebSocket  │  │  Background      │ │
│  │  Routers    │  │  Manager    │  │  Scheduler       │ │
│  └──────┬──────┘  └──────┬──────┘  └────────┬────────┘ │
│         │                │                   │          │
│  ┌──────▼──────────────────────────────────────────┐   │
│  │              Service Layer                      │   │
│  │  InvoiceService · ClientService · AIService     │   │
│  │  AnalyticsService · WorkflowService · etc.      │   │
│  └──────┬──────────────────────────────────────────┘   │
│         │                                               │
│  ┌──────▼──────────────────────────────────────────┐   │
│  │              AI Layer                           │   │
│  │  Agents · Embeddings · Prompt Templates         │   │
│  └──────┬──────────────────────────────────────────┘   │
└─────────┼───────────────────────────────────────────────┘
          │
┌─────────▼───────────────────────────────────────────────┐
│                  Data Layer                             │
│   PostgreSQL (async)  ·  Redis (cache/sessions)        │
└─────────────────────────────────────────────────────────┘
          │
┌─────────▼───────────────────────────────────────────────┐
│                External Services                        │
│   OpenAI API  ·  Stripe  ·  SMTP  ·  WhatsApp API      │
└─────────────────────────────────────────────────────────┘
```

---

## Component Breakdown

### API Layer (`app/routers/`)

Each router is a self-contained module handling HTTP concerns only — validation, auth, and response shaping. Business logic lives exclusively in the service layer.

| Router             | Responsibility                                   |
| ------------------ | ------------------------------------------------ |
| `auth.py`          | JWT issuance, token refresh, user identity       |
| `invoices.py`      | Invoice CRUD + AI generation + voice + recurring |
| `clients.py`       | Client management + risk scoring                 |
| `analytics.py`     | KPIs, forecasts, insights, health scores         |
| `ai_assistant.py`  | Chat, commands, search, recommendations          |
| `workflows.py`     | Automation CRUD + execution                      |
| `notifications.py` | In-app notification center                       |
| `reports.py`       | PDF/CSV/Excel report generation                  |
| `integrations.py`  | Stripe, email, WhatsApp, currencies              |

---

### Service Layer (`app/services/`)

Pure async business logic. Services never import from routers; they only depend on models and external APIs.

```
invoice_service.py      ← CRUD, totals calc, payment flow, duplication
client_service.py       ← Aggregation, risk levels, leaderboard
ai_service.py           ← All LLM calls, chat memory, search, embeddings
analytics_service.py    ← Revenue, DSO, MRR, forecasting, health score
reminder_service.py     ← Reminder lifecycle, tone management
workflow_service.py     ← Trigger eval, condition matching, action execution
notification_service.py ← In-app + email + push + WebSocket broadcast
payment_service.py      ← Stripe integration, webhook handling
report_service.py       ← PDF/CSV/Excel generation with WeasyPrint
voice_service.py        ← Audio transcription + NL command parsing
```

---

### AI Layer (`app/ai/`)

```
ai/
├── agents/
│   ├── invoice_agent.py    ← Invoice generation & validation
│   ├── analytics_agent.py  ← Insight generation & forecasting
│   ├── reminder_agent.py   ← Tone optimisation & escalation
│   └── assistant_agent.py  ← Conversational agent with memory
├── embeddings/
│   └── faq_embeddings.py   ← Semantic FAQ search & context injection
└── prompts/
    ├── invoice_generation.txt
    ├── business_insights.txt
    ├── reminder_generation.txt
    └── financial_chatbot.txt
```

**AI Flow:**

```
User message
    │
    ▼
FAQIndex.build_context_for_prompt()   ← inject relevant knowledge
    │
    ▼
Agent selects prompt template
    │
    ▼
OpenAI API call (gpt-4o-mini)
    │
    ▼
Structured JSON response
    │
    ▼
Service layer executes action (create invoice / send reminder / etc.)
    │
    ▼
WebSocket broadcasts result to dashboard
```

---

### Workflow Engine (`app/workflows/`)

Event-driven automation system. Each workflow file encapsulates a full automation flow:

```
overdue_invoice_flow.py    detect → AI reminder → send → schedule → escalate
recurring_invoice_flow.py  check schedule → duplicate → update dates → send
payment_followup_flow.py   payment detected → thank-you → update scores → notify
onboarding_flow.py         new user → welcome → demo data → tips → check-ins
```

**Trigger evaluation** (in `workflow_service.py`):

1. Scheduler runs every minute (APScheduler)
2. For each active workflow, evaluate `trigger_type` against current system state
3. Match `conditions` (days_overdue, amount thresholds, risk scores)
4. Execute `actions` sequentially
5. Log to `workflow_runs`, broadcast WebSocket event

---

### WebSocket Architecture (`app/websocket/`)

```
ConnectionManager (singleton)
├── _team_connections: dict[team_id → set[WebSocket]]
├── _user_connections: dict[user_id → WebSocket]
└── _socket_meta: dict[WebSocket → {user_id, team_id, connected_at}]

Key methods:
  connect(ws, user_id, team_id)
  disconnect(ws)
  broadcast_team(team_id, event)
  send_personal_message(user_id, event)
  stream_ai_token(user_id, token)
```

WebSocket events flow:

```
Database change / Scheduler event
    │
    ▼
Service layer calls broadcast helper
    │
    ▼
ConnectionManager.broadcast_team(team_id, event)
    │
    ▼
All connected team members receive real-time update
```

---

### Database Architecture

**Engine:** PostgreSQL 15+ with `asyncpg` driver via SQLAlchemy async.

**Key design decisions:**

- All PKs are UUIDs for distributed safety
- JSONB columns for flexible metadata (workflow conditions, client tags, preferences)
- Indexed on: `email`, `team_id`, `status`, `due_date`, `invoice_id`
- Soft deletes not used — hard deletes with FK cascades
- Async sessions via `get_db()` dependency injection

---

### Caching Strategy (Redis)

| Key Pattern            | TTL | Purpose                 |
| ---------------------- | --- | ----------------------- |
| `embedding:{hash}`     | 24h | FAQ/document embeddings |
| `kpi:{team_id}`        | 5m  | Dashboard KPI snapshot  |
| `exchange_rates`       | 1h  | Currency rates          |
| `ai_context:{user_id}` | 30m | AI conversation context |
| `rate_limit:{ip}`      | 1m  | Request rate limiting   |

---

## Security Architecture

- **Authentication:** JWT (HS256) with short-lived access tokens (30 min) + long-lived refresh tokens (7 days)
- **RBAC:** `superadmin` > `admin` > `manager` > `member` hierarchy via `permissions.py`
- **Rate limiting:** IP-based in-memory middleware (upgradeable to Redis)
- **Password hashing:** bcrypt via passlib
- **CORS:** Configurable origins via `Settings`
- **Input sanitisation:** HTML sanitiser on all text fields
- **Stripe webhooks:** Signature verification via `stripe-signature` header

---

## Deployment Architecture

```
Internet → Cloudflare (DDoS/CDN) → Nginx → Gunicorn (4 workers) → FastAPI
                                                    │
                                              PostgreSQL (primary)
                                                    │
                                               Redis (cache)
                                                    │
                                             Celery workers (async tasks)
```

See `deployment/` for Docker Compose, Nginx config, Render.yaml, and Kubernetes manifests.
