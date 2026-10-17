import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SECRET_KEY"] = "test-secret"
# Unit tests must never touch real AWS: force local storage before app import.
# S3-mode tests explicitly override app.db module attributes at runtime.
os.environ["STORAGE_BACKEND"] = "local"
os.environ["AI_API_KEY"] = ""

from fastapi.testclient import TestClient
import pytest

from app.db import Base, SessionLocal, engine, get_db
from app.main import app
from app import seed


@pytest.fixture(scope="session")
def client():
    Base.metadata.create_all(engine)

    def override():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


@pytest.fixture(scope="session")
def seeded(client):
    seed.run()
    return client


def login(client, email):
    r = client.post("/api/auth/login", json={"email": email})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}
