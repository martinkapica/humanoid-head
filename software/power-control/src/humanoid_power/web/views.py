from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import cast

from flask import Blueprint, current_app, render_template, request, session
from flask_login import current_user, login_required

from humanoid_power.infrastructure.repositories import EventRepository, SettingsRepository
from humanoid_power.services.runtime import PowerRuntime
from humanoid_power.web.presenters import build_schedule_payload, build_state_payload

views_blueprint = Blueprint("views", __name__)

_EVENT_MESSAGES = {
    "ADMIN_PASSWORD_SET": "Administrator password changed",
    "LOGIN_BLOCKED": "Sign-in blocked by account lockout",
    "LOGIN_FAILED": "Sign-in failed",
    "LOGIN_SUCCEEDED": "Administrator signed in",
    "PROFILE_CHANGED": "Operating profile changed",
    "OUTLET_SETTINGS_CHANGED": "Outlet settings changed",
    "INTERFACE_SETTINGS_CHANGED": "Interface settings changed",
    "CONTROLLER_CONFIRMED": "Controller confirmed the requested outlet state",
    "ALREADY_IN_TARGET_STATE": "Outlet was already in the requested state",
    "SCHEDULE_CONTROLLER_CONFIRMED": "Controller confirmed the hardware schedule",
    "SCHEDULE_DELETE_CONFIRMED": "Controller confirmed schedule deletion",
    "READBACK_MISMATCH": "Controller readback did not match the request",
    "RESULT_UNKNOWN": "The hardware operation result is unknown",
}


def _settings() -> SettingsRepository:
    return cast(SettingsRepository, current_app.extensions["settings_repository"])


def _runtime() -> PowerRuntime:
    return cast(PowerRuntime, current_app.extensions["power_runtime"])


@views_blueprint.app_context_processor
def navigation_context():  # type: ignore[no-untyped-def]
    if not current_user.is_authenticated:
        return {}
    settings = _settings().get_system()
    payload = build_state_payload(_settings(), _runtime().cache)
    return {"nav_settings": settings, "nav_state": payload}


@views_blueprint.get("/")
@login_required
def manual():  # type: ignore[no-untyped-def]
    payload = build_state_payload(_settings(), _runtime().cache)
    return render_template("manual.html", state=payload)


@views_blueprint.get("/schedules")
@login_required
def schedules():  # type: ignore[no-untyped-def]
    payload = build_state_payload(_settings(), _runtime().cache)
    return render_template("schedules.html", state=payload)


@views_blueprint.get("/schedules/<int:outlet_id>")
@login_required
def schedule_detail(outlet_id: int):  # type: ignore[no-untyped-def]
    details = build_schedule_payload(outlet_id, _settings(), _runtime().cache)
    system = _settings().get_system()
    return render_template(
        "schedule_detail.html",
        details=details,
        system=system,
        controller_time_basis=_runtime().schedules.clock.now_local().isoformat(),
    )


@views_blueprint.get("/activity")
@login_required
def activity():  # type: ignore[no-untyped-def]
    events: EventRepository = current_app.extensions["event_repository"]
    outlet_raw = request.args.get("outlet", "")
    outlet_id = int(outlet_raw) if outlet_raw in {"1", "2", "3", "4"} else None
    rows = events.list_recent(
        limit=200,
        outlet_id=outlet_id,
        category=request.args.get("category") or None,
        severity=request.args.get("severity") or None,
    )
    for row in rows:
        row["display_message"] = _EVENT_MESSAGES.get(
            row["message_code"], row["message_code"].replace("_", " ").title()
        )
    return render_template("activity.html", events=rows)


@views_blueprint.get("/settings/operation")
@login_required
def settings_operation():  # type: ignore[no-untyped-def]
    return render_template(
        "settings/operation.html",
        system=_settings().get_system(),
        state=build_state_payload(_settings(), _runtime().cache),
    )


@views_blueprint.get("/settings/outlets")
@login_required
def settings_outlets():  # type: ignore[no-untyped-def]
    return render_template(
        "settings/outlets.html",
        system=_settings().get_system(),
        outlets=_settings().list_outlets(),
    )


@views_blueprint.get("/settings/interface")
@login_required
def settings_interface():  # type: ignore[no-untyped-def]
    return render_template("settings/interface.html", system=_settings().get_system())


@views_blueprint.get("/settings/system")
@login_required
def settings_system():  # type: ignore[no-untyped-def]
    config = current_app.extensions["power_config"]
    try:
        app_version = version("humanoid-power")
    except PackageNotFoundError:
        app_version = "development"
    return render_template(
        "settings/system.html",
        app_version=app_version,
        controller_binary=config.controller.binary_path,
        controller_status=_runtime().cache.controller_status.value,
        time_synchronized=_runtime().schedules.clock.is_synchronized(),
        hardware_accepted=config.controller.hardware_accepted,
        login_at_utc=session.get("login_at_utc"),
        https_active=request.is_secure,
    )
