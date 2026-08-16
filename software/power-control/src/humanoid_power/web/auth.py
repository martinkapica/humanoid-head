from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, fresh_login_required, login_required, login_user, logout_user

from humanoid_power.domain.errors import ValidationError
from humanoid_power.services.auth_service import AuthService

auth_blueprint = Blueprint("auth", __name__)


def _safe_next(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        return None
    return parsed.path


def _login_is_recent(maximum_minutes: int = 10) -> bool:
    raw_value = session.get("login_at_utc")
    try:
        login_at = datetime.fromisoformat(raw_value) if raw_value else None
    except (TypeError, ValueError):
        return False
    return login_at is not None and datetime.now(UTC) - login_at <= timedelta(
        minutes=maximum_minutes
    )


@auth_blueprint.before_app_request
def enforce_session_timeouts():  # type: ignore[no-untyped-def]
    if not current_user.is_authenticated:
        return None
    now = datetime.now(UTC)
    config = current_app.extensions["power_config"]
    login_at_raw = session.get("login_at_utc")
    last_raw = session.get("last_activity_utc")
    try:
        login_at = datetime.fromisoformat(login_at_raw) if login_at_raw else now
        last = datetime.fromisoformat(last_raw) if last_raw else now
    except ValueError:
        logout_user()
        session.clear()
        return redirect(url_for("auth.login"))
    absolute_expired = now - login_at > timedelta(hours=config.web.session_absolute_hours)
    idle_expired = now - last > timedelta(minutes=config.web.session_idle_minutes)
    if absolute_expired or idle_expired:
        logout_user()
        session.clear()
        flash("Session expired. Sign in again.", "warning")
        return redirect(url_for("auth.login"))
    session["last_activity_utc"] = now.isoformat()
    return None


@auth_blueprint.route("/login", methods=["GET", "POST"])
def login():  # type: ignore[no-untyped-def]
    if current_user.is_authenticated:
        return redirect(url_for("views.manual"))
    if request.method == "POST":
        service: AuthService = current_app.extensions["auth_service"]
        user = service.authenticate(
            request.form.get("username", ""),
            request.form.get("password", ""),
            request.remote_addr or "UNKNOWN",
        )
        if user is not None:
            login_user(user, fresh=True)
            now = datetime.now(UTC).isoformat()
            session.permanent = True
            session["login_at_utc"] = now
            session["last_activity_utc"] = now
            return redirect(_safe_next(request.form.get("next")) or url_for("views.manual"))
        flash("Sign-in failed or account temporarily locked.", "error")
    return render_template("login.html", next_path=_safe_next(request.args.get("next")))


@auth_blueprint.post("/logout")
def logout():  # type: ignore[no-untyped-def]
    logout_user()
    session.clear()
    return redirect(url_for("auth.login"))


@auth_blueprint.post("/settings/password")
@login_required
@fresh_login_required
def change_password():  # type: ignore[no-untyped-def]
    if not _login_is_recent():
        logout_user()
        session.clear()
        flash("Sign in again before changing the password.", "warning")
        return redirect(url_for("auth.login", next="/settings/system"))
    service: AuthService = current_app.extensions["auth_service"]
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirmation = request.form.get("confirm_password", "")
    authenticated = service.authenticate(
        current_user.username,
        current_password,
        request.remote_addr or "UNKNOWN",
    )
    if authenticated is None:
        flash("Current password is incorrect.", "error")
        return redirect(url_for("views.settings_system"))
    if new_password != confirmation:
        flash("The new passwords do not match.", "error")
        return redirect(url_for("views.settings_system"))
    try:
        service.set_admin_password(new_password, username=current_user.username)
    except ValidationError as error:
        flash(error.message, "error")
        return redirect(url_for("views.settings_system"))
    logout_user()
    session.clear()
    flash("Password changed. Sign in with the new password.", "info")
    return redirect(url_for("auth.login"))
