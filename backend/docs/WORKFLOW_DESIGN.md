# InvoiceFlow AI – Workflow Design Guide

## Overview

The workflow engine is InvoiceFlow's automation backbone. It allows users to define **trigger → condition → action** pipelines that run automatically, eliminating manual follow-ups and reducing time-to-payment.

---

## Core Concepts

### Workflow Structure

```json
{
  "name": "Auto Overdue Reminder",
  "trigger_type": "invoice_overdue",
  "conditions": { "days_overdue": 7, "min_amount": 500 },
  "actions": [
    { "type": "send_reminder", "tone": "professional" },
    {
      "type": "create_notification",
      "message": "Reminder sent for overdue invoice"
    }
  ],
  "is_active": true
}
```

Every workflow has three parts:

| Part           | Role                                             |
| -------------- | ------------------------------------------------ |
| **Trigger**    | What event starts the workflow                   |
| **Conditions** | Filter criteria — only matching entities proceed |
| **Actions**    | Ordered list of steps to execute                 |

---

## Triggers

### `invoice_overdue`

Fires when an invoice's `due_date` has passed and `status` is `sent` or `partial`.

**Condition fields:**

- `days_overdue` — minimum days past due (default: 1)
- `min_amount` — minimum balance due
- `client_ids` — restrict to specific clients

**Example:**

```json
{
  "trigger_type": "invoice_overdue",
  "conditions": { "days_overdue": 14, "min_amount": 1000 }
}
```

---

### `invoice_paid`

Fires immediately when a payment is recorded that fully satisfies an invoice.

**Condition fields:**

- `min_amount` — only trigger for large payments
- `client_ids` — restrict to specific clients

---

### `scheduled`

Fires on a time-based schedule, independent of any invoice event.

**Condition fields:**

- `schedule`: `daily` | `weekly` | `monthly`
- `day`: `monday`–`sunday` (for weekly)
- `day_of_month`: 1–31 (for monthly)
- `time`: `"09:00"` (24-hour, UTC)

**Example — weekly revenue report every Monday:**

```json
{
  "trigger_type": "scheduled",
  "conditions": { "schedule": "weekly", "day": "monday", "time": "08:00" }
}
```

---

### `client_risk_high`

Fires when a client's AI risk score crosses a threshold.

**Condition fields:**

- `risk_threshold`: 0.0–1.0 (default: 0.7)

---

## Actions

Actions are executed **sequentially** in the order defined. If one fails, the workflow logs the error and continues with remaining actions (configurable).

### `send_reminder`

Generates AI reminder content and sends it via email.

```json
{ "type": "send_reminder", "tone": "friendly" }
```

Tones: `friendly` | `professional` | `urgent` | `firm`

---

### `send_email`

Send a custom email or pre-built template.

```json
{
  "type": "send_email",
  "template": "thank_you",
  "subject": "Optional override"
}
```

Templates: `thank_you` | `overdue_notice` | `payment_confirmation` | `weekly_summary`

---

### `create_notification`

Creates an in-app notification for the invoice owner.

```json
{ "type": "create_notification", "message": "High-value invoice is overdue!" }
```

---

### `generate_report`

Generates a financial report and optionally emails it.

```json
{
  "type": "generate_report",
  "report_type": "revenue",
  "format": "pdf",
  "email": true
}
```

---

### `update_status`

Changes the invoice status.

```json
{ "type": "update_status", "status": "escalated" }
```

---

### `ai_action`

Freeform AI-powered action — describe what you want in natural language.

```json
{
  "type": "ai_action",
  "prompt": "Analyse this client's payment trend and generate a risk summary"
}
```

---

## Built-in Workflow Flows

### 1. Overdue Invoice Flow (`overdue_invoice_flow.py`)

The most important automation flow. Runs on the scheduler every hour.

```
┌──────────────────────────────────────────────────────┐
│              Overdue Invoice Flow                    │
│                                                      │
│  1. detect_overdue_invoices()                        │
│     └─ Query: status in [sent, partial] & due < now  │
│                                                      │
│  2. For each overdue invoice:                        │
│     ├─ Determine escalation tier:                    │
│     │   Days 1-7  → friendly                        │
│     │   Days 8-14 → professional                    │
│     │   Days 15-30 → urgent                         │
│     │   Days 31+  → firm                            │
│     │                                               │
│     ├─ generate_ai_reminder(invoice, client)        │
│     ├─ send_reminder_email()                        │
│     ├─ schedule_follow_up() (next reminder date)    │
│     └─ broadcast_overdue_event() → WebSocket        │
│                                                      │
│  3. Return summary stats                            │
└──────────────────────────────────────────────────────┘
```

---

### 2. Recurring Invoice Flow (`recurring_invoice_flow.py`)

Runs on the scheduler at midnight daily.

```
┌──────────────────────────────────────────────────────┐
│              Recurring Invoice Flow                  │
│                                                      │
│  1. get_due_recurring_invoices()                     │
│     └─ Query: is_active=true & next_run <= now       │
│                                                      │
│  2. For each due recurring invoice:                  │
│     ├─ duplicate_invoice(template_id)               │
│     │   └─ Clone items, new number, reset dates     │
│     ├─ auto_send_invoice()                          │
│     ├─ log_recurring_activity()                     │
│     ├─ broadcast_recurring_event() → WebSocket      │
│     └─ update_recurring_schedule()                  │
│         └─ Advance next_run by frequency            │
│         └─ Deactivate if max_runs reached           │
└──────────────────────────────────────────────────────┘
```

**Frequency options:** `daily` | `weekly` | `biweekly` | `monthly` | `quarterly` | `yearly`

---

### 3. Payment Follow-up Flow (`payment_followup_flow.py`)

Triggered immediately after a payment is recorded.

```
┌──────────────────────────────────────────────────────┐
│              Payment Follow-up Flow                  │
│                                                      │
│  Triggered by: POST /invoices/{id}/pay               │
│                                                      │
│  1. generate_thank_you_email(invoice, client)        │
│     └─ AI-generated personalised thank-you          │
│                                                      │
│  2. update_client_scores(client_id, days_to_pay)    │
│     ├─ Update payment_behavior_score               │
│     └─ Update average_days_to_pay                  │
│                                                      │
│  3. create_payment_notification(invoice)            │
│                                                      │
│  4. log_payment_activity()                          │
│                                                      │
│  5. broadcast_payment_event() → WebSocket           │
│     └─ "invoice_paid" event to team room            │
└──────────────────────────────────────────────────────┘
```

---

### 4. Onboarding Flow (`onboarding_flow.py`)

Triggered once on new user registration.

```
┌──────────────────────────────────────────────────────┐
│              Onboarding Flow                         │
│                                                      │
│  Triggered by: POST /auth/register                   │
│                                                      │
│  1. send_welcome_notification(user_id, user_name)   │
│                                                      │
│  2. create_demo_data(user_id, team_id)              │
│     ├─ 3 demo clients                               │
│     ├─ 3 demo invoices (paid/sent/overdue/pending)  │
│     └─ Demo invoice items                           │
│                                                      │
│  3. schedule_checkin_reminders(user_id)             │
│     ├─ Day 1: AI tip notification                   │
│     ├─ Day 3: Workflow setup tip                    │
│     └─ Day 7: Week 1 review                        │
│                                                      │
│  4. create_default_workflow(team_id)                │
│     └─ "Auto Overdue Reminder" enabled by default   │
└──────────────────────────────────────────────────────┘
```

---

## Execution Engine (`workflow_service.py`)

### Run lifecycle

```
Workflow triggered
    │
    ▼
WorkflowRun created (status: "running")
    │
    ▼
For each action in workflow.actions:
    ├─ Execute action handler
    ├─ Log result to run.log[]
    └─ On error: log error, continue (or abort if configured)
    │
    ▼
WorkflowRun updated (status: "completed" | "failed")
    │
    ▼
WebSocket broadcast: workflow_completed event
```

### Manual trigger

Any workflow can be triggered manually via:

```
POST /workflows/{id}/run
```

Useful for testing and one-off executions.

---

## Escalation System

The overdue flow implements automatic escalation — each reminder is progressively firmer:

```
Day 1-7:   "Hi {{client}}, just a friendly reminder about invoice #{{n}}..."
Day 8-14:  "Dear {{client}}, your invoice #{{n}} remains outstanding..."
Day 15-30: "URGENT: Invoice #{{n}} is seriously overdue. Immediate action required."
Day 31+:   "FINAL NOTICE: Invoice #{{n}} will be referred to collections if not settled."
```

The next follow-up is auto-scheduled based on the tier interval:

- Friendly tier → next reminder in 3 days
- Professional tier → next reminder in 5 days
- Urgent tier → next reminder in 7 days

---

## Workflow Templates

Pre-built templates available at `GET /workflows/templates/list`:

| Template               | Trigger                  | Actions                          |
| ---------------------- | ------------------------ | -------------------------------- |
| Auto Overdue Reminder  | invoice_overdue (day 1)  | send_reminder (friendly)         |
| Escalation Sequence    | invoice_overdue (day 14) | send_reminder (urgent) + notify  |
| Payment Thank You      | invoice_paid             | send_email (thank_you)           |
| Weekly Revenue Report  | scheduled (monday)       | generate_report (revenue)        |
| High Risk Client Alert | client_risk_high         | create_notification + send_email |
| Monthly Recurring      | scheduled (monthly)      | generate_report (cashflow)       |

---

## Monitoring

Every workflow execution is logged to `workflow_runs`:

```json
{
  "id": "uuid",
  "workflow_id": "uuid",
  "status": "completed",
  "started_at": "2026-05-01T09:00:00Z",
  "completed_at": "2026-05-01T09:00:02Z",
  "triggered_by": "scheduler",
  "log": [
    { "action": "send_reminder", "status": "success", "invoice_id": "uuid" },
    { "action": "create_notification", "status": "success" }
  ]
}
```

View run history: `GET /workflows/{id}/runs`
