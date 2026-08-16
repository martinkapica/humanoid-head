from __future__ import annotations

import threading
import time
from dataclasses import replace

from humanoid_power.adapters.base import AdapterResult, AdapterWriteResult
from humanoid_power.domain.enums import (
    ControllerStatus,
    DataQuality,
    OutletState,
    ScheduleStatus,
)
from humanoid_power.domain.models import ControllerInventory, HardwareSchedule, utc_now


class FakePowerAdapter:
    """Deterministic in-memory adapter used for development and tests."""

    def __init__(self, delay_seconds: float = 0.0) -> None:
        self._lock = threading.RLock()
        self._states = {outlet_id: OutletState.OFF for outlet_id in range(1, 5)}
        self._schedules = {
            outlet_id: HardwareSchedule(
                outlet_id=outlet_id,
                status=ScheduleStatus.NONE,
                data_quality=DataQuality.GOOD,
                observed_at_utc=utc_now(),
            )
            for outlet_id in range(1, 5)
        }
        self.online = True
        self.controller_count = 1
        self.delay_seconds = max(0.0, delay_seconds)
        self.fail_next_code: str | None = None
        self.uncertain_next_write = False
        self.mismatch_next_read: OutletState | None = None
        self.maximum_parallel_calls = 0
        self._active_calls = 0

    def _begin(self) -> float:
        started = time.monotonic()
        self._active_calls += 1
        self.maximum_parallel_calls = max(self.maximum_parallel_calls, self._active_calls)
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        return started

    def _finish(self, started: float) -> float:
        self._active_calls -= 1
        return (time.monotonic() - started) * 1000

    def _consume_failure(self) -> str | None:
        code = self.fail_next_code
        self.fail_next_code = None
        return code

    def scan_controllers(self) -> AdapterResult[ControllerInventory]:
        with self._lock:
            started = self._begin()
            failure = self._consume_failure()
            if failure:
                return AdapterResult(False, error_code=failure, duration_ms=self._finish(started))
            if not self.online or self.controller_count == 0:
                inventory = ControllerInventory(
                    0, ControllerStatus.OFFLINE, "Fake controller offline"
                )
            elif self.controller_count > 1:
                inventory = ControllerInventory(
                    self.controller_count, ControllerStatus.AMBIGUOUS, "Multiple fake controllers"
                )
            else:
                inventory = ControllerInventory(
                    1, ControllerStatus.READY, "Fake 4-outlet controller"
                )
            return AdapterResult(True, inventory, duration_ms=self._finish(started))

    def read_outlet_state(self, outlet_id: int) -> AdapterResult[OutletState]:
        with self._lock:
            started = self._begin()
            if outlet_id not in self._states:
                return AdapterResult(
                    False, error_code="INVALID_OUTLET", duration_ms=self._finish(started)
                )
            failure = self._consume_failure()
            if failure or not self.online:
                return AdapterResult(
                    False,
                    error_code=failure or "CONTROLLER_NOT_FOUND",
                    duration_ms=self._finish(started),
                )
            value = self.mismatch_next_read or self._states[outlet_id]
            self.mismatch_next_read = None
            return AdapterResult(True, value, duration_ms=self._finish(started))

    def set_outlet_state(self, outlet_id: int, target: OutletState) -> AdapterWriteResult:
        with self._lock:
            started = self._begin()
            if outlet_id not in self._states or target not in {OutletState.ON, OutletState.OFF}:
                return AdapterWriteResult(
                    False, error_code="INVALID_OUTLET", duration_ms=self._finish(started)
                )
            failure = self._consume_failure()
            if failure or not self.online:
                uncertain = self.uncertain_next_write
                self.uncertain_next_write = False
                return AdapterWriteResult(
                    False,
                    error_code=failure or "CONTROLLER_NOT_FOUND",
                    duration_ms=self._finish(started),
                    outcome_uncertain=uncertain,
                )
            self._states[outlet_id] = target
            return AdapterWriteResult(True, duration_ms=self._finish(started))

    def read_schedule(self, outlet_id: int) -> AdapterResult[HardwareSchedule]:
        with self._lock:
            started = self._begin()
            if outlet_id not in self._schedules:
                return AdapterResult(
                    False, error_code="INVALID_OUTLET", duration_ms=self._finish(started)
                )
            failure = self._consume_failure()
            if failure or not self.online:
                return AdapterResult(
                    False,
                    error_code=failure or "CONTROLLER_NOT_FOUND",
                    duration_ms=self._finish(started),
                )
            schedule = replace(
                self._schedules[outlet_id],
                data_quality=DataQuality.GOOD,
                observed_at_utc=utc_now(),
            )
            return AdapterResult(True, schedule, duration_ms=self._finish(started))

    def write_schedule(self, outlet_id: int, schedule: HardwareSchedule) -> AdapterWriteResult:
        with self._lock:
            started = self._begin()
            if outlet_id not in self._schedules or schedule.outlet_id != outlet_id:
                return AdapterWriteResult(
                    False, error_code="INVALID_OUTLET", duration_ms=self._finish(started)
                )
            failure = self._consume_failure()
            if failure or not self.online:
                uncertain = self.uncertain_next_write
                self.uncertain_next_write = False
                return AdapterWriteResult(
                    False,
                    error_code=failure or "CONTROLLER_NOT_FOUND",
                    duration_ms=self._finish(started),
                    outcome_uncertain=uncertain,
                )
            self._schedules[outlet_id] = replace(
                schedule,
                status=ScheduleStatus.ACTIVE if schedule.events else ScheduleStatus.NONE,
                data_quality=DataQuality.GOOD,
                observed_at_utc=utc_now(),
            )
            return AdapterWriteResult(True, duration_ms=self._finish(started))

    def delete_schedule(self, outlet_id: int) -> AdapterWriteResult:
        with self._lock:
            started = self._begin()
            if outlet_id not in self._schedules:
                return AdapterWriteResult(
                    False, error_code="INVALID_OUTLET", duration_ms=self._finish(started)
                )
            failure = self._consume_failure()
            if failure or not self.online:
                uncertain = self.uncertain_next_write
                self.uncertain_next_write = False
                return AdapterWriteResult(
                    False,
                    error_code=failure or "CONTROLLER_NOT_FOUND",
                    duration_ms=self._finish(started),
                    outcome_uncertain=uncertain,
                )
            self._schedules[outlet_id] = HardwareSchedule(
                outlet_id=outlet_id,
                status=ScheduleStatus.NONE,
                data_quality=DataQuality.GOOD,
                observed_at_utc=utc_now(),
            )
            return AdapterWriteResult(True, duration_ms=self._finish(started))
