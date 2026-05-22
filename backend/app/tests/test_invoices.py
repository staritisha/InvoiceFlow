"""
Tests for invoice endpoints: CRUD, payment, duplicate, AI generate, voice, recurring, themes.
Run: pytest app/tests/test_invoices.py -v
"""

import pytest
from httpx import AsyncClient
from app.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def auth_headers():
    """Register and login a test user, return auth headers."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        await client.post("/api/auth/register", json={
            "email": "inv_test@invoiceflow.test",
            "username": "invtester",
            "password": "TestPass123!",
            "full_name": "Invoice Tester",
            "business_name": "Tester Co.",
        })
        login = await client.post("/api/auth/login", json={
            "email": "inv_test@invoiceflow.test",
            "password": "TestPass123!",
        })
        token = login.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def test_client_id(auth_headers):
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post("/api/clients/", json={
            "name": "Test Client",
            "email": "client@test.example.com",
            "company": "Test Corp",
        }, headers=auth_headers)
        return resp.json()["id"]


@pytest.fixture
def invoice_payload():
    return {
        "description": "Web Development Services",
        "currency": "USD",
        "tax_rate": 10.0,
        "theme": "modern",
        "items": [
            {"description": "Frontend Development", "quantity": 10, "rate": 100.0},
            {"description": "Backend API", "quantity": 5, "rate": 150.0},
        ],
        "due_date": "2026-06-30T00:00:00Z",
        "notes": "Thank you for your business.",
        "terms": "Net 30",
    }


# ---------------------------------------------------------------------------
# Create Invoice
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_invoice(auth_headers, test_client_id, invoice_payload):
    invoice_payload["client_id"] = test_client_id
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/invoices/", json=invoice_payload, headers=auth_headers)
    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert data["status"] == "pending"
    assert data["total"] > 0


@pytest.mark.asyncio
async def test_create_invoice_calculates_totals(auth_headers, test_client_id, invoice_payload):
    invoice_payload["client_id"] = test_client_id
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/invoices/", json=invoice_payload, headers=auth_headers)
    data = response.json()
    # 10*100 + 5*150 = 1750 subtotal, 10% tax = 175, total = 1925
    assert data["subtotal"] == pytest.approx(1750.0)
    assert data["tax_amount"] == pytest.approx(175.0)
    assert data["total"] == pytest.approx(1925.0)
    assert data["balance_due"] == pytest.approx(1925.0)


@pytest.mark.asyncio
async def test_create_invoice_missing_client(auth_headers, invoice_payload):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/invoices/", json=invoice_payload, headers=auth_headers)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Read Invoice
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_invoice(auth_headers, test_client_id, invoice_payload):
    invoice_payload["client_id"] = test_client_id
    async with AsyncClient(app=app, base_url="http://test") as client:
        created = await client.post("/api/invoices/", json=invoice_payload, headers=auth_headers)
        invoice_id = created.json()["id"]
        response = await client.get(f"/api/invoices/{invoice_id}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["id"] == invoice_id


@pytest.mark.asyncio
async def test_list_invoices(auth_headers):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/invoices/", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "items" in data or isinstance(data, list)


@pytest.mark.asyncio
async def test_get_nonexistent_invoice(auth_headers):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/invoices/00000000-0000-0000-0000-000000000000", headers=auth_headers)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Update Invoice
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_invoice(auth_headers, test_client_id, invoice_payload):
    invoice_payload["client_id"] = test_client_id
    async with AsyncClient(app=app, base_url="http://test") as client:
        created = await client.post("/api/invoices/", json=invoice_payload, headers=auth_headers)
        invoice_id = created.json()["id"]
        response = await client.put(f"/api/invoices/{invoice_id}", json={"notes": "Updated note"}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["notes"] == "Updated note"


# ---------------------------------------------------------------------------
# Delete Invoice
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_invoice(auth_headers, test_client_id, invoice_payload):
    invoice_payload["client_id"] = test_client_id
    async with AsyncClient(app=app, base_url="http://test") as client:
        created = await client.post("/api/invoices/", json=invoice_payload, headers=auth_headers)
        invoice_id = created.json()["id"]
        delete_resp = await client.delete(f"/api/invoices/{invoice_id}", headers=auth_headers)
        get_resp = await client.get(f"/api/invoices/{invoice_id}", headers=auth_headers)
    assert delete_resp.status_code == 204
    assert get_resp.status_code == 404


# ---------------------------------------------------------------------------
# Payment
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_full_payment(auth_headers, test_client_id, invoice_payload):
    invoice_payload["client_id"] = test_client_id
    async with AsyncClient(app=app, base_url="http://test") as client:
        created = await client.post("/api/invoices/", json=invoice_payload, headers=auth_headers)
        invoice_id = created.json()["id"]
        total = created.json()["total"]
        response = await client.post(f"/api/invoices/{invoice_id}/pay", json={
            "amount": total,
            "method": "bank_transfer",
        }, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "paid"


@pytest.mark.asyncio
async def test_record_partial_payment(auth_headers, test_client_id, invoice_payload):
    invoice_payload["client_id"] = test_client_id
    async with AsyncClient(app=app, base_url="http://test") as client:
        created = await client.post("/api/invoices/", json=invoice_payload, headers=auth_headers)
        invoice_id = created.json()["id"]
        response = await client.post(f"/api/invoices/{invoice_id}/pay", json={
            "amount": 500.0,
            "method": "cash",
        }, headers=auth_headers)
    data = response.json()
    assert response.status_code == 200
    assert data["status"] == "partial"
    assert data["amount_paid"] == pytest.approx(500.0)


# ---------------------------------------------------------------------------
# Duplicate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_invoice(auth_headers, test_client_id, invoice_payload):
    invoice_payload["client_id"] = test_client_id
    async with AsyncClient(app=app, base_url="http://test") as client:
        created = await client.post("/api/invoices/", json=invoice_payload, headers=auth_headers)
        invoice_id = created.json()["id"]
        response = await client.post(f"/api/invoices/{invoice_id}/duplicate", headers=auth_headers)
    assert response.status_code == 201
    dup = response.json()
    assert dup["id"] != invoice_id
    assert dup["number"] != created.json()["number"]
    assert dup["status"] == "pending"


# ---------------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_invoice_themes(auth_headers):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/invoices/themes/list", headers=auth_headers)
    assert response.status_code == 200
    themes = response.json()
    assert len(themes) >= 6
    theme_names = [t["name"] if isinstance(t, dict) else t for t in themes]
    assert "modern" in theme_names


# ---------------------------------------------------------------------------
# AI Generation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ai_generate_invoice(auth_headers, test_client_id):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/invoices/ai/generate", json={
            "prompt": f"Invoice client {test_client_id} for 10 hours web development at $120 per hour due in 30 days",
            "client_id": test_client_id,
        }, headers=auth_headers)
    assert response.status_code in (200, 201)
    data = response.json()
    assert "items" in data or "id" in data


# ---------------------------------------------------------------------------
# Recurring Invoices
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_recurring_invoice(auth_headers, test_client_id, invoice_payload):
    invoice_payload["client_id"] = test_client_id
    async with AsyncClient(app=app, base_url="http://test") as client:
        created = await client.post("/api/invoices/", json=invoice_payload, headers=auth_headers)
        invoice_id = created.json()["id"]
        response = await client.post("/api/invoices/recurring", json={
            "template_invoice_id": invoice_id,
            "frequency": "monthly",
            "start_date": "2026-06-01T00:00:00Z",
        }, headers=auth_headers)
    assert response.status_code in (200, 201)
    data = response.json()
    assert data["frequency"] == "monthly"
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_list_recurring_invoices(auth_headers):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/invoices/recurring/list", headers=auth_headers)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Send Invoice
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_invoice(auth_headers, test_client_id, invoice_payload):
    invoice_payload["client_id"] = test_client_id
    async with AsyncClient(app=app, base_url="http://test") as client:
        created = await client.post("/api/invoices/", json=invoice_payload, headers=auth_headers)
        invoice_id = created.json()["id"]
        response = await client.post(f"/api/invoices/{invoice_id}/send", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "sent"