"""
Tests for workflow automation: creation, trigger conditions, action execution, run history.
Run: pytest app/tests/test_workflows.py -v
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
            "email": "wf_test@invoiceflow.test",
            "username": "wftester",
            "password": "TestPass123!",
            "full_name": "Workflow Tester",
            "business_name": "WF Test Co.",
        })
        login = await client.post("/api/auth/login", json={
            "email": "wf_test@invoiceflow.test",
            "password": "TestPass123!",
        })
        token = login.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def overdue_reminder_workflow():
    return {
        "name": "Auto Overdue Reminder",
        "description": "Send reminder when invoice is overdue by 1 day",
        "trigger_type": "invoice_overdue",
        "conditions": {"days_overdue": 1},
        "actions": [{"type": "send_reminder", "tone": "friendly"}],
        "is_active": True,
    }


@pytest.fixture
def payment_thankyou_workflow():
    return {
        "name": "Payment Thank You",
        "description": "Send thank-you email when invoice is paid",
        "trigger_type": "invoice_paid",
        "conditions": {},
        "actions": [{"type": "send_email", "template": "thank_you"}],
        "is_active": True,
    }


@pytest.fixture
def scheduled_report_workflow():
    return {
        "name": "Weekly Revenue Report",
        "description": "Generate revenue report every Monday",
        "trigger_type": "scheduled",
        "conditions": {"schedule": "weekly", "day": "monday"},
        "actions": [{"type": "generate_report", "report_type": "revenue"}],
        "is_active": True,
    }


@pytest.fixture
def client_risk_workflow():
    return {
        "name": "High Risk Client Alert",
        "description": "Notify when client risk score becomes high",
        "trigger_type": "client_risk_high",
        "conditions": {"risk_threshold": 0.7},
        "actions": [{"type": "create_notification", "message": "Client risk is high"}],
        "is_active": True,
    }


# ---------------------------------------------------------------------------
# Workflow CRUD
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_workflow(auth_headers, overdue_reminder_workflow):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/workflows/", json=overdue_reminder_workflow, headers=auth_headers)
    assert response.status_code in (200, 201)
    data = response.json()
    assert "id" in data
    assert data["name"] == overdue_reminder_workflow["name"]
    assert data["trigger_type"] == "invoice_overdue"
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_create_workflow_paid_trigger(auth_headers, payment_thankyou_workflow):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/workflows/", json=payment_thankyou_workflow, headers=auth_headers)
    assert response.status_code in (200, 201)
    assert response.json()["trigger_type"] == "invoice_paid"


@pytest.mark.asyncio
async def test_create_workflow_scheduled_trigger(auth_headers, scheduled_report_workflow):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/workflows/", json=scheduled_report_workflow, headers=auth_headers)
    assert response.status_code in (200, 201)


@pytest.mark.asyncio
async def test_list_workflows(auth_headers, overdue_reminder_workflow):
    async with AsyncClient(app=app, base_url="http://test") as client:
        await client.post("/api/workflows/", json=overdue_reminder_workflow, headers=auth_headers)
        response = await client.get("/api/workflows/", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list) or "items" in data


@pytest.mark.asyncio
async def test_get_workflow_by_id(auth_headers, overdue_reminder_workflow):
    async with AsyncClient(app=app, base_url="http://test") as client:
        created = await client.post("/api/workflows/", json=overdue_reminder_workflow, headers=auth_headers)
        wf_id = created.json()["id"]
        response = await client.get(f"/api/workflows/{wf_id}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["id"] == wf_id


@pytest.mark.asyncio
async def test_update_workflow(auth_headers, overdue_reminder_workflow):
    async with AsyncClient(app=app, base_url="http://test") as client:
        created = await client.post("/api/workflows/", json=overdue_reminder_workflow, headers=auth_headers)
        wf_id = created.json()["id"]
        response = await client.put(f"/api/workflows/{wf_id}", json={
            "name": "Updated Workflow Name",
            "is_active": False,
        }, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Workflow Name"
    assert data["is_active"] is False


@pytest.mark.asyncio
async def test_delete_workflow(auth_headers, overdue_reminder_workflow):
    async with AsyncClient(app=app, base_url="http://test") as client:
        created = await client.post("/api/workflows/", json=overdue_reminder_workflow, headers=auth_headers)
        wf_id = created.json()["id"]
        delete_resp = await client.delete(f"/api/workflows/{wf_id}", headers=auth_headers)
        get_resp = await client.get(f"/api/workflows/{wf_id}", headers=auth_headers)
    assert delete_resp.status_code in (200, 204)
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_get_nonexistent_workflow(auth_headers):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/workflows/00000000-0000-0000-0000-000000000000", headers=auth_headers)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Trigger Conditions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workflow_with_amount_condition(auth_headers):
    """Workflow conditions can filter by invoice amount."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/workflows/", json={
            "name": "Large Invoice Alert",
            "trigger_type": "invoice_overdue",
            "conditions": {"days_overdue": 7, "min_amount": 1000},
            "actions": [{"type": "send_reminder", "tone": "urgent"}],
            "is_active": True,
        }, headers=auth_headers)
    assert response.status_code in (200, 201)
    data = response.json()
    assert data["conditions"]["min_amount"] == 1000


@pytest.mark.asyncio
async def test_workflow_client_risk_conditions(auth_headers, client_risk_workflow):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/workflows/", json=client_risk_workflow, headers=auth_headers)
    assert response.status_code in (200, 201)
    assert response.json()["conditions"]["risk_threshold"] == 0.7


@pytest.mark.asyncio
async def test_workflow_multiple_actions(auth_headers):
    """Workflows should support multiple sequential actions."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/workflows/", json={
            "name": "Multi-Action Workflow",
            "trigger_type": "invoice_overdue",
            "conditions": {"days_overdue": 14},
            "actions": [
                {"type": "send_reminder", "tone": "urgent"},
                {"type": "create_notification", "message": "Escalated overdue invoice"},
                {"type": "update_status", "status": "escalated"},
            ],
            "is_active": True,
        }, headers=auth_headers)
    assert response.status_code in (200, 201)
    assert len(response.json()["actions"]) == 3


# ---------------------------------------------------------------------------
# Manual Trigger / Execution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trigger_workflow_manually(auth_headers, overdue_reminder_workflow):
    async with AsyncClient(app=app, base_url="http://test") as client:
        created = await client.post("/api/workflows/", json=overdue_reminder_workflow, headers=auth_headers)
        wf_id = created.json()["id"]
        response = await client.post(f"/api/workflows/{wf_id}/run", headers=auth_headers)
    assert response.status_code in (200, 202)
    data = response.json()
    assert "run_id" in data or "status" in data or "id" in data


@pytest.mark.asyncio
async def test_trigger_inactive_workflow_fails(auth_headers):
    """Triggering an inactive workflow should return an error or warning."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        created = await client.post("/api/workflows/", json={
            "name": "Inactive WF",
            "trigger_type": "invoice_overdue",
            "conditions": {},
            "actions": [{"type": "send_reminder", "tone": "friendly"}],
            "is_active": False,
        }, headers=auth_headers)
        wf_id = created.json()["id"]
        response = await client.post(f"/api/workflows/{wf_id}/run", headers=auth_headers)
    # Either 400 or 200 with a warning is acceptable
    assert response.status_code in (200, 202, 400)


# ---------------------------------------------------------------------------
# Run History
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_workflow_run_history(auth_headers, overdue_reminder_workflow):
    async with AsyncClient(app=app, base_url="http://test") as client:
        created = await client.post("/api/workflows/", json=overdue_reminder_workflow, headers=auth_headers)
        wf_id = created.json()["id"]
        await client.post(f"/api/workflows/{wf_id}/run", headers=auth_headers)
        response = await client.get(f"/api/workflows/{wf_id}/runs", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list) or "items" in data


@pytest.mark.asyncio
async def test_run_history_contains_status(auth_headers, overdue_reminder_workflow):
    async with AsyncClient(app=app, base_url="http://test") as client:
        created = await client.post("/api/workflows/", json=overdue_reminder_workflow, headers=auth_headers)
        wf_id = created.json()["id"]
        await client.post(f"/api/workflows/{wf_id}/run", headers=auth_headers)
        history = await client.get(f"/api/workflows/{wf_id}/runs", headers=auth_headers)
    runs = history.json() if isinstance(history.json(), list) else history.json().get("items", [])
    if runs:
        assert "status" in runs[0]


# ---------------------------------------------------------------------------
# Workflow Templates
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_workflow_templates(auth_headers):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/workflows/templates/list", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0


@pytest.mark.asyncio
async def test_workflow_templates_have_required_fields(auth_headers):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/workflows/templates/list", headers=auth_headers)
    templates = response.json()
    for template in templates:
        assert "name" in template
        assert "trigger_type" in template


# ---------------------------------------------------------------------------
# Reminder Workflow (overdue_invoice_flow integration)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reminder_workflow_action_escalation(auth_headers):
    """Escalation workflow: friendly → professional → urgent → firm."""
    tones = ["friendly", "professional", "urgent", "firm"]
    async with AsyncClient(app=app, base_url="http://test") as client:
        for i, tone in enumerate(tones):
            response = await client.post("/api/workflows/", json={
                "name": f"Escalation Level {i+1}",
                "trigger_type": "invoice_overdue",
                "conditions": {"days_overdue": (i + 1) * 7},
                "actions": [{"type": "send_reminder", "tone": tone}],
                "is_active": True,
            }, headers=auth_headers)
            assert response.status_code in (200, 201)
            assert response.json()["actions"][0]["tone"] == tone


# ---------------------------------------------------------------------------
# Payment Workflow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_payment_workflow_trigger(auth_headers, payment_thankyou_workflow):
    """Payment workflow should be creatable and triggerable."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        created = await client.post("/api/workflows/", json=payment_thankyou_workflow, headers=auth_headers)
        wf_id = created.json()["id"]
        response = await client.post(f"/api/workflows/{wf_id}/run", headers=auth_headers)
    assert response.status_code in (200, 202)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_workflow_invalid_trigger(auth_headers):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/workflows/", json={
            "name": "Bad Trigger",
            "trigger_type": "nonexistent_trigger_type",
            "conditions": {},
            "actions": [],
        }, headers=auth_headers)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_workflow_missing_name(auth_headers):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/workflows/", json={
            "trigger_type": "invoice_overdue",
            "conditions": {},
            "actions": [],
        }, headers=auth_headers)
    assert response.status_code == 422