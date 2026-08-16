from __future__ import annotations

import threading
import time
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from humanoid_power.adapters.base import PowerAdapter
from humanoid_power.config import PollingConfig
from humanoid_power.domain.enums import (
    ControllerStatus,
    DataQuality,
    ModuleCondition,
    OperationStatus,
    OutletState,
    ScheduleStatus,
)
from humanoid_power.domain.models import (
    HardwareOperation,
    HardwareSchedule,
    OutletConfig,
    OutletObservation,
    StateSnapshot,
    utc_now,
)


class StateCache:
    def __init__(self, polling: PollingConfig) -> None:
        self._lock = threading.RLock()
        self._polling = polling
        self._controller_status = ControllerStatus.STARTING
        self._controller_last_seen: datetime | None = None
        self._observations = {
            outlet_id: OutletObservation(outlet_id=outlet_id) for outlet_id in range(1, 5)
        }
        self._schedules = {
            outlet_id: HardwareSchedule(
                outlet_id=outlet_id,
                status=ScheduleStatus.UNKNOWN,
                data_quality=DataQuality.UNKNOWN,
            )
            for outlet_id in range(1, 5)
        }
        self._active_operations: dict[int, HardwareOperation] = {}
        self._last_operations: dict[int, HardwareOperation] = {}

    @property
    def controller_status(self) -> ControllerStatus:
        with self._lock:
            return self._effective_controller_status(utc_now())

    @property
    def controller_last_seen(self) -> datetime | None:
        with self._lock:
            return self._controller_last_seen

    def set_controller_status(self, status: ControllerStatus, seen: bool = False) -> None:
        with self._lock:
            self._controller_status = status
            if seen:
                self._controller_last_seen = utc_now()

    def get_observation(self, outlet_id: int) -> OutletObservation:
        with self._lock:
            return self._effective_observation(self._observations[outlet_id], utc_now())

    def set_observation(self, observation: OutletObservation) -> None:
        with self._lock:
            self._observations[observation.outlet_id] = observation
            if observation.data_quality is DataQuality.GOOD:
                self._controller_last_seen = observation.observed_at_utc or utc_now()

    def record_observation_failure(self, outlet_id: int, invalid: bool = False) -> None:
        with self._lock:
            previous = self._observations[outlet_id]
            quality = (
                DataQuality.INVALID
                if invalid
                else (
                    DataQuality.STALE
                    if previous.observed_at_utc is not None
                    else DataQuality.UNKNOWN
                )
            )
            self._observations[outlet_id] = replace(
                previous,
                data_quality=quality,
                consecutive_failures=previous.consecutive_failures + 1,
            )

    def get_schedule(self, outlet_id: int) -> HardwareSchedule:
        with self._lock:
            return self._effective_schedule(self._schedules[outlet_id], utc_now())

    def set_schedule(self, schedule: HardwareSchedule) -> None:
        with self._lock:
            self._schedules[schedule.outlet_id] = schedule

    def record_schedule_failure(self, outlet_id: int, invalid: bool = False) -> None:
        with self._lock:
            previous = self._schedules[outlet_id]
            quality = (
                DataQuality.INVALID
                if invalid
                else (
                    DataQuality.STALE
                    if previous.observed_at_utc is not None
                    else DataQuality.UNKNOWN
                )
            )
            status = ScheduleStatus.INVALID if invalid else ScheduleStatus.UNKNOWN
            self._schedules[outlet_id] = replace(previous, data_quality=quality, status=status)

    def set_active_operation(self, operation: HardwareOperation) -> None:
        with self._lock:
            self._active_operations[operation.outlet_id] = operation

    def complete_operation(self, operation: HardwareOperation) -> None:
        with self._lock:
            self._active_operations.pop(operation.outlet_id, None)
            self._last_operations[operation.outlet_id] = operation

    def active_operation(self, outlet_id: int) -> HardwareOperation | None:
        with self._lock:
            return self._active_operations.get(outlet_id)

    def last_operation(self, outlet_id: int) -> HardwareOperation | None:
        with self._lock:
            return self._last_operations.get(outlet_id)

    def any_active_operation(self) -> bool:
        with self._lock:
            return bool(self._active_operations)

    def snapshot(self, outlet_configs: tuple[OutletConfig, ...]) -> StateSnapshot:
        now = utc_now()
        with self._lock:
            observations = tuple(
                self._effective_observation(self._observations[outlet_id], now)
                for outlet_id in range(1, 5)
            )
            schedules = tuple(
                self._effective_schedule(self._schedules[outlet_id], now)
                for outlet_id in range(1, 5)
            )
            controller_status = self._effective_controller_status(now)
            condition = self._condition(
                controller_status,
                observations,
                schedules,
                tuple(self._last_operations.values()),
                outlet_configs,
            )
            return StateSnapshot(
                controller_status=controller_status,
                module_condition=condition,
                observations=observations,
                schedules=schedules,
                active_operations=tuple(self._active_operations.values()),
                published_at_utc=now,
            )

    def _effective_controller_status(self, now: datetime) -> ControllerStatus:
        if self._controller_status in {ControllerStatus.AMBIGUOUS, ControllerStatus.STARTING}:
            return self._controller_status
        if self._controller_last_seen is None:
            return ControllerStatus.OFFLINE
        age = (now - self._controller_last_seen).total_seconds()
        if age >= self._polling.offline_after_seconds:
            return ControllerStatus.OFFLINE
        return self._controller_status

    def _effective_observation(
        self, observation: OutletObservation, now: datetime
    ) -> OutletObservation:
        age = observation.age_seconds(now)
        if (
            observation.data_quality is DataQuality.GOOD
            and age is not None
            and age >= self._polling.stale_after_seconds
        ):
            return replace(observation, data_quality=DataQuality.STALE)
        return observation

    def _effective_schedule(self, schedule: HardwareSchedule, now: datetime) -> HardwareSchedule:
        if schedule.observed_at_utc is None:
            return schedule
        age = (now - schedule.observed_at_utc).total_seconds()
        stale_after = max(self._polling.schedule_interval_seconds * 2, 60.0)
        if schedule.data_quality is DataQuality.GOOD and age >= stale_after:
            return replace(schedule, data_quality=DataQuality.STALE)
        return schedule

    @staticmethod
    def _condition(
        controller: ControllerStatus,
        observations: tuple[OutletObservation, ...],
        schedules: tuple[HardwareSchedule, ...],
        last_operations: tuple[HardwareOperation, ...],
        configs: tuple[OutletConfig, ...],
    ) -> ModuleCondition:
        if controller in {ControllerStatus.OFFLINE, ControllerStatus.AMBIGUOUS}:
            return ModuleCondition.FAULT
        if controller is ControllerStatus.STARTING:
            return ModuleCondition.WARNING
        if any(
            schedule.data_quality in {DataQuality.UNKNOWN, DataQuality.INVALID}
            for schedule in schedules
        ):
            return ModuleCondition.FAULT
        critical_ids = {
            config.outlet_id for config in configs if config.criticality.value == "CRITICAL"
        }
        if any(
            operation.outlet_id in critical_ids
            and operation.status in {OperationStatus.UNKNOWN, OperationStatus.MISMATCH}
            for operation in last_operations
        ):
            return ModuleCondition.CRITICAL
        if any(
            operation.status in {OperationStatus.UNKNOWN, OperationStatus.MISMATCH}
            for operation in last_operations
        ):
            return ModuleCondition.WARNING
        if any(item.data_quality is DataQuality.INVALID for item in observations):
            return ModuleCondition.FAULT
        if any(
            item.data_quality in {DataQuality.STALE, DataQuality.UNKNOWN} for item in observations
        ) or any(schedule.data_quality is DataQuality.STALE for schedule in schedules):
            return ModuleCondition.WARNING
        return ModuleCondition.NORMAL


class StatePoller:
    def __init__(
        self,
        adapter: PowerAdapter,
        cache: StateCache,
        hardware_lock: threading.RLock,
        polling: PollingConfig,
        timezone: str,
    ) -> None:
        self.adapter = adapter
        self.cache = cache
        self.hardware_lock = hardware_lock
        self.polling = polling
        self.timezone = ZoneInfo(timezone)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(target=self._run, name="power-state-poller", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def request_reconcile(self) -> None:
        self._wake.set()

    def reconcile_once(self, include_schedules: bool = True) -> None:
        with self.hardware_lock:
            inventory_result = self.adapter.scan_controllers()
        if not inventory_result.ok or inventory_result.value is None:
            self.cache.set_controller_status(ControllerStatus.OFFLINE)
            for outlet_id in range(1, 5):
                self.cache.record_observation_failure(outlet_id)
                if include_schedules:
                    self.cache.record_schedule_failure(outlet_id)
            return
        inventory = inventory_result.value
        if inventory.status is not ControllerStatus.READY:
            self.cache.set_controller_status(inventory.status)
            for outlet_id in range(1, 5):
                self.cache.record_observation_failure(outlet_id)
            return

        failures = 0
        for outlet_id in range(1, 5):
            previous = self.cache.get_observation(outlet_id)
            with self.hardware_lock:
                result = self.adapter.read_outlet_state(outlet_id)
            if result.ok and result.value is not None:
                changed = previous.reported_state not in {OutletState.UNKNOWN, result.value}
                self.cache.set_observation(
                    OutletObservation(
                        outlet_id=outlet_id,
                        reported_state=result.value,
                        data_quality=DataQuality.GOOD,
                        observed_at_utc=utc_now(),
                        consecutive_failures=0,
                        last_change_at_utc=utc_now() if changed else previous.last_change_at_utc,
                        last_known_source=(
                            "CONTROLLER EVENT" if changed else previous.last_known_source
                        ),
                    )
                )
            else:
                failures += 1
                invalid = result.error_code in {"PARSE_ERROR", "UNEXPECTED_OUTPUT"}
                self.cache.record_observation_failure(outlet_id, invalid=invalid)

        if include_schedules:
            for outlet_id in range(1, 5):
                with self.hardware_lock:
                    schedule_result = self.adapter.read_schedule(outlet_id)
                if schedule_result.ok and schedule_result.value is not None:
                    self.cache.set_schedule(schedule_result.value.with_next_event(self.timezone))
                else:
                    invalid = schedule_result.error_code in {
                        "PARSE_ERROR",
                        "UNEXPECTED_OUTPUT",
                    }
                    self.cache.record_schedule_failure(outlet_id, invalid=invalid)

        self.cache.set_controller_status(
            ControllerStatus.DEGRADED if failures else ControllerStatus.READY,
            seen=failures < 4,
        )

    def _run(self) -> None:
        next_schedule_poll = 0.0
        backoff = self.polling.outlet_interval_seconds
        while not self._stop.is_set():
            now_monotonic = time.monotonic()
            include_schedules = now_monotonic >= next_schedule_poll
            try:
                self.reconcile_once(include_schedules=include_schedules)
                if include_schedules:
                    next_schedule_poll = time.monotonic() + self.polling.schedule_interval_seconds
                backoff = self.polling.outlet_interval_seconds
            except Exception:
                self.cache.set_controller_status(ControllerStatus.DEGRADED)
                backoff = min(max(backoff * 2, 2.0), self.polling.backoff_max_seconds)
            self._wake.wait(backoff)
            self._wake.clear()
