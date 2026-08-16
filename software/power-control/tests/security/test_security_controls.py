from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import ADMIN_PASSWORD, authenticated_csrf, extract_csrf, login
from flask import Flask
from flask.testing import FlaskClient

from humanoid_power.infrastructure.repositories import UserRepository
from humanoid_power.services.auth_service import AuthService


def test_security_headers_are_present(client: FlaskClient) -> None:
    response = client.get("/login")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "same-origin"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Request-ID"]


def test_session_cookie_has_security_attributes(app: Flask, client: FlaskClient) -> None:
    app.config["SESSION_COOKIE_SECURE"] = True
    response = login(client)
    cookies = response.headers.getlist("Set-Cookie")
    session_cookie = next(
        cookie for cookie in cookies if cookie.startswith("humanoid_power_session=")
    )
    assert "Secure" in session_cookie
    assert "HttpOnly" in session_cookie
    assert "SameSite=Strict" in session_cookie


def test_account_locks_after_five_failed_logins(app: Flask) -> None:
    auth: AuthService = app.extensions["auth_service"]
    for _ in range(5):
        assert auth.authenticate("admin", "wrong-password", "127.0.0.1") is None
    users: UserRepository = app.extensions["user_repository"]
    record = users.get_by_username("admin")
    assert record is not None
    assert record.locked_until_utc is not None
    assert auth.authenticate("admin", ADMIN_PASSWORD, "127.0.0.1") is None


def test_failed_login_counter_resets_outside_fifteen_minute_window(app: Flask) -> None:
    auth: AuthService = app.extensions["auth_service"]
    users: UserRepository = app.extensions["user_repository"]
    record = users.get_by_username("admin")
    assert record is not None
    database = app.extensions["database"]
    with database.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE users
            SET failed_login_count = 4, last_failed_login_at_utc = ?
            WHERE id = ?
            """,
            ((datetime.now(UTC) - timedelta(minutes=16)).isoformat(), record.id),
        )
    assert auth.authenticate("admin", "wrong-password", "127.0.0.1") is None
    updated = users.get_by_username("admin")
    assert updated is not None
    assert updated.failed_login_count == 1
    assert updated.locked_until_utc is None


def test_administrator_can_change_password_and_must_sign_in_again(
    client: FlaskClient,
) -> None:
    login(client)
    token = authenticated_csrf(client)
    new_password = "Changed-Administrator-2026!"
    response = client.post(
        "/settings/password",
        data={
            "csrf_token": token,
            "current_password": ADMIN_PASSWORD,
            "new_password": new_password,
            "confirm_password": new_password,
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    login_page = client.get("/login")
    login_token = extract_csrf(login_page.get_data(as_text=True))
    second_login = client.post(
        "/login",
        data={
            "username": "admin",
            "password": new_password,
            "csrf_token": login_token,
        },
    )
    assert second_login.status_code == 302
    assert second_login.headers["Location"].endswith("/")
