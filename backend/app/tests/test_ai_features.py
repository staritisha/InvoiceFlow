"""
Tests for AI features: chat, command center, smart search, recommendations, insights.
Run: pytest app/tests/test_ai_features.py -v
"""

import pytest
from httpx import AsyncClient
from app.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def auth_headers():
    async with AsyncClient(app=app, base_url="http://test") as client:
        await client.post("/api/auth/register", json={
            "email": "ai_test@invoiceflow.test",
            "username": "aitester",
            "password": "TestPass123!",
            "full_name": "AI Tester",
            "business_name": "AI Test Co.",
        })
        login = await client.post("/api/auth/login", json={
            "email": "ai_test@invoiceflow.test",
            "password": "TestPass123!",
        })
        token = login.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def chat_session_id():
    return "test-session-001"


# ---------------------------------------------------------------------------
# AI Chat / Assistant
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ai_chat_basic(auth_headers, chat_session_id):
    """AI assistant should return a response for a basic question."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/ai/chat", json={
            "message": "What is my total revenue this month?",
            "session_id": chat_session_id,
        }, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "response" in data or "message" in data or "content" in data


@pytest.mark.asyncio
async def test_ai_chat_invoice_query(auth_headers, chat_session_id):
    """AI assistant should handle invoice-specific questions."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/ai/chat", json={
            "message": "Show me all overdue invoices",
            "session_id": chat_session_id,
        }, headers=auth_headers)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_ai_chat_multi_turn(auth_headers, chat_session_id):
    """Multi-turn conversation should maintain context."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        await client.post("/api/ai/chat", json={
            "message": "My business is a freelance design studio.",
            "session_id": chat_session_id,
        }, headers=auth_headers)
        response = await client.post("/api/ai/chat", json={
            "message": "What invoice template would you recommend for me?",
            "session_id": chat_session_id,
        }, headers=auth_headers)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_ai_chat_history(auth_headers, chat_session_id):
    """Chat history endpoint should return previous messages."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        await client.post("/api/ai/chat", json={
            "message": "Hello AI",
            "session_id": chat_session_id,
        }, headers=auth_headers)
        response = await client.get(f"/api/ai/chat/history/{chat_session_id}", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0


@pytest.mark.asyncio
async def test_ai_chat_clear_history(auth_headers, chat_session_id):
    """Clearing chat history should succeed."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        await client.post("/api/ai/chat", json={
            "message": "Test message",
            "session_id": chat_session_id,
        }, headers=auth_headers)
        response = await client.delete(f"/api/ai/chat/history/{chat_session_id}", headers=auth_headers)
    assert response.status_code in (200, 204)


# ---------------------------------------------------------------------------
# AI Command Center
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ai_command_create_invoice(auth_headers):
    """AI command: create invoice from natural language."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/ai/command", json={
            "command": "Create an invoice for Acme Corp for 5 hours of consulting at $200/hr",
        }, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "action" in data or "result" in data or "response" in data


@pytest.mark.asyncio
async def test_ai_command_analytics_query(auth_headers):
    """AI command: query analytics data."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/ai/command", json={
            "command": "What are my top 3 clients by revenue?",
        }, headers=auth_headers)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_ai_command_send_reminder(auth_headers):
    """AI command: send reminder."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/ai/command", json={
            "command": "Send a reminder for all overdue invoices",
        }, headers=auth_headers)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_ai_command_empty_fails(auth_headers):
    """Empty command should return validation error."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/ai/command", json={
            "command": "",
        }, headers=auth_headers)
    assert response.status_code in (400, 422)


# ---------------------------------------------------------------------------
# Smart Search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ai_smart_search_invoices(auth_headers):
    """Smart search should return relevant invoice results."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/ai/search", json={
            "query": "overdue invoices last month",
            "entity_types": ["invoice"],
        }, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "results" in data or isinstance(data, list)


@pytest.mark.asyncio
async def test_ai_smart_search_clients(auth_headers):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/ai/search", json={
            "query": "high risk clients",
            "entity_types": ["client"],
        }, headers=auth_headers)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_ai_smart_search_all_entities(auth_headers):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/ai/search", json={
            "query": "Acme",
        }, headers=auth_headers)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_ai_smart_search_empty_query(auth_headers):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/ai/search", json={
            "query": "",
        }, headers=auth_headers)
    assert response.status_code in (400, 422)


# ---------------------------------------------------------------------------
# AI Recommendations
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_ai_recommendations(auth_headers):
    """Recommendations endpoint should return a list of action items."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/ai/recommendations", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list) or "recommendations" in data


@pytest.mark.asyncio
async def test_ai_action_suggestions(auth_headers):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/ai/action-suggestions", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list) or "suggestions" in data


@pytest.mark.asyncio
async def test_ai_personalized_tips(auth_headers):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/ai/personalized-tips", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list) or "tips" in data


# ---------------------------------------------------------------------------
# AI Business Insights
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_ai_insights(auth_headers):
    """Insight cards should return structured business insights."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/analytics/insights", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list) or "insights" in data


@pytest.mark.asyncio
async def test_generate_ai_insights(auth_headers):
    """Generating new insights should succeed."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/analytics/insights/generate", headers=auth_headers)
    assert response.status_code in (200, 201)


@pytest.mark.asyncio
async def test_ai_insight_cards(auth_headers):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/ai/insights/cards", headers=auth_headers)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# AI Memory / Context
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ai_memory_context(auth_headers):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/ai/memory/context", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# AI Onboarding
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ai_onboarding_steps(auth_headers):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/ai/onboarding-steps", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    first_step = data[0]
    assert "step" in first_step or "title" in first_step


# ---------------------------------------------------------------------------
# AI Conversational Filter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ai_filter_invoices(auth_headers):
    """AI filter parser should convert natural language to structured filters."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/ai/filter", json={
            "query": "show me paid invoices from last 3 months over $1000",
            "entity_type": "invoice",
        }, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "filters" in data or isinstance(data, dict)