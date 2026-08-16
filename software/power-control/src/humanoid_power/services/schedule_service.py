from __future__ import annotations

import threading
import time
from datetime import UTC, date, datetime
from datetime import time as local_time
from typing import Any
from uuid import UUID

from humanoid_power.adapters.base import PowerAdapter
from humanoid_power.domain.enums import (
    ControllerStatus,
    DataQuality,
    EventAction,
    OperationKind,
    OperationSource,
    OperationStatus,
    Profile,
    RepeatMode,
    ScheduleStatus,
)
from humanoid_power.domain.errors import DomainError, ValidationError
from humanoid_power.domain.models import (
    HardwareOperation,
    HardwareSchedule,
    OperationExecutionResult,
    ScheduleEvent,
)
from humanoid_power.infrastructure.repositories import SettingsRepository
from humanoid_power.infrastructure.system_clock import SystemClock
from humanoid_power.services.operation_queue import OperationCoordinator
from humanoid_power.services.state_service import StateCache


class ScheduleService:
    def __init__(
        self,
        adapter: PowerAdapter,
        cache: StateCache,
        settings: SettingsRepository,
        coordinator: OperationCoordinator,
        hardware_lock: threading.RLock,
        clock: SystemClock,
        require_time_sync: bool = True,
    ) -> None:
        self.adapter = adapter
        self.cache = cache
        self.settings = settings
        self.coordinator = coordinator
        self.hardware_lock = hardware_lock
        self.clock = clock
        self.require_time_sync = require_time_sync

    def request_write(
        self,
        *,
        outlet_id: int,
        payload: dict[str, Any],
        idempotency_key: UUID,
        expected_config_version: int,
        expected_schedule_hash: str,
        user_id: int,
    ) -> HardwareOperation:
        self._validate_write_access(outlet_id, expected_config_version)
        current = self.cache.get_schedule(outlet_id)
        if current.data_quality is not DataQuality.GOOD:
            raise DomainError(
                "SCHEDULE_STATE_UNKNOWN", "Current hardware schedule is not readable.", 409
            )
        if current.canonical_hash() != expected_schedule_hash:
            raise DomainError(
                "SCHEDULE_VERSION_CONFLICT",
                "Hardware schedule changed. Reload before saving.",
                409,
            )
        schedule = self._build_schedule(outlet_id, payload)
        operation = HardwareOperation(
            kind=OperationKind.WRITE_SCHEDULE,
            outlet_id=outlet_id,
            source=OperationSource.WEB_SCHEDULE,
            requested_by=user_id,
            payload={
                "schedule": schedule.canonical_payload(),
                "repeat_mode": schedule.repeat_mode.value,
            },
            idempotency_key=idempotency_key,
        )
        return self.coordinator.submit(operation)

    def request_delete(
        self,
        *,
        outlet_id: int,
        idempotency_key: UUID,
        expected_config_version: int,
        expected_schedule_hash: str,
        user_id: int,
    ) -> HardwareOperation:
        self._validate_write_access(outlet_id, expected_config_version, check_time=False)
        current = self.cache.get_schedule(outlet_id)
        if current.data_quality is not DataQuality.GOOD:
            raise DomainError(
                "SCHEDULE_STATE_UNKNOWN", "Current hardware schedule is not readable.", 409
            )
        if current.canonical_hash() != expected_schedule_hash:
            raise DomainError(
                "SCHEDULE_VERSION_CONFLICT",
                "Hardware schedule changed. Reload before deleting.",
                409,
            )
        operation = HardwareOperation(
            kind=OperationKind.DELETE_SCHEDULE,
            outlet_id=outlet_id,
            source=OperationSource.WEB_SCHEDULE,
            requested_by=user_id,
            payload={},
            idempotency_key=idempotency_key,
        )
        return self.coordinator.submit(operation)

    def execute(self, operation: HardwareOperation) -> OperationExecutionResult:
        if operation.kind is OperationKind.WRITE_SCHEDULE:
            target = self._schedule_from_operation(operation)
            with self.hardware_lock:
                write_result = self.adapter.write_schedule(operation.outlet_id, target)
                readback = self._readback(operation.outlet_id)
            if readback is not None:
                self.cache.set_schedule(readback.with_next_event(self.clock.timezone))
                if readback.structurally_matches(target):
                    return OperationExecutionResult(
                        OperationStatus.CONFIRMED, "SCHEDULE_CONTROLLER_CONFIRMED"
                    )
                return OperationExecutionResult(
                    OperationStatus.MISMATCH,
                    "READBACK_MISMATCH",
                    technical_detail=write_result.error_code,
                )
            self.cache.record_schedule_failure(operation.outlet_id)
            if write_result.ok or write_result.outcome_uncertain:
                return OperationExecutionResult(OperationStatus.UNKNOWN, "RESULT_UNKNOWN")
            return OperationExecutionResult(
                OperationStatus.FAILED,
                write_result.error_code or "SCHEDULE_COMMAND_FAILED",
                technical_detail=write_result.detail,
            )

        if operation.kind is OperationKind.DELETE_SCHEDULE:
            with self.hardware_lock:
                write_result = self.adapter.delete_schedule(operation.outlet_id)
                readback = self._readback(operation.outlet_id)
            if readback is not None:
                self.cache.set_schedule(readback.with_next_event(self.clock.timezone))
                if not readback.events and readback.loop_minutes == 0:
                    return OperationExecutionResult(
                        OperationStatus.CONFIRMED, "SCHEDULE_DELETE_CONFIRMED"
                    )
                return OperationExecutionResult(
                    OperationStatus.MISMATCH,
                    "READBACK_MISMATCH",
                    technical_detail=write_result.error_code,
                )
            self.cache.record_schedule_failure(operation.outlet_id)
            if write_result.ok or write_result.outcome_uncertain:
                return OperationExecutionResult(OperationStatus.UNKNOWN, "RESULT_UNKNOWN")
            return OperationExecutionResult(
                OperationStatus.FAILED,
                write_result.error_code or "SCHEDULE_COMMAND_FAILED",
                technical_detail=write_result.detail,
            )
        return OperationExecutionResult(OperationStatus.FAILED, "INVALID_OPERATION_KIND")

    def _readback(self, outlet_id: int) -> HardwareSchedule | None:
        for attempt in range(3):
            result = self.adapter.read_schedule(outlet_id)
            if result.ok and result.value is not None:
                return result.value
            if attempt < 2:
                time.sleep(0.15 if attempt == 0 else 0.30)
        return None

    def _validate_write_access(
        self, outlet_id: int, expected_config_version: int, check_time: bool = True
    ) -> None:
        system = self.settings.get_system()
        if system.config_version != expected_config_version:
            raise DomainError(
                "CONFIG_VERSION_CONFLICT", "Configuration changed. Reload before saving.", 409
            )
        if system.active_profile is not Profile.TIMED:
            raise DomainError(
                "PROFILE_NOT_ALLOWED", "Schedules can only be changed in TIMED profile.", 409
            )
        if not self.settings.get_outlet(outlet_id).control_enabled:
            raise DomainError("OUTLET_DISABLED", "This outlet is disabled for control.", 409)
        if self.cache.controller_status is not ControllerStatus.READY:
            raise DomainError(
                "CONTROLLER_UNAVAILABLE", "Controller is not ready for schedule changes.", 503
            )
        if check_time and self.require_time_sync and not self.clock.is_synchronized():
            raise DomainError(
                "TIME_NOT_SYNCHRONIZED",
                "System time is not synchronized. Schedule changes are blocked.",
                409,
            )

    def _build_schedule(self, outlet_id: int, payload: dict[str, Any]) -> HardwareSchedule:
        raw_events = payload.get("events")
        if not isinstance(raw_events, list) or not 1 <= len(raw_events) <= 16:
            raise ValidationError("A schedule requires 1 to 16 events.")
        events: list[ScheduleEvent] = []
        for position, raw in enumerate(raw_events, start=1):
            if not isinstance(raw, dict):
                raise ValidationError("Each schedule event must be an object.")
            try:
                event_date = date.fromisoformat(str(raw["local_date"]))
                event_time = local_time.fromisoformat(str(raw["local_time"])).replace(
                    second=0, microsecond=0
                )
                action = EventAction(str(raw["action"]).upper())
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationError(
                    "Schedule event contains an invalid date, time or action."
                ) from exc
            event = ScheduleEvent(position, event_date, event_time, action)
            self._validate_wall_time(event.local_datetime())
            events.append(event)
        event_datetimes = [event.local_datetime() for event in events]
        unordered = any(
            current <= previous
            for previous, current in zip(event_datetimes, event_datetimes[1:], strict=False)
        )
        if unordered:
            raise ValidationError("Schedule events must be in chronological order.")
        try:
            repeat_mode = RepeatMode(str(payload.get("repeat_mode", "NONE")).upper())
        except ValueError as exc:
            raise ValidationError("Unknown repeat mode.") from exc
        if repeat_mode is RepeatMode.NONE:
            loop_minutes = 0
        elif repeat_mode is RepeatMode.DAILY:
            loop_minutes = 1440
        elif repeat_mode is RepeatMode.WEEKLY:
            loop_minutes = 10080
        else:
            try:
                loop_minutes = int(payload["loop_minutes"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationError("Custom repeat interval must be a positive number.") from exc
            if loop_minutes < 1:
                raise ValidationError("Custom repeat interval must be positive.")
        if loop_minutes and len(event_datetimes) > 1:
            span = int((event_datetimes[-1] - event_datetimes[0]).total_seconds() // 60)
            if span >= loop_minutes:
                raise ValidationError(
                    "Schedule event span must be shorter than the repeat interval."
                )
        first_aware = event_datetimes[0].replace(tzinfo=self.clock.timezone)
        if first_aware <= self.clock.now_local():
            raise ValidationError("The first event of a new schedule must be in the future.")
        return HardwareSchedule(
            outlet_id=outlet_id,
            events=tuple(events),
            repeat_mode=repeat_mode,
            loop_minutes=loop_minutes,
            status=ScheduleStatus.ACTIVE,
            data_quality=DataQuality.GOOD,
        )

    def _validate_wall_time(self, value: datetime) -> None:
        timezone = self.clock.timezone
        valid_offsets: set[object] = set()
        for fold in (0, 1):
            aware = value.replace(tzinfo=timezone, fold=fold)
            round_trip = aware.astimezone(UTC).astimezone(timezone).replace(tzinfo=None)
            if round_trip == value:
                valid_offsets.add(aware.utcoffset())
        if len(valid_offsets) != 1:
            raise ValidationError(
                "Schedule time is missing or ambiguous because of a time-zone transition."
            )

    @staticmethod
    def _schedule_from_operation(operation: HardwareOperation) -> HardwareSchedule:
        raw_schedule = operation.payload["schedule"]
        events = tuple(
            ScheduleEvent(
                position=int(raw["position"]),
                local_date=date.fromisoformat(raw["local_date"]),
                local_time=local_time.fromisoformat(raw["local_time"]),
                action=EventAction(raw["action"]),
            )
            for raw in raw_schedule["events"]
        )
        return HardwareSchedule(
            outlet_id=operation.outlet_id,
            events=events,
            repeat_mode=RepeatMode(operation.payload["repeat_mode"]),
            loop_minutes=int(raw_schedule["loop_minutes"]),
            status=ScheduleStatus.ACTIVE,
            data_quality=DataQuality.GOOD,
        )
