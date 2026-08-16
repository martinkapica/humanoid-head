from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from flask import Blueprint, current_app, jsonify, request, session
from flask_login import current_user, fresh_login_required, login_required

from humanoid_power.domain.enums import Criticality, OutletState, Profile
from humanoid_power.domain.errors import DomainError, ValidationError
from humanoid_power.domain.models import OutletConfig
from humanoid_power.infrastructure.repositories import OperationRepository, SettingsRepository
from humanoid_power.services.runtime import PowerRuntime
from humanoid_power.web.presenters import build_state_payload

api_blueprint = Blueprint("api", __name__, url_prefix="/api/v1")


def _json_object() -> dict[str, Any]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")
    return payload


def _uuid(value: Any, field_name: str) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, TypeError) as exc:
        raise ValidationError(f"{field_name} must be a valid UUID.") from exc


def _int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValidationError(f"{field_name} must be an integer.")
    try:
        return int(value)
    except (ValueError, TypeError) as exc:
        raise ValidationError(f"{field_name} must be an integer.") from exc


def _bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"{field_name} must be true or false.")
    return value


def _runtime() -> PowerRuntime:
    return cast(PowerRuntime, current_app.extensions["power_runtime"])


def _settings() -> SettingsRepository:
    return cast(SettingsRepository, current_app.extensions["settings_repository"])


def _user_id() -> int:
    return int(current_user.get_id())


def _require_recent_login() -> None:
    raw_value = session.get("login_at_utc")
    try:
        login_at = datetime.fromisoformat(raw_value) if raw_value else None
    except (TypeError, ValueError):
        login_at = None
    if login_at is None or datetime.now(UTC) - login_at > timedelta(minutes=10):
        raise DomainError(
            "FRESH_LOGIN_REQUIRED",
            "Sign in again before changing security-sensitive settings.",
            401,
        )


@api_blueprint.get("/state")
@login_required
def state():  # type: ignore[no-untyped-def]
    return jsonify(build_state_payload(_settings(), _runtime().cache))


@api_blueprint.post("/reconcile")
@login_required
def reconcile():  # type: ignore[no-untyped-def]
    _runtime().poller.request_reconcile()
    return jsonify(status="ACCEPTED"), 202


@api_blueprint.post("/outlets/<int:outlet_id>/operations")
@login_required
def create_outlet_operation(outlet_id: int):  # type: ignore[no-untyped-def]
    payload = _json_object()
    try:
        target = OutletState(str(payload.get("target_state", "")).upper())
    except ValueError as exc:
        raise ValidationError("target_state must be ON or OFF.") from exc
    if target is OutletState.UNKNOWN:
        raise ValidationError("target_state must be ON or OFF.")
    operation = _runtime().control.request_state_change(
        outlet_id=outlet_id,
        target=target,
        idempotency_key=_uuid(payload.get("idempotency_key"), "idempotency_key"),
        expected_config_version=_int(
            payload.get("expected_config_version"), "expected_config_version"
        ),
        confirmed_critical_action=_bool(
            payload.get("confirmed_critical_action", False),
            "confirmed_critical_action",
        ),
        user_id=_user_id(),
    )
    return jsonify(operation.public_dict()), 202


@api_blueprint.get("/operations/<uuid:operation_id>")
@login_required
def get_operation(operation_id: UUID):  # type: ignore[no-untyped-def]
    repository: OperationRepository = current_app.extensions["operation_repository"]
    operation = repository.get(operation_id)
    if operation is None:
        raise DomainError("OPERATION_NOT_FOUND", "Operation does not exist.", 404)
    return jsonify(operation.public_dict())


@api_blueprint.get("/schedules")
@login_required
def get_schedules():  # type: ignore[no-untyped-def]
    return jsonify(build_state_payload(_settings(), _runtime().cache)["outlets"])


@api_blueprint.put("/outlets/<int:outlet_id>/schedule")
@login_required
def write_schedule(outlet_id: int):  # type: ignore[no-untyped-def]
    payload = _json_object()
    operation = _runtime().schedules.request_write(
        outlet_id=outlet_id,
        payload=payload,
        idempotency_key=_uuid(payload.get("idempotency_key"), "idempotency_key"),
        expected_config_version=_int(
            payload.get("expected_config_version"), "expected_config_version"
        ),
        expected_schedule_hash=str(payload.get("expected_schedule_hash", "")),
        user_id=_user_id(),
    )
    return jsonify(operation.public_dict()), 202


@api_blueprint.delete("/outlets/<int:outlet_id>/schedule")
@login_required
def delete_schedule(outlet_id: int):  # type: ignore[no-untyped-def]
    payload = _json_object()
    operation = _runtime().schedules.request_delete(
        outlet_id=outlet_id,
        idempotency_key=_uuid(payload.get("idempotency_key"), "idempotency_key"),
        expected_config_version=_int(
            payload.get("expected_config_version"), "expected_config_version"
        ),
        expected_schedule_hash=str(payload.get("expected_schedule_hash", "")),
        user_id=_user_id(),
    )
    return jsonify(operation.public_dict()), 202


@api_blueprint.put("/settings/profile")
@fresh_login_required
def update_profile():  # type: ignore[no-untyped-def]
    _require_recent_login()
    payload = _json_object()
    try:
        profile = Profile(str(payload.get("profile", "")).upper())
    except ValueError as exc:
        raise ValidationError("Unknown profile.") from exc
    settings = _runtime().settings.change_profile(
        profile,
        _int(payload.get("expected_config_version"), "expected_config_version"),
        _user_id(),
    )
    return jsonify(profile=settings.active_profile.value, config_version=settings.config_version)


@api_blueprint.put("/settings/outlets/<int:outlet_id>")
@fresh_login_required
def update_outlet(outlet_id: int):  # type: ignore[no-untyped-def]
    _require_recent_login()
    payload = _json_object()
    current = _settings().get_outlet(outlet_id)
    try:
        criticality = Criticality(str(payload.get("criticality", "NORMAL")).upper())
    except ValueError as exc:
        raise ValidationError("criticality must be NORMAL or CRITICAL.") from exc
    outlet = OutletConfig(
        outlet_id=outlet_id,
        name=str(payload.get("name", "")),
        description=str(payload.get("description", "")),
        dashboard_visible=_bool(payload.get("dashboard_visible"), "dashboard_visible"),
        control_enabled=_bool(payload.get("control_enabled"), "control_enabled"),
        criticality=criticality,
        confirm_on=_bool(payload.get("confirm_on"), "confirm_on"),
        confirm_off=_bool(payload.get("confirm_off"), "confirm_off"),
        revision=_int(payload.get("revision", current.revision), "revision"),
    )
    updated, system = _runtime().settings.update_outlet(
        outlet,
        _int(payload.get("expected_config_version"), "expected_config_version"),
        _user_id(),
    )
    return jsonify(
        outlet_id=updated.outlet_id,
        revision=updated.revision,
        config_version=system.config_version,
    )


@api_blueprint.put("/settings/interface")
@fresh_login_required
def update_interface():  # type: ignore[no-untyped-def]
    _require_recent_login()
    payload = _json_object()
    settings = _runtime().settings.update_interface(
        show_schedules_tab=_bool(payload.get("show_schedules_tab"), "show_schedules_tab"),
        show_activity_tab=_bool(payload.get("show_activity_tab"), "show_activity_tab"),
        technical_details_default=_bool(
            payload.get("technical_details_default"), "technical_details_default"
        ),
        expected_version=_int(payload.get("expected_config_version"), "expected_config_version"),
        user_id=_user_id(),
    )
    return jsonify(config_version=settings.config_version)
