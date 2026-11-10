"""Shared pytest fixtures."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator

import pytest

# Point the app at a throwaway SQLite file BEFORE importing anything that reads settings.
_tmpdir = tempfile.mkdtemp(prefix="nigrani-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmpdir}/test.db"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["RATE_LIMIT_PER_MINUTE"] = "0"  # disable limiting in tests
os.environ["SEED_ADMIN_EMAIL"] = "admin@example.com"
os.environ["SEED_ADMIN_PASSWORD"] = "TestPass123!"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _create_schema() -> Generator[None, None, None]:
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth_headers(client: TestClient) -> dict[str, str]:
    """Register a fresh user and return bearer headers."""
    import uuid

    email = f"user-{uuid.uuid4().hex[:10]}@example.com"
    res = client.post(
        "/api/auth/register",
        json={"email": email, "full_name": "Test Engineer", "password": "TestPass123!"},
    )
    assert res.status_code == 201, res.text
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def plant(client: TestClient, auth_headers: dict[str, str]) -> dict:
    res = client.post(
        "/api/plants",
        json={"name": "Karachi Plant 1", "city": "Karachi"},
        headers=auth_headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


@pytest.fixture()
def machine(client: TestClient, auth_headers: dict[str, str], plant: dict) -> dict:
    import uuid

    res = client.post(
        "/api/machines",
        json={
            "plant_id": plant["id"],
            "name": "Cooling water pump 2",
            "asset_tag": f"KHI-PMP-{uuid.uuid4().hex[:6].upper()}",
            "machine_type": "pump",
            "power_class": 2,
            "baseline_vibration": 2.8,
            "baseline_temperature": 62.0,
            "baseline_current": 10.0,
        },
        headers=auth_headers,
    )
    assert res.status_code == 201, res.text
    return res.json()
