"""API tests — auth, ownership isolation, and the alert lifecycle.

The ownership tests are the important ones. A maintenance platform that leaks
one plant's machine list to another plant's engineer is not a product.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

# Point at a throwaway SQLite file before importing the app.
_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"
os.environ["JWT_SECRET"] = "test-secret-not-for-production-use-only-in-tests-0123456789"

from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    init_db()
    with TestClient(app) as c:
        yield c


def register(client, email: str) -> str:
    res = client.post(
        "/api/auth/register",
        json={"email": email, "password": "correct-horse-battery", "display_name": "Test Engineer"},
    )
    assert res.status_code == 201, res.text
    return res.json()["access_token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def make_machine(client, token: str, tag: str = "PMP-900") -> int:
    res = client.post(
        "/api/machines",
        headers=auth(token),
        json={"asset_tag": tag, "name": "Test pump", "line": "Line Z", "machine_type": "pump"},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


# ── meta ─────────────────────────────────────────────────────────


def test_health_endpoint(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_security_headers_present(client):
    res = client.get("/api/health")
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "DENY"


# ── auth ─────────────────────────────────────────────────────────


def test_register_returns_token(client):
    token = register(client, "a@plant.pk")
    assert token


def test_duplicate_email_is_409(client):
    register(client, "dup@plant.pk")
    res = client.post(
        "/api/auth/register",
        json={"email": "dup@plant.pk", "password": "another-password", "display_name": "X"},
    )
    assert res.status_code == 409


def test_short_password_is_rejected(client):
    res = client.post(
        "/api/auth/register",
        json={"email": "short@plant.pk", "password": "abc", "display_name": "X"},
    )
    assert res.status_code == 422


def test_login_works(client):
    register(client, "login@plant.pk")
    res = client.post(
        "/api/auth/login",
        json={"email": "login@plant.pk", "password": "correct-horse-battery"},
    )
    assert res.status_code == 200
    assert res.json()["access_token"]


def test_wrong_password_is_401(client):
    register(client, "wrong@plant.pk")
    res = client.post(
        "/api/auth/login",
        json={"email": "wrong@plant.pk", "password": "not-the-password"},
    )
    assert res.status_code == 401


def test_unknown_email_gives_same_message_as_wrong_password(client):
    """Do not leak which half of the credential was wrong."""
    register(client, "known@plant.pk")
    a = client.post("/api/auth/login", json={"email": "known@plant.pk", "password": "nope-nope-nope"})
    b = client.post("/api/auth/login", json={"email": "ghost@plant.pk", "password": "nope-nope-nope"})
    assert a.status_code == b.status_code == 401
    assert a.json()["detail"] == b.json()["detail"]


def test_me_requires_token(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_returns_user(client):
    token = register(client, "me@plant.pk")
    res = client.get("/api/auth/me", headers=auth(token))
    assert res.status_code == 200
    assert res.json()["email"] == "me@plant.pk"


def test_garbage_token_is_401(client):
    res = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert res.status_code == 401


# ── machines ─────────────────────────────────────────────────────


def test_create_and_list_machine(client):
    token = register(client, "m1@plant.pk")
    mid = make_machine(client, token, "PMP-001")
    res = client.get("/api/machines", headers=auth(token))
    assert res.status_code == 200
    assert [m["id"] for m in res.json()] == [mid]


def test_duplicate_asset_tag_is_409(client):
    token = register(client, "m2@plant.pk")
    make_machine(client, token, "PMP-002")
    res = client.post(
        "/api/machines",
        headers=auth(token),
        json={"asset_tag": "PMP-002", "name": "Another", "line": "A", "machine_type": "pump"},
    )
    assert res.status_code == 409


def test_critical_must_exceed_nominal(client):
    token = register(client, "m3@plant.pk")
    res = client.post(
        "/api/machines",
        headers=auth(token),
        json={
            "asset_tag": "BAD-1",
            "name": "Bad",
            "line": "A",
            "machine_type": "pump",
            "nominal_vibration": 10.0,
            "critical_vibration": 5.0,
        },
    )
    assert res.status_code == 422


def test_other_users_machine_is_404_not_403(client):
    """404, not 403 — do not confirm that someone else's machine exists."""
    owner = register(client, "owner@plant.pk")
    intruder = register(client, "intruder@plant.pk")
    mid = make_machine(client, owner, "PMP-OWN")

    res = client.get(f"/api/machines/{mid}", headers=auth(intruder))
    assert res.status_code == 404


def test_other_users_machine_cannot_be_deleted(client):
    owner = register(client, "owner2@plant.pk")
    intruder = register(client, "intruder2@plant.pk")
    mid = make_machine(client, owner, "PMP-OWN2")

    assert client.delete(f"/api/machines/{mid}", headers=auth(intruder)).status_code == 404
    assert client.get(f"/api/machines/{mid}", headers=auth(owner)).status_code == 200


# ── readings and health ──────────────────────────────────────────


def test_reading_returns_health(client):
    token = register(client, "r1@plant.pk")
    mid = make_machine(client, token, "PMP-R1")

    res = client.post(
        f"/api/machines/{mid}/readings",
        headers=auth(token),
        json={"vibration_rms": 2.1, "temperature_c": 46.0, "current_a": 12.1},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["health"] > 90.0
    assert body["reading_count"] == 1
    assert len(body["channels"]) == 3


def test_health_with_no_readings_is_unknown_not_crash(client):
    token = register(client, "r2@plant.pk")
    mid = make_machine(client, token, "PMP-R2")
    res = client.get(f"/api/machines/{mid}/health", headers=auth(token))
    assert res.status_code == 200
    assert res.json()["reading_count"] == 0


def test_negative_vibration_is_rejected(client):
    token = register(client, "r3@plant.pk")
    mid = make_machine(client, token, "PMP-R3")
    res = client.post(
        f"/api/machines/{mid}/readings",
        headers=auth(token),
        json={"vibration_rms": -5.0, "temperature_c": 45.0, "current_a": 12.0},
    )
    assert res.status_code == 422


def test_degrading_machine_raises_alert(client):
    token = register(client, "r4@plant.pk")
    mid = make_machine(client, token, "PMP-R4")

    # Walk vibration from nominal to past critical.
    for vib in [2.0, 3.5, 5.0, 6.5, 8.0, 9.5, 10.8, 11.2]:
        client.post(
            f"/api/machines/{mid}/readings",
            headers=auth(token),
            json={"vibration_rms": vib, "temperature_c": 45.0, "current_a": 12.0},
        )

    alerts = client.get("/api/alerts?open_only=true", headers=auth(token)).json()
    assert len(alerts) >= 1
    assert alerts[0]["severity"] in {"warning", "critical", "emergency"}


def test_alert_auto_resolves_when_machine_recovers(client):
    token = register(client, "r5@plant.pk")
    mid = make_machine(client, token, "PMP-R5")

    for vib in [2.0, 5.0, 8.0, 10.5]:
        client.post(
            f"/api/machines/{mid}/readings",
            headers=auth(token),
            json={"vibration_rms": vib, "temperature_c": 45.0, "current_a": 12.0},
        )
    assert len(client.get("/api/alerts", headers=auth(token)).json()) >= 1

    # Back to nominal, several times, so the trend window is healthy again.
    for _ in range(12):
        client.post(
            f"/api/machines/{mid}/readings",
            headers=auth(token),
            json={"vibration_rms": 2.0, "temperature_c": 45.0, "current_a": 12.0},
        )

    open_alerts = client.get("/api/alerts?open_only=true", headers=auth(token)).json()
    assert open_alerts == []


def test_resolve_alert_requires_note(client):
    token = register(client, "r6@plant.pk")
    mid = make_machine(client, token, "PMP-R6")
    for vib in [2.0, 6.0, 9.0, 11.0]:
        client.post(
            f"/api/machines/{mid}/readings",
            headers=auth(token),
            json={"vibration_rms": vib, "temperature_c": 45.0, "current_a": 12.0},
        )
    alert = client.get("/api/alerts", headers=auth(token)).json()[0]

    res = client.post(f"/api/alerts/{alert['id']}/resolve", headers=auth(token), json={"note": ""})
    assert res.status_code == 422

    res = client.post(
        f"/api/alerts/{alert['id']}/resolve",
        headers=auth(token),
        json={"note": "Replaced bearing"},
    )
    assert res.status_code == 200
    assert res.json()["resolved_note"] == "Replaced bearing"


def test_forecast_returns_points(client):
    token = register(client, "r7@plant.pk")
    mid = make_machine(client, token, "PMP-R7")
    for vib in [2.0, 3.0, 4.0, 5.0, 6.0]:
        client.post(
            f"/api/machines/{mid}/readings",
            headers=auth(token),
            json={"vibration_rms": vib, "temperature_c": 45.0, "current_a": 12.0},
        )
    res = client.get(f"/api/machines/{mid}/forecast?hours=48", headers=auth(token))
    assert res.status_code == 200
    body = res.json()
    assert len(body["points"]) > 1
    assert "not a trained forecast" in body["note"].lower()


def test_maintenance_log_roundtrip(client):
    token = register(client, "r8@plant.pk")
    mid = make_machine(client, token, "PMP-R8")
    res = client.post(
        f"/api/machines/{mid}/maintenance",
        headers=auth(token),
        json={"action": "Replaced bearing", "notes": "Wear ring visible", "downtime_minutes": 45},
    )
    assert res.status_code == 201
    logs = client.get(f"/api/machines/{mid}/maintenance", headers=auth(token)).json()
    assert len(logs) == 1
    assert logs[0]["downtime_minutes"] == 45


# ── fleet ────────────────────────────────────────────────────────


def test_fleet_summary_counts(client):
    token = register(client, "f1@plant.pk")
    make_machine(client, token, "PMP-F1")
    make_machine(client, token, "PMP-F2")
    res = client.get("/api/fleet", headers=auth(token))
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    assert body["healthy"] == 2


def test_fleet_is_scoped_to_owner(client):
    a = register(client, "fa@plant.pk")
    b = register(client, "fb@plant.pk")
    make_machine(client, a, "PMP-FA")
    make_machine(client, b, "PMP-FB")

    assert client.get("/api/fleet", headers=auth(a)).json()["total"] == 1
    assert client.get("/api/fleet", headers=auth(b)).json()["total"] == 1


# ── demo seeding ─────────────────────────────────────────────────


def test_seed_demo_creates_fleet_once(client):
    token = register(client, "seed@plant.pk")
    first = client.post("/api/seed-demo", headers=auth(token), json={})
    assert first.status_code == 201
    assert first.json()["created"] == 5

    second = client.post("/api/seed-demo", headers=auth(token), json={})
    assert second.json()["created"] == 0

    assert client.get("/api/fleet", headers=auth(token)).json()["total"] == 5


def test_seeded_fleet_has_varied_health(client):
    """The demo fleet must not be five identical green cards."""
    token = register(client, "seed2@plant.pk")
    client.post("/api/seed-demo", headers=auth(token), json={})
    fleet = client.get("/api/fleet", headers=auth(token)).json()
    healths = [m["health"] for m in fleet["machines"]]
    assert max(healths) - min(healths) > 20.0


def test_seeded_fleet_raises_alerts_for_unhealthy_machines(client):
    """Regression: seeding used to write readings without running the verdict.

    The demo fleet showed a red machine next to an empty alert inbox. Seeding
    now goes through the same alert-sync path as live ingestion.
    """
    token = register(client, "seed3@plant.pk")
    client.post("/api/seed-demo", headers=auth(token), json={})

    fleet = client.get("/api/fleet", headers=auth(token)).json()
    unhealthy = [m for m in fleet["machines"] if m["severity"] is not None]
    assert unhealthy, "demo fleet should contain at least one unhealthy machine"

    alerts = client.get("/api/alerts?open_only=true", headers=auth(token)).json()
    assert len(alerts) == len(unhealthy), f"{len(unhealthy)} unhealthy machines but {len(alerts)} open alerts"
    assert fleet["open_alerts"] == len(alerts)


def test_simulate_keeps_alerts_in_sync(client):
    token = register(client, "seed4@plant.pk")
    client.post("/api/seed-demo", headers=auth(token), json={})
    client.post("/api/simulate", headers=auth(token), json={"hours": 24})

    fleet = client.get("/api/fleet", headers=auth(token)).json()
    unhealthy = [m for m in fleet["machines"] if m["severity"] is not None]
    alerts = client.get("/api/alerts?open_only=true", headers=auth(token)).json()
    assert len(alerts) == len(unhealthy)
