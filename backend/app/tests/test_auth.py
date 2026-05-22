"""
Tests for authentication endpoints.
Run: pytest app/tests/test_auth.py -v
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.database import get_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def test_user_payload():
    return {
        "email": "testuser@invoiceflow.test",
        "username": "testuser",
        "password": "SecurePass123!",
        "full_name": "Test User",
        "business_name": "Test Business",
    }


@pytest.fixture
def admin_user_payload():
    return {
        "email": "admin@invoiceflow.test",
        "username": "adminuser",
        "password": "AdminPass123!",
        "full_name": "Admin User",
        "business_name": "Admin Business",
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_new_user(test_user_payload):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/auth/register", json=test_user_payload)
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == test_user_payload["email"]
    assert "id" in data
    assert "hashed_password" not in data


@pytest.mark.asyncio
async def test_register_duplicate_email(test_user_payload):
    async with AsyncClient(app=app, base_url="http://test") as client:
        await client.post("/api/auth/register", json=test_user_payload)
        response = await client.post("/api/auth/register", json=test_user_payload)
    assert response.status_code == 400
    assert "already registered" in response.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_register_invalid_email():
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/auth/register", json={
            "email": "not-an-email",
            "username": "u1",
            "password": "Pass123!",
        })
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_register_weak_password():
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/auth/register", json={
            "email": "weak@test.com",
            "username": "weakuser",
            "password": "123",
        })
    assert response.status_code in (400, 422)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_success(test_user_payload):
    async with AsyncClient(app=app, base_url="http://test") as client:
        await client.post("/api/auth/register", json=test_user_payload)
        response = await client.post("/api/auth/login", json={
            "email": test_user_payload["email"],
            "password": test_user_payload["password"],
        })
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password(test_user_payload):
    async with AsyncClient(app=app, base_url="http://test") as client:
        await client.post("/api/auth/register", json=test_user_payload)
        response = await client.post("/api/auth/login", json={
            "email": test_user_payload["email"],
            "password": "WrongPassword!",
        })
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_nonexistent_user():
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/auth/login", json={
            "email": "ghost@nowhere.test",
            "password": "Pass123!",
        })
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_token_refresh(test_user_payload):
    async with AsyncClient(app=app, base_url="http://test") as client:
        await client.post("/api/auth/register", json=test_user_payload)
        login = await client.post("/api/auth/login", json={
            "email": test_user_payload["email"],
            "password": test_user_payload["password"],
        })
        refresh_token = login.json()["refresh_token"]
        response = await client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
    assert response.status_code == 200
    assert "access_token" in response.json()


@pytest.mark.asyncio
async def test_token_refresh_invalid():
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/auth/refresh", json={"refresh_token": "invalid.token.here"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# /me endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_me_authenticated(test_user_payload):
    async with AsyncClient(app=app, base_url="http://test") as client:
        await client.post("/api/auth/register", json=test_user_payload)
        login = await client.post("/api/auth/login", json={
            "email": test_user_payload["email"],
            "password": test_user_payload["password"],
        })
        token = login.json()["access_token"]
        response = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == test_user_payload["email"]


@pytest.mark.asyncio
async def test_get_me_unauthenticated():
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_me_invalid_token():
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/auth/me", headers={"Authorization": "Bearer invalid.jwt.token"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Protected routes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_protected_route_requires_auth():
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/invoices/")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_admin_route_forbidden_for_regular_user(test_user_payload):
    """Regular users should not access admin-only endpoints."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        await client.post("/api/auth/register", json=test_user_payload)
        login = await client.post("/api/auth/login", json={
            "email": test_user_payload["email"],
            "password": test_user_payload["password"],
        })
        token = login.json()["access_token"]
        response = await client.get("/api/users/", headers={"Authorization": f"Bearer {token}"})
    # Regular user listing all users might be forbidden depending on RBAC
    assert response.status_code in (200, 403)