from __future__ import annotations

from typing import Any

from humanoid_power.domain.enums import DataQuality, ModuleCondition, OutletState, Profile
from humanoid_power.domain.models import HardwareSchedule, utc_now
from humanoid_power.infrastructure.repositories import SettingsRepository
from humanoid_power.services.state_service import StateCache


def _next_action(schedule: HardwareSchedule) -> str | None:
    if schedule.next_event_local is None:
        return None
    timezone = schedule.next_event_local.tzinfo
    if timezone is None:
        return None
    target = schedule.next_event_local
    candidates: list[tuple[float, str]] = []
    for event in schedule.events:
        base = event.local_datetime().replace(tzinfo=timezone)
        if schedule.loop_minutes:
            delta = (target - base).total_seconds() / 60
            if delta >= 0 and abs(delta % schedule.loop_minutes) < 0.001:
                candidates.append((delta, event.action.value))
        elif base == target:
            candidates.append((0, event.action.value))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def build_state_payload(settings: SettingsRepository, cache: StateCache) -> dict[str, Any]:
    system = settings.get_system()
    outlets = settings.list_outlets()
    snapshot = cache.snapshot(outlets)
    observation_by_id = {item.outlet_id: item for item in snapshot.observations}
    schedule_by_id = {item.outlet_id: item for item in snapshot.schedules}
    active_by_id = {item.outlet_id: item for item in snapshot.active_operations}
    payload_outlets: list[dict[str, Any]] = []
    for config in outlets:
        observation = observation_by_id[config.outlet_id]
        schedule = schedule_by_id[config.outlet_id]
        active = active_by_id.get(config.outlet_id)
        last = cache.last_operation(config.outlet_id)
        forced_visible = (
            observation.reported_state is OutletState.ON
            or observation.data_quality is not DataQuality.GOOD
            or schedule.active
            or active is not None
            or (last is not None and last.status.value in {"MISMATCH", "UNKNOWN"})
            or snapshot.module_condition in {ModuleCondition.FAULT, ModuleCondition.CRITICAL}
        )
        payload_outlets.append(
            {
                "outlet_id": config.outlet_id,
                "name": config.name,
                "description": config.description,
                "reported_state": observation.reported_state.value,
                "data_quality": observation.data_quality.value,
                "confirmed_at_utc": (
                    observation.observed_at_utc.isoformat() if observation.observed_at_utc else None
                ),
                "age_seconds": observation.age_seconds(snapshot.published_at_utc),
                "last_known_source": observation.last_known_source,
                "dashboard_visible": config.dashboard_visible,
                "forced_visible": forced_visible,
                "control_enabled": config.control_enabled,
                "manual_allowed": (
                    system.active_profile in {Profile.DIRECT, Profile.TIMED}
                    and config.control_enabled
                    and snapshot.controller_status.value == "READY"
                    and observation.data_quality is DataQuality.GOOD
                    and active is None
                ),
                "criticality": config.criticality.value,
                "confirm_on": config.confirm_on,
                "confirm_off": config.confirm_off,
                "active_operation": active.public_dict() if active else None,
                "last_operation": last.public_dict() if last else None,
                "schedule": {
                    "status": schedule.status.value,
                    "data_quality": schedule.data_quality.value,
                    "event_count": len(schedule.events),
                    "active": schedule.active,
                    "repeat_mode": schedule.repeat_mode.value,
                    "loop_minutes": schedule.loop_minutes,
                    "canonical_hash": schedule.canonical_hash(),
                    "next_event_local": (
                        schedule.next_event_local.isoformat() if schedule.next_event_local else None
                    ),
                    "next_action": _next_action(schedule),
                },
            }
        )
    return {
        "schema_version": 1,
        "server_time_utc": utc_now().isoformat(),
        "timezone": system.timezone,
        "config_version": system.config_version,
        "profile": system.active_profile.value,
        "module_condition": snapshot.module_condition.value,
        "controller": {
            "status": snapshot.controller_status.value,
            "last_seen_at_utc": (
                cache.controller_last_seen.isoformat() if cache.controller_last_seen else None
            ),
        },
        "automation_count": sum(1 for schedule in snapshot.schedules if schedule.active),
        "outlets": payload_outlets,
    }


def build_schedule_payload(
    outlet_id: int, settings: SettingsRepository, cache: StateCache
) -> dict[str, Any]:
    outlet = settings.get_outlet(outlet_id)
    schedule = cache.get_schedule(outlet_id)
    return {
        "outlet": outlet,
        "schedule": schedule,
        "schedule_hash": schedule.canonical_hash(),
        "editable": schedule.data_quality is DataQuality.GOOD,
    }
