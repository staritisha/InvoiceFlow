import pytest
import uuid
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.database import get_db, Base

TEST_DATABASE_URL = "sqlite:///./test_invoiceflow.db"
engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

TEST_EMAIL = f"test_{uuid.uuid4().hex[:8]}@example.com"
TEST_PASSWORD = "TestPass123"

@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    try:
        Base.metadata.drop_all(bind=engine)
    except Exception:
        pass

@pytest.fixture
def client():
    return TestClient(app)

def test_register(client):
    response = client.post("/api/v1/auth/register", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD,
        "full_name": "Test User",
    })
    assert response.status_code == 200
    assert "id" in response.json()

def test_login(client):
    response = client.post("/api/v1/auth/login", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD,
    })
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data

def test_get_me(client):
    login = client.post("/api/v1/auth/login", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD,
    })
    token = login.json()["access_token"]
    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["email"] == TEST_EMAIL

def test_invalid_login(client):
    response = client.post("/api/v1/auth/login", json={
        "email": "wrong@example.com",
        "password": "WrongPass123",
    })
    assert response.status_code == 401