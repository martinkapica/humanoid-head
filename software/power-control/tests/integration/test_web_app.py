from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import authenticated_csrf, login
from flask import Flask
from flask.testing import FlaskClient

from humanoid_power.adapters.fake import FakePowerAdapter
from humanoid_power.domain.enums import (
    Criticality,
    DataQuality,
    EventAction,
    RepeatMode,
    ScheduleStatus,
)
from humanoid_power.domain.models import HardwareSchedule, OutletConfig, ScheduleEvent
from humanoid_power.infrastructure.repositories import SettingsRepository
from humanoid_power.services.runtime import PowerRuntime


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/schedules",
        "/schedules/1",
        "/activity",
        "/settings/operation",
        "/settings/outlets",
        "/settings/interface",
        "/settings/system",
    ],
)
def test_authenticated_html_routes_render(client: FlaskClient, path: str) -> None:
    login(client)
    response = client.get(path)
    assert response.status_code == 200
    assert "HUMANOID CONTROL" in response.get_data(as_text=True)


def test_health_and_unauthenticated_api(client: FlaskClient) -> None:
    assert client.get("/health/live").get_json() == {"live": True}
    assert client.get("/health/ready").status_code == 302
    response = client.get("/api/v1/state")
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    login(client)
    assert client.get("/health/ready").status_code == 200


def test_login_and_manual_page_show_confirmed_state(client: FlaskClient) -> None:
    response = login(client)
    assert response.status_code == 302
    page = client.get("/")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "REPORTED STATE" in body
    assert "Power outlet 1" in body
    assert "OFF" in body


def test_manual_operation_is_confirmed_end_to_end(client: FlaskClient) -> None:
    login(client)
    token = authenticated_csrf(client)
    state = client.get("/api/v1/state").get_json()
    response = client.post(
        "/api/v1/outlets/1/operations",
        headers={"X-CSRFToken": token},
        json={
            "target_state": "ON",
            "idempotency_key": str(uuid4()),
            "expected_config_version": state["config_version"],
            "confirmed_critical_action": False,
        },
    )
    assert response.status_code == 202
    operation_id = response.get_json()["operation_id"]
    deadline = time.monotonic() + 3
    operation = None
    while time.monotonic() < deadline:
        operation = client.get(f"/api/v1/operations/{operation_id}").get_json()
        if operation["status"] not in {"QUEUED", "RUNNING"}:
            break
        time.sleep(0.02)
    assert operation is not None
    assert operation["status"] == "CONFIRMED"
    assert operation["state_after"] == "ON"
    assert client.get("/api/v1/state").get_json()["outlets"][0]["reported_state"] == "ON"


def test_missing_csrf_token_is_rejected(client: FlaskClient) -> None:
    login(client)
    response = client.post(
        "/api/v1/outlets/1/operations",
        json={
            "target_state": "ON",
            "idempotency_key": str(uuid4()),
            "expected_config_version": 1,
            "confirmed_critical_action": False,
        },
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "CSRF_FAILED"


def test_critical_outlet_requires_explicit_confirmation(app: Flask, client: FlaskClient) -> None:
    repository: SettingsRepository = app.extensions["settings_repository"]
    current = repository.get_outlet(1)
    system = repository.get_system()
    repository.update_outlet(
        OutletConfig(
            outlet_id=1,
            name=current.name,
            criticality=Criticality.CRITICAL,
            confirm_on=True,
            revision=current.revision,
        ),
        expected_version=system.config_version,
        user_id=1,
    )
    login(client)
    token = authenticated_csrf(client)
    state = client.get("/api/v1/state").get_json()
    response = client.post(
        "/api/v1/outlets/1/operations",
        headers={"X-CSRFToken": token},
        json={
            "target_state": "ON",
            "idempotency_key": str(uuid4()),
            "expected_config_version": state["config_version"],
            "confirmed_critical_action": False,
        },
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "CRITICAL_CONFIRMATION_REQUIRED"


def test_direct_profile_is_rejected_while_hardware_schedule_is_active(
    app: Flask, client: FlaskClient, fake_adapter: FakePowerAdapter
) -> None:
    runtime: PowerRuntime = app.extensions["power_runtime"]
    future = runtime.schedules.clock.now_local() + timedelta(days=2)
    schedule = HardwareSchedule(
        outlet_id=1,
        events=(
            ScheduleEvent(
                1, future.date(), future.time().replace(second=0, microsecond=0), EventAction.ON
            ),
        ),
        repeat_mode=RepeatMode.NONE,
        status=ScheduleStatus.ACTIVE,
        data_quality=DataQuality.GOOD,
    )
    assert fake_adapter.write_schedule(1, schedule).ok
    runtime.reconcile_now()
    login(client)
    token = authenticated_csrf(client)
    state = client.get("/api/v1/state").get_json()
    response = client.put(
        "/api/v1/settings/profile",
        headers={"X-CSRFToken": token},
        json={"profile": "DIRECT", "expected_config_version": state["config_version"]},
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "ACTIVE_SCHEDULES_PRESENT"


def test_settings_change_requires_login_newer_than_ten_minutes(
    client: FlaskClient,
) -> None:
    login(client)
    token = authenticated_csrf(client)
    with client.session_transaction() as session:
        session["login_at_utc"] = (datetime.now(UTC) - timedelta(minutes=11)).isoformat()
    state = client.get("/api/v1/state").get_json()
    response = client.put(
        "/api/v1/settings/profile",
        headers={"X-CSRFToken": token},
        json={"profile": "MONITOR", "expected_config_version": state["config_version"]},
    )
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "FRESH_LOGIN_REQUIRED"
