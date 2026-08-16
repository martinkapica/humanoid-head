from __future__ import annotations

import atexit
import os
import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask, g, jsonify, redirect, request, url_for
from flask_login import LoginManager, login_required
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix

from humanoid_power.adapters.base import PowerAdapter
from humanoid_power.adapters.fake import FakePowerAdapter
from humanoid_power.adapters.sispmctl import SispmctlAdapter
from humanoid_power.config import AppConfig, load_config
from humanoid_power.domain.errors import DomainError
from humanoid_power.infrastructure.database import Database
from humanoid_power.infrastructure.repositories import (
    EventRepository,
    OperationRepository,
    SettingsRepository,
    UserRepository,
)
from humanoid_power.infrastructure.system_clock import SystemClock
from humanoid_power.services.auth_service import AppUser, AuthService
from humanoid_power.services.runtime import PowerRuntime

csrf = CSRFProtect()
login_manager = LoginManager()


def _load_secret_key(config: AppConfig, testing: bool) -> str:
    if testing:
        return "test-secret-key-only"
    path = Path(config.application.secret_key_path)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(
            f"Secret key file is missing or unreadable: {path}. Run the initialization CLI."
        ) from exc
    try:
        secret_bytes = bytes.fromhex(value)
    except ValueError as exc:
        raise RuntimeError("Secret key file must contain hexadecimal data.") from exc
    if len(secret_bytes) < 32:
        raise RuntimeError("Secret key file must contain at least 32 random bytes.")
    return value


def create_app(
    config_path: str | Path | None = None,
    *,
    testing: bool = False,
    adapter: PowerAdapter | None = None,
    database_path: str | Path | None = None,
    start_runtime: bool | None = None,
) -> Flask:
    if config_path is None:
        config_path = os.environ.get("HUMANOID_POWER_CONFIG")
    config = load_config(config_path)
    if (
        adapter is None
        and config.controller.adapter == "sispmctl"
        and not config.controller.hardware_accepted
    ):
        raise RuntimeError(
            "Real hardware is blocked until controller.hardware_accepted = true after "
            "the documented hardware acceptance tests."
        )
    if database_path is not None:
        config = AppConfig(
            application=type(config.application)(
                timezone=config.application.timezone,
                database_path=str(database_path),
                secret_key_path=config.application.secret_key_path,
                runtime_enabled=config.application.runtime_enabled,
                require_time_sync=config.application.require_time_sync,
            ),
            controller=config.controller,
            polling=config.polling,
            queue=config.queue,
            web=config.web,
        )

    app = Flask(__name__, template_folder="web/templates", static_folder="web/static")
    app.config.update(
        TESTING=testing,
        SECRET_KEY=_load_secret_key(config, testing),
        SESSION_COOKIE_NAME="humanoid_power_session",
        SESSION_COOKIE_SECURE=not testing,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=config.web.session_absolute_hours),
        WTF_CSRF_TIME_LIMIT=config.web.session_absolute_hours * 60 * 60,
        MAX_CONTENT_LENGTH=32 * 1024,
        MAX_FORM_MEMORY_SIZE=32 * 1024,
        MAX_FORM_PARTS=100,
        TRUSTED_HOSTS=list(config.web.trusted_hosts) if not testing else None,
    )
    if not testing and config.web.proxy_count == 1:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[method-assign]

    database = Database(config.application.database_path)
    database.initialize()
    settings_repository = SettingsRepository(database)
    settings_repository.initialize_defaults()
    user_repository = UserRepository(database)
    operation_repository = OperationRepository(database)
    event_repository = EventRepository(database)
    auth_service = AuthService(user_repository, event_repository)

    selected_adapter = adapter
    if selected_adapter is None:
        selected_adapter = (
            FakePowerAdapter()
            if config.controller.adapter == "fake"
            else SispmctlAdapter(
                config.controller.binary_path, config.controller.command_timeout_seconds
            )
        )
    clock = SystemClock(
        config.application.timezone,
        force_synchronized=True if testing else None,
    )
    runtime = PowerRuntime(
        config,
        selected_adapter,
        settings_repository,
        operation_repository,
        event_repository,
        clock,
    )

    app.extensions["power_config"] = config
    app.extensions["database"] = database
    app.extensions["settings_repository"] = settings_repository
    app.extensions["user_repository"] = user_repository
    app.extensions["operation_repository"] = operation_repository
    app.extensions["event_repository"] = event_repository
    app.extensions["auth_service"] = auth_service
    app.extensions["power_runtime"] = runtime

    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.refresh_view = "auth.login"
    login_manager.session_protection = "strong"

    @login_manager.unauthorized_handler
    def unauthorized():  # type: ignore[no-untyped-def]
        if request.path.startswith("/api/"):
            return jsonify(error={"code": "AUTHENTICATION_REQUIRED"}), 401
        return redirect(url_for("auth.login", next=request.full_path))

    @login_manager.needs_refresh_handler
    def refresh_required():  # type: ignore[no-untyped-def]
        if request.path.startswith("/api/"):
            return jsonify(error={"code": "FRESH_LOGIN_REQUIRED"}), 401
        return redirect(url_for("auth.login", next=request.full_path))

    @login_manager.user_loader
    def load_user(user_id: str) -> AppUser | None:
        try:
            record = user_repository.get_by_id(int(user_id))
        except ValueError:
            return None
        return AppUser(record) if record else None

    from humanoid_power.web.api import api_blueprint
    from humanoid_power.web.auth import auth_blueprint
    from humanoid_power.web.views import views_blueprint

    app.register_blueprint(auth_blueprint)
    app.register_blueprint(views_blueprint)
    app.register_blueprint(api_blueprint)

    @app.before_request
    def assign_correlation_id() -> None:
        g.correlation_id = request.headers.get("X-Request-ID", secrets.token_hex(16))[:64]

    @app.after_request
    def security_headers(response):  # type: ignore[no-untyped-def]
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["X-Request-ID"] = g.get("correlation_id", "")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(DomainError)
    def domain_error(error: DomainError):  # type: ignore[no-untyped-def]
        if request.path.startswith("/api/"):
            return (
                jsonify(
                    error={
                        "code": error.code,
                        "message": error.message,
                        "correlation_id": g.get("correlation_id"),
                    }
                ),
                error.http_status,
            )
        return error.message, error.http_status

    @app.errorhandler(CSRFError)
    def csrf_error(error: CSRFError):  # type: ignore[no-untyped-def]
        if request.path.startswith("/api/"):
            return (
                jsonify(
                    error={
                        "code": "CSRF_FAILED",
                        "message": "The security token is missing or expired. Reload the page.",
                        "correlation_id": g.get("correlation_id"),
                    }
                ),
                400,
            )
        return "Security token missing or expired. Reload the page.", 400

    @app.get("/health/live")
    def health_live():  # type: ignore[no-untyped-def]
        return jsonify(live=True)

    @app.get("/health/ready")
    @login_required
    def health_ready():  # type: ignore[no-untyped-def]
        ready = runtime.cache.controller_status.value in {"READY", "DEGRADED"}
        return jsonify(ready=ready), 200 if ready else 503

    should_start = config.application.runtime_enabled if start_runtime is None else start_runtime
    if should_start:
        runtime.start()
        atexit.register(runtime.stop)

    return app
