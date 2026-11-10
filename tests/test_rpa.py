"""Tests for the RPA runbooks — idempotency and technician routing."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.rpa.runbooks import TECHNICIANS


def test_technician_pool_is_non_empty():
    assert len(TECHNICIANS) >= 3
    assert all(isinstance(name, str) and name for name in TECHNICIANS)


def test_repeated_critical_readings_do_not_duplicate_work_orders(
    client: TestClient, auth_headers: dict, machine: dict
):
    """Two critical readings in a row must not open two work orders for one alert.

    Each reading creates its own alert, so two alerts legitimately produce two
    work orders. What must never happen is one alert producing two.
    """
    payload = {
        "vibration_rms": 26.0,
        "temperature_c": 99.0,
        "current_a": 15.0,
        "runtime_hours": 19_000,
    }
    first = client.post(
        f"/api/machines/{machine['id']}/readings", json=payload, headers=auth_headers
    ).json()
    second = client.post(
        f"/api/machines/{machine['id']}/readings", json=payload, headers=auth_headers
    ).json()

    assert first["work_order_id"] != second["work_order_id"]

    orders = client.get("/api/work-orders", headers=auth_headers).json()
    machine_orders = [o for o in orders if o["machine_id"] == machine["id"]]
    # One work order per alert, never two for the same alert.
    alert_ids = [o["alert_id"] for o in machine_orders]
    assert len(alert_ids) == len(set(alert_ids))


def test_work_order_is_assigned_to_a_real_technician(
    client: TestClient, auth_headers: dict, machine: dict
):
    res = client.post(
        f"/api/machines/{machine['id']}/readings",
        json={
            "vibration_rms": 25.0,
            "temperature_c": 98.0,
            "current_a": 15.0,
            "runtime_hours": 19_000,
        },
        headers=auth_headers,
    )
    order_id = res.json()["work_order_id"]
    orders = client.get("/api/work-orders", headers=auth_headers).json()
    order = next(o for o in orders if o["id"] == order_id)
    assert order["assigned_to"] in TECHNICIANS


def test_work_order_description_carries_the_reasoning(
    client: TestClient, auth_headers: dict, machine: dict
):
    res = client.post(
        f"/api/machines/{machine['id']}/readings",
        json={
            "vibration_rms": 25.0,
            "temperature_c": 98.0,
            "current_a": 15.0,
            "runtime_hours": 19_000,
        },
        headers=auth_headers,
    )
    order_id = res.json()["work_order_id"]
    orders = client.get("/api/work-orders", headers=auth_headers).json()
    order = next(o for o in orders if o["id"] == order_id)

    # The description must explain *why*, not just say "machine broken".
    assert "Health index" in order["description"]
    assert "Suggested first checks" in order["description"]
    assert machine["asset_tag"] in order["description"]


def test_healthy_readings_never_open_work_orders(
    client: TestClient, auth_headers: dict, machine: dict
):
    before = len(client.get("/api/work-orders", headers=auth_headers).json())
    for _ in range(3):
        client.post(
            f"/api/machines/{machine['id']}/readings",
            json={
                "vibration_rms": 2.0,
                "temperature_c": 63.0,
                "current_a": 10.0,
                "runtime_hours": 400,
            },
            headers=auth_headers,
        )
    after = len(client.get("/api/work-orders", headers=auth_headers).json())
    assert after == before
