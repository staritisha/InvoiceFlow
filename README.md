python3 << 'EOF'
readme = """# InvoiceFlow

![CI](https://github.com/staritisha/InvoiceFlow/actions/workflows/ci.yml/badge.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js_14-black?style=flat-square&logo=next.js)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-336791?style=flat-square&logo=postgresql&logoColor=white)
![Python](https://img.shields.io/badge/Python_3.12-3776AB?style=flat-square&logo=python&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?style=flat-square&logo=typescript&logoColor=white)

> AI-powered billing and invoice management platform built with FastAPI, Next.js 14, and PostgreSQL.

[Live Demo](#) · [API Docs](#) · [Report Bug](https://github.com/staritisha/InvoiceFlow/issues)

---

## What Is This

InvoiceFlow is a production-grade SaaS application that handles the complete invoice lifecycle — creation, delivery, payment tracking, and overdue escalation — with an AI layer for follow-up emails, weekly business summaries, and financial insights.

Built to demonstrate full-stack engineering: JWT auth, relational data modeling, background scheduling, PDF generation, REST API design, and AI integration.

---

## Features

**Core**

- Invoice CRUD with line items, auto-calculated totals, branded PDF download
- Invoice status lifecycle: draft → sent → paid → overdue
- Client management with payment history
- Recurring billing: weekly / monthly / quarterly / yearly auto-schedules
- Payment tracking and balance management
- CSV export for accounting handoff

**AI**

- Tone-aware follow-up emails: polite / firm / urgent (OpenAI or Anthropic)
- AI weekly business summary — revenue, collection health, actionable recommendation
- Analytics dashboard with KPIs, monthly revenue chart, overdue rate

**Engineering**

- JWT auth with bcrypt hashing, access + refresh token flow
- APScheduler background jobs for overdue checks and recurring invoice generation
- Per-IP rate limiting middleware (120 req/min sliding window)
- Security headers: HSTS, X-Frame-Options, XSS protection, Content-Type nosniff
- Request tracing: UUID per request, logged with method, path, status, latency
- GitHub Actions CI: pytest (4 auth tests) + Next.js production build on every push

---

## Tech Stack

| Layer      | Technology                                  |
| ---------- | ------------------------------------------- |
| Frontend   | Next.js 14 (App Router), TypeScript         |
| Backend    | FastAPI, Python 3.12, SQLAlchemy 2.0        |
| Database   | PostgreSQL (prod), SQLite (tests)           |
| Auth       | JWT via python-jose, bcrypt via passlib     |
| AI         | OpenAI GPT-4o-mini / Anthropic Claude Haiku |
| PDF        | ReportLab                                   |
| Scheduling | APScheduler                                 |
| Testing    | pytest, FastAPI TestClient                  |
| CI/CD      | GitHub Actions                              |
| Deploy     | Render + Vercel                             |

---

## Getting Started

**Backend**

```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

**Frontend**

```bash
cd frontend
npm install
echo "NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1" > .env.local
npm run dev
```

**Docker**

```bash
git clone https://github.com/staritisha/InvoiceFlow.git
cd InvoiceFlow && cp .env.example .env
docker compose up --build
```

---

## Environment Variables

```env
DATABASE_URL=postgresql://user:password@host/dbname
SECRET_KEY=your-minimum-32-character-secret-key
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USERNAME=your@gmail.com
EMAIL_PASSWORD=your-app-password
ALLOWED_ORIGINS=http://localhost:3000
```

---

## API Reference

| Method   | Endpoint                            | Auth | Description     |
| -------- | ----------------------------------- | ---- | --------------- |
| POST     | `/api/v1/auth/register`             | —    | Create account  |
| POST     | `/api/v1/auth/login`                | —    | Get JWT tokens  |
| GET      | `/api/v1/auth/me`                   | Yes  | Current user    |
| GET/POST | `/api/v1/customers`                 | Yes  | Clients         |
| GET/POST | `/api/v1/invoices`                  | Yes  | Invoices        |
| GET      | `/api/v1/invoices/{id}/pdf`         | Yes  | Download PDF    |
| PATCH    | `/api/v1/invoices/{id}/status`      | Yes  | Update status   |
| POST     | `/api/v1/invoices/{id}/ai-followup` | Yes  | AI reminder     |
| GET/POST | `/api/v1/recurring-billing`         | Yes  | Recurring plans |
| GET      | `/api/v1/dashboard/summary`         | Yes  | KPIs            |
| GET      | `/api/v1/analytics/revenue`         | Yes  | Revenue data    |
| POST     | `/api/v1/ai/weekly-summary`         | Yes  | AI summary      |

---

## Testing

```bash
cd backend && source venv/bin/activate
DATABASE_URL=sqlite:///./test.db \\
SECRET_KEY=test-secret-key-for-ci-testing-only-32chars \\
OPENAI_API_KEY=test \\
pytest app/tests/test_auth.py -v
```

4 tests covering register, login, authenticated route, and invalid credentials.

---

## Project Structure
