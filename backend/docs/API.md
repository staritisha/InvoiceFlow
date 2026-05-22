# InvoiceFlow AI – API Documentation

## Base URL

```
https://your-domain.com/api
```

All endpoints require `Content-Type: application/json` unless otherwise noted.

---

## Authentication

InvoiceFlow uses **JWT Bearer tokens**.

### Register

```
POST /auth/register
```

**Body:**

```json
{
  "email": "user@example.com",
  "username": "johndoe",
  "password": "SecurePass123!",
  "full_name": "John Doe",
  "business_name": "Acme Studio"
}
```

**Response 201:**

```json
{
  "id": "uuid",
  "email": "user@example.com",
  "username": "johndoe",
  "full_name": "John Doe",
  "role": "member",
  "created_at": "2026-01-01T00:00:00Z"
}
```

---

### Login

```
POST /auth/login
```

**Body:**

```json
{ "email": "user@example.com", "password": "SecurePass123!" }
```

**Response 200:**

```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer"
}
```

---

### Refresh Token

```
POST /auth/refresh
```

**Body:** `{ "refresh_token": "eyJ..." }`

---

### Get Current User

```
GET /auth/me
Authorization: Bearer <token>
```

---

## Invoices

All invoice endpoints require `Authorization: Bearer <token>`.

### List Invoices

```
GET /invoices/?status=pending&client_id=uuid&search=acme&sort=due_date&page=1&limit=20
```

**Query Params:** `status`, `client_id`, `search`, `sort` (`created_at`|`due_date`|`total`), `order` (`asc`|`desc`), `page`, `limit`

**Response 200:** Paginated list with `items`, `total`, `page`, `pages`.

---

### Create Invoice

```
POST /invoices/
```

**Body:**

```json
{
  "client_id": "uuid",
  "description": "Web Development",
  "currency": "USD",
  "tax_rate": 10.0,
  "discount": 0,
  "theme": "modern",
  "due_date": "2026-06-30T00:00:00Z",
  "notes": "Net 30",
  "terms": "Payment due within 30 days",
  "items": [{ "description": "Frontend Dev", "quantity": 10, "rate": 150.0 }]
}
```

**Response 201:** Full invoice object with calculated `subtotal`, `tax_amount`, `total`, `balance_due`.

---

### Get / Update / Delete Invoice

```
GET    /invoices/{id}
PUT    /invoices/{id}
DELETE /invoices/{id}
```

---

### Send Invoice

```
POST /invoices/{id}/send
```

Marks invoice as `sent` and emails the client.

---

### Record Payment

```
POST /invoices/{id}/pay
```

**Body:**

```json
{ "amount": 1500.0, "method": "bank_transfer", "notes": "Wire ref #123" }
```

---

### Duplicate Invoice

```
POST /invoices/{id}/duplicate
```

Clones invoice with a new number and `pending` status.

---

### Invoice Themes

```
GET /invoices/themes/list
```

Returns: `["modern", "classic", "minimal", "bold", "startup", "elegant"]`

---

### AI Invoice Generation

```
POST /invoices/ai/generate
```

**Body:**

```json
{
  "prompt": "Invoice Acme Corp for 10 hours web dev at $150/hr due in 30 days",
  "client_id": "uuid"
}
```

---

### AI Auto-Fill

```
POST /invoices/ai/fill
```

**Body:** `{ "invoice_id": "uuid", "fields": ["description", "items"] }`

---

### Voice Invoice

```
POST /invoices/voice
```

**Body:** `{ "transcript": "Create invoice for Nova Digital 5 hours design 200 per hour" }`

---

### Recurring Invoices

```
POST /invoices/recurring          # Create recurring schedule
GET  /invoices/recurring/list     # List all recurring invoices
```

**Create Body:**

```json
{
  "template_invoice_id": "uuid",
  "frequency": "monthly",
  "start_date": "2026-06-01T00:00:00Z",
  "max_runs": 12
}
```

---

## Clients

### CRUD

```
GET    /clients/?search=acme&risk=high&page=1
POST   /clients/
GET    /clients/{id}
PUT    /clients/{id}
DELETE /clients/{id}
```

### Client Analytics

```
GET /clients/{id}/summary            # AI-generated client summary
POST /clients/{id}/risk-score        # Trigger AI risk scoring
GET  /clients/{id}/payment-behavior  # Payment history & trends
GET  /clients/leaderboard/top        # Top clients by revenue
```

---

## Analytics

```
GET /analytics/revenue              # Revenue dashboard
GET /analytics/late-payments        # Late payment analytics
GET /analytics/recurring-revenue    # MRR / ARR
GET /analytics/health-score         # AI business health score (0-100)
GET /analytics/weekly-summary       # AI weekly summary
GET /analytics/cash-flow-forecast   # 30/60/90-day cash flow forecast
GET /analytics/kpis                 # DSO, collection rate, avg invoice value
GET /analytics/top-clients          # Top clients by revenue
GET /analytics/insights             # AI business insights
POST /analytics/insights/generate   # Generate new AI insights
GET /analytics/revenue-forecast     # AI revenue forecast
GET /analytics/heatmaps/payments    # Payment date heatmap
GET /analytics/trends/financial     # Financial trend detection
```

---

## AI Assistant

### Chat

```
POST /ai/chat
```

**Body:**

```json
{
  "message": "What are my overdue invoices?",
  "session_id": "session-abc123"
}
```

**Response:**

```json
{
  "response": "You have 3 overdue invoices totalling $4,250...",
  "session_id": "session-abc123",
  "context": {}
}
```

### Chat History

```
GET    /ai/chat/history/{session_id}
DELETE /ai/chat/history/{session_id}
```

### AI Command Center

```
POST /ai/command
```

**Body:** `{ "command": "Create invoice for Acme for 5 hours consulting at $200/hr" }`

### Conversational Filters

```
POST /ai/filter
```

**Body:** `{ "query": "paid invoices over $1000 last month", "entity_type": "invoice" }`

**Response:** `{ "filters": { "status": "paid", "min_amount": 1000, "date_range": "last_30_days" } }`

### Smart Search

```
POST /ai/search
```

**Body:** `{ "query": "Acme overdue", "entity_types": ["invoice", "client"] }`

### Recommendations & Insights

```
GET /ai/recommendations
GET /ai/action-suggestions
GET /ai/personalized-tips
GET /ai/onboarding-steps
GET /ai/memory/context
GET /ai/insights/cards
```

---

## Reminders

```
GET  /reminders/                          # List reminders
POST /reminders/                          # Create reminder
GET  /reminders/{id}
POST /reminders/{id}/send                 # Send immediately
POST /reminders/ai/generate              # AI-generate reminder content
GET  /reminders/ai/thank-you/{invoice_id} # AI thank-you email
GET  /reminders/templates/list            # Tone templates
```

**AI Generate Body:**

```json
{
  "invoice_id": "uuid",
  "tone": "friendly",
  "days_overdue": 5
}
```

---

## Workflows

```
GET  /workflows/
POST /workflows/
GET  /workflows/{id}
PUT  /workflows/{id}
DELETE /workflows/{id}
POST /workflows/{id}/run        # Manual trigger
GET  /workflows/{id}/runs       # Run history
GET  /workflows/templates/list  # Pre-built templates
```

**Create Body:**

```json
{
  "name": "Auto Overdue Reminder",
  "trigger_type": "invoice_overdue",
  "conditions": { "days_overdue": 1 },
  "actions": [{ "type": "send_reminder", "tone": "friendly" }],
  "is_active": true
}
```

**Trigger Types:** `invoice_overdue` | `invoice_paid` | `scheduled` | `client_risk_high`

**Action Types:** `send_reminder` | `send_email` | `create_notification` | `generate_report` | `update_status`

---

## Notifications

```
GET  /notifications/             # Smart notification center
GET  /notifications/unread-count
POST /notifications/
PUT  /notifications/{id}/read
PUT  /notifications/read-all
PUT  /notifications/bulk-update
DELETE /notifications/{id}
GET  /notifications/ws/info
```

---

## Reports

```
GET  /reports/
POST /reports/               # Generate report
GET  /reports/{id}
GET  /reports/{id}/download
DELETE /reports/{id}
GET  /reports/types/list
```

**Generate Body:**

```json
{
  "type": "revenue",
  "format": "pdf",
  "title": "Q1 Revenue Report",
  "filters": { "start_date": "2026-01-01", "end_date": "2026-03-31" }
}
```

**Report Types:** `revenue` | `tax` | `client` | `expense` | `cashflow`
**Formats:** `pdf` | `csv` | `excel`

---

## Integrations

```
POST /integrations/stripe/create-payment-intent
POST /integrations/stripe/webhook
GET  /integrations/stripe/config
POST /integrations/email/send
POST /integrations/whatsapp/send
GET  /integrations/exchange-rates
GET  /integrations/currencies/list
POST /integrations/currencies/convert
```

---

## WebSocket

### Connect

```
ws://your-domain.com/ws/connect?token=<jwt>
```

### Client → Server Events

| Event                   | Payload                                     |
| ----------------------- | ------------------------------------------- |
| `ping`                  | `{}`                                        |
| `request_kpi_refresh`   | `{}`                                        |
| `request_activity_feed` | `{}`                                        |
| `ai_chat`               | `{ "message": "...", "session_id": "..." }` |

### Server → Client Events

| Event               | Description                    |
| ------------------- | ------------------------------ |
| `connected`         | Connection confirmed           |
| `initial_snapshot`  | KPIs + unread count on connect |
| `kpi_refresh`       | Live KPI update                |
| `activity_feed`     | Latest 20 activities           |
| `invoice_created`   | New invoice event              |
| `invoice_paid`      | Payment received               |
| `overdue_detected`  | Invoice became overdue         |
| `analytics_updated` | Analytics recalculated         |
| `ai_insight`        | New AI insight available       |
| `ai_token`          | Streaming AI response token    |
| `notification`      | New in-app notification        |

---

## Error Responses

All errors follow this format:

```json
{
  "detail": "Human-readable error message",
  "code": "ERROR_CODE",
  "field": "field_name"
}
```

| Status | Meaning                                 |
| ------ | --------------------------------------- |
| 400    | Bad Request                             |
| 401    | Unauthorized – missing or invalid token |
| 403    | Forbidden – insufficient permissions    |
| 404    | Not Found                               |
| 422    | Validation Error                        |
| 429    | Rate Limited                            |
| 500    | Internal Server Error                   |

---

## Rate Limiting

- **Default:** 100 requests / minute per IP
- **AI endpoints:** 30 requests / minute per user
- **WebSocket:** 1 connection per user

---

## Pagination

All list endpoints return:

```json
{
  "items": [...],
  "total": 150,
  "page": 1,
  "limit": 20,
  "pages": 8
}
```
