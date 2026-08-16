from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from humanoid_power.domain.enums import (
    ControllerStatus,
    Criticality,
    DataQuality,
    EventAction,
    ModuleCondition,
    OperationKind,
    OperationSource,
    OperationStatus,
    OutletState,
    Profile,
    RepeatMode,
    ScheduleStatus,
)
from humanoid_power.domain.errors import ValidationError


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{field_name} must contain timezone information.")


@dataclass(frozen=True, slots=True)
class OutletConfig:
    outlet_id: int
    name: str
    description: str = ""
    dashboard_visible: bool = True
    control_enabled: bool = True
    criticality: Criticality = Criticality.NORMAL
    confirm_on: bool = False
    confirm_off: bool = False
    revision: int = 1

    def __post_init__(self) -> None:
        if self.outlet_id not in {1, 2, 3, 4}:
            raise ValidationError("Outlet ID must be between 1 and 4.")
        clean_name = self.name.strip()
        clean_description = self.description.strip()
        if not 1 <= len(clean_name) <= 48:
            raise ValidationError("Outlet name must contain 1 to 48 characters.")
        if len(clean_description) > 120:
            raise ValidationError("Outlet description must not exceed 120 characters.")
        if self.revision < 1:
            raise ValidationError("Outlet revision must be positive.")
        object.__setattr__(self, "name", clean_name)
        object.__setattr__(self, "description", clean_description)

    def requires_confirmation(self, target: OutletState) -> bool:
        return (target is OutletState.ON and self.confirm_on) or (
            target is OutletState.OFF and self.confirm_off
        )


@dataclass(frozen=True, slots=True)
class OutletObservation:
    outlet_id: int
    reported_state: OutletState = OutletState.UNKNOWN
    data_quality: DataQuality = DataQuality.UNKNOWN
    observed_at_utc: datetime | None = None
    consecutive_failures: int = 0
    last_change_at_utc: datetime | None = None
    last_known_source: str | None = None

    def __post_init__(self) -> None:
        if self.outlet_id not in {1, 2, 3, 4}:
            raise ValidationError("Outlet observation has an invalid outlet ID.")
        if self.observed_at_utc is not None:
            ensure_aware(self.observed_at_utc, "observed_at_utc")
        if self.last_change_at_utc is not None:
            ensure_aware(self.last_change_at_utc, "last_change_at_utc")

    def age_seconds(self, now: datetime | None = None) -> float | None:
        if self.observed_at_utc is None:
            return None
        current = now or utc_now()
        ensure_aware(current, "now")
        return max(0.0, (current - self.observed_at_utc).total_seconds())


@dataclass(frozen=True, slots=True)
class ScheduleEvent:
    position: int
    local_date: date
    local_time: time
    action: EventAction

    def __post_init__(self) -> None:
        if not 1 <= self.position <= 16:
            raise ValidationError("Schedule event position must be between 1 and 16.")
        if self.local_time.tzinfo is not None:
            raise ValidationError("Schedule event time must be a controller-local wall time.")

    def local_datetime(self) -> datetime:
        return datetime.combine(self.local_date, self.local_time).replace(second=0, microsecond=0)

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "local_date": self.local_date.isoformat(),
            "local_time": self.local_time.strftime("%H:%M"),
            "action": self.action.value,
        }


@dataclass(frozen=True, slots=True)
class HardwareSchedule:
    outlet_id: int
    events: tuple[ScheduleEvent, ...] = ()
    repeat_mode: RepeatMode = RepeatMode.NONE
    loop_minutes: int = 0
    status: ScheduleStatus = ScheduleStatus.NONE
    data_quality: DataQuality = DataQuality.UNKNOWN
    observed_at_utc: datetime | None = None
    next_event_local: datetime | None = None

    def __post_init__(self) -> None:
        if self.outlet_id not in {1, 2, 3, 4}:
            raise ValidationError("Schedule has an invalid outlet ID.")
        if len(self.events) > 16:
            raise ValidationError("A schedule can contain no more than 16 events.")
        if self.loop_minutes < 0:
            raise ValidationError("Schedule loop minutes must not be negative.")
        if self.observed_at_utc is not None:
            ensure_aware(self.observed_at_utc, "observed_at_utc")
        if self.next_event_local is not None:
            ensure_aware(self.next_event_local, "next_event_local")

    @property
    def active(self) -> bool:
        return self.status is ScheduleStatus.ACTIVE and bool(self.events)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "outlet_id": self.outlet_id,
            "events": [event.canonical_dict() for event in self.events],
            "loop_minutes": self.loop_minutes,
        }

    def canonical_hash(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def structurally_matches(self, other: HardwareSchedule) -> bool:
        return self.canonical_hash() == other.canonical_hash()

    def with_next_event(self, timezone: ZoneInfo, now: datetime | None = None) -> HardwareSchedule:
        if not self.events:
            return replace(self, next_event_local=None)
        current = now or datetime.now(timezone)
        ensure_aware(current, "now")
        candidates: list[datetime] = []
        for event in self.events:
            base = event.local_datetime().replace(tzinfo=timezone)
            if base >= current:
                candidates.append(base)
                continue
            if self.loop_minutes > 0:
                elapsed_minutes = (current - base).total_seconds() / 60
                loops = max(1, math.ceil(elapsed_minutes / self.loop_minutes))
                candidates.append(base + timedelta(minutes=loops * self.loop_minutes))
        return replace(self, next_event_local=min(candidates) if candidates else None)


@dataclass(frozen=True, slots=True)
class ControllerInventory:
    count: int
    status: ControllerStatus
    description: str = ""


@dataclass(slots=True)
class HardwareOperation:
    kind: OperationKind
    outlet_id: int
    source: OperationSource
    requested_by: int | None
    payload: dict[str, Any]
    idempotency_key: UUID = field(default_factory=uuid4)
    operation_id: UUID = field(default_factory=uuid4)
    status: OperationStatus = OperationStatus.QUEUED
    requested_at_utc: datetime = field(default_factory=utc_now)
    started_at_utc: datetime | None = None
    completed_at_utc: datetime | None = None
    state_before: OutletState | None = None
    state_after: OutletState | None = None
    result_code: str | None = None
    technical_detail: str | None = None

    def __post_init__(self) -> None:
        if self.outlet_id not in {1, 2, 3, 4}:
            raise ValidationError("Operation has an invalid outlet ID.")
        ensure_aware(self.requested_at_utc, "requested_at_utc")

    def public_dict(self) -> dict[str, Any]:
        return {
            "operation_id": str(self.operation_id),
            "kind": self.kind.value,
            "outlet_id": self.outlet_id,
            "status": self.status.value,
            "result_code": self.result_code,
            "requested_at_utc": self.requested_at_utc.isoformat(),
            "started_at_utc": self.started_at_utc.isoformat() if self.started_at_utc else None,
            "completed_at_utc": (
                self.completed_at_utc.isoformat() if self.completed_at_utc else None
            ),
            "state_before": self.state_before.value if self.state_before else None,
            "state_after": self.state_after.value if self.state_after else None,
        }


@dataclass(frozen=True, slots=True)
class OperationExecutionResult:
    status: OperationStatus
    result_code: str
    state_before: OutletState | None = None
    state_after: OutletState | None = None
    technical_detail: str | None = None


@dataclass(frozen=True, slots=True)
class SystemSettings:
    active_profile: Profile = Profile.TIMED
    timezone: str = "Europe/Berlin"
    show_schedules_tab: bool = True
    show_activity_tab: bool = True
    technical_details_default: bool = False
    config_version: int = 1


@dataclass(frozen=True, slots=True)
class StateSnapshot:
    controller_status: ControllerStatus
    module_condition: ModuleCondition
    observations: tuple[OutletObservation, ...]
    schedules: tuple[HardwareSchedule, ...]
    active_operations: tuple[HardwareOperation, ...]
    published_at_utc: datetime
