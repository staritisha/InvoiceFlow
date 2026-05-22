# InvoiceFlow

AI-powered invoice management platform. Create and send invoices, manage clients, track payments, set up recurring billing, and get AI-generated business insights.

## Tech Stack

- **Frontend**: Next.js 14, TypeScript, Tailwind CSS
- **Backend**: FastAPI (Python 3.12), SQLAlchemy, PostgreSQL
- **AI**: OpenAI GPT or Anthropic Claude (configurable)
- **Infrastructure**: Docker Compose

## Quick Start (Docker)

```bash
git clone https://github.com/staritisha/InvoiceFlow.git
cd InvoiceFlow
cp .env.example .env        # fill in your values
docker compose up --build
```

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- API docs: http://localhost:8000/docs

## Environment Variables

Create a `.env` file in the project root (or set these in `docker-compose.yml`):

```env
# Database (auto-configured in Docker)
DATABASE_URL=postgresql://postgres:postgres@db:5432/invoiceflow_db

# Security — change this before going live!
SECRET_KEY=your-long-random-secret-key-here

# AI (at least one required for AI features)
OPENAI_API_KEY=sk-...
# ANTHROPIC_API_KEY=sk-ant-...

# Email (optional — needed for sending reminders)
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USERNAME=your@gmail.com
EMAIL_PASSWORD=your-app-password
EMAIL_FROM=your@gmail.com
```

## Local Development (without Docker)

**Backend**
```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
DATABASE_URL=postgresql://postgres:postgres@localhost:5433/invoiceflow_db uvicorn app.main:app --reload
```

**Frontend**
```bash
cd frontend
npm install
npm run dev
```

## Features

| Feature | Status |
|---|---|
| User registration & login (JWT) | ✅ |
| Client management (CRUD) | ✅ |
| Invoice creation with line items | ✅ |
| Invoice PDF download | ✅ |
| Payment status tracking | ✅ |
| CSV export | ✅ |
| Recurring billing schedules | ✅ |
| Payment reminders | ✅ |
| Analytics dashboard (live data) | ✅ |
| AI weekly business summary | ✅ requires API key |
| AI follow-up message generation | ✅ requires API key |
| Email sending | ✅ requires SMTP config |

## API Reference

Full interactive docs at `/docs` (development mode only).

Key endpoints:
- `POST /api/v1/auth/register` — create account
- `POST /api/v1/auth/login` — get JWT token
- `GET/POST /api/v1/customers` — client management
- `GET/POST /api/v1/invoices` — invoice management
- `GET /api/v1/invoices/{id}/pdf` — download PDF
- `POST /api/v1/invoices/{id}/ai-followup` — AI reminder
- `GET /api/v1/dashboard/summary` — dashboard stats
- `POST /api/v1/ai/weekly-summary` — AI business summary
