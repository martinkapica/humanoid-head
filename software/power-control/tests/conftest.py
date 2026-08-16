from __future__ import annotations

import re
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from humanoid_power.adapters.fake import FakePowerAdapter
from humanoid_power.app import create_app
from humanoid_power.services.auth_service import AuthService
from humanoid_power.services.runtime import PowerRuntime

ADMIN_PASSWORD = "Test-Administrator-2026!"


@pytest.fixture
def fake_adapter() -> FakePowerAdapter:
    return FakePowerAdapter()


@pytest.fixture
def app(tmp_path: Path, fake_adapter: FakePowerAdapter) -> Generator[Flask, None, None]:
    application = create_app(
        testing=True,
        adapter=fake_adapter,
        database_path=tmp_path / "power-test.db",
        start_runtime=False,
    )
    auth: AuthService = application.extensions["auth_service"]
    auth.set_admin_password(ADMIN_PASSWORD)
    runtime: PowerRuntime = application.extensions["power_runtime"]
    runtime.reconcile_now()
    runtime.start()
    yield application
    runtime.stop()


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


def extract_csrf(html: str, *, meta: bool = False) -> str:
    pattern = (
        r'name="csrf-token" content="([^"]+)"' if meta else r'name="csrf_token" value="([^"]+)"'
    )
    match = re.search(pattern, html)
    if match is None:
        raise AssertionError("CSRF token not found in response.")
    return match.group(1)


def login(client: FlaskClient) -> Any:
    token = extract_csrf(client.get("/login").get_data(as_text=True))
    return client.post(
        "/login",
        data={"username": "admin", "password": ADMIN_PASSWORD, "csrf_token": token},
    )


def authenticated_csrf(client: FlaskClient) -> str:
    response = client.get("/")
    assert response.status_code == 200
    return extract_csrf(response.get_data(as_text=True), meta=True)
