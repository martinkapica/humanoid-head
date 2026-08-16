from __future__ import annotations

import threading
import time
from contextlib import suppress
from datetime import timedelta

from humanoid_power.adapters.base import PowerAdapter
from humanoid_power.config import AppConfig
from humanoid_power.domain.enums import OperationKind, OperationStatus
from humanoid_power.domain.models import HardwareOperation, OperationExecutionResult, utc_now
from humanoid_power.infrastructure.repositories import (
    EventRepository,
    OperationRepository,
    SettingsRepository,
)
from humanoid_power.infrastructure.system_clock import SystemClock
from humanoid_power.services.control_service import ControlService
from humanoid_power.services.operation_queue import OperationCoordinator
from humanoid_power.services.schedule_service import ScheduleService
from humanoid_power.services.settings_service import SettingsService
from humanoid_power.services.state_service import StateCache, StatePoller


class PowerRuntime:
    def __init__(
        self,
        config: AppConfig,
        adapter: PowerAdapter,
        settings_repository: SettingsRepository,
        operation_repository: OperationRepository,
        event_repository: EventRepository,
        clock: SystemClock,
    ) -> None:
        self.config = config
        self.adapter = adapter
        self.hardware_lock = threading.RLock()
        self.cache = StateCache(config.polling)
        self.coordinator = OperationCoordinator(
            operation_repository,
            event_repository,
            self.cache,
            config.queue.maximum_operations,
        )
        self.control = ControlService(
            adapter,
            self.cache,
            settings_repository,
            operation_repository,
            self.coordinator,
            self.hardware_lock,
            config.controller.minimum_switch_interval_seconds,
        )
        self.schedules = ScheduleService(
            adapter,
            self.cache,
            settings_repository,
            self.coordinator,
            self.hardware_lock,
            clock,
            config.application.require_time_sync,
        )
        self.settings = SettingsService(settings_repository, event_repository, self.cache)
        self.clock = clock
        self.poller = StatePoller(
            adapter,
            self.cache,
            self.hardware_lock,
            config.polling,
            config.application.timezone,
        )
        self.coordinator.set_executor(self._execute)
        self._started = False
        self._operation_repository = operation_repository
        self._event_repository = event_repository
        self._maintenance_stop = threading.Event()
        self._maintenance_thread: threading.Thread | None = None

    def start(self) -> None:
        if self._started:
            return
        self.coordinator.start()
        self.poller.start()
        self._maintenance_stop.clear()
        with suppress(Exception):
            self.clock.refresh_synchronization()
        with suppress(Exception):
            self._run_retention()
        self._maintenance_thread = threading.Thread(
            target=self._maintenance_loop,
            name="power-retention-maintenance",
            daemon=True,
        )
        self._maintenance_thread.start()
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self.poller.stop()
        self.coordinator.stop()
        self._maintenance_stop.set()
        if self._maintenance_thread:
            self._maintenance_thread.join(2.0)
        self._started = False

    def reconcile_now(self, include_schedules: bool = True) -> None:
        self.poller.reconcile_once(include_schedules=include_schedules)

    def _execute(self, operation: HardwareOperation) -> OperationExecutionResult:
        if operation.kind is OperationKind.SET_OUTLET_STATE:
            return self.control.execute(operation)
        if operation.kind in {OperationKind.WRITE_SCHEDULE, OperationKind.DELETE_SCHEDULE}:
            return self.schedules.execute(operation)
        return OperationExecutionResult(OperationStatus.FAILED, "INVALID_OPERATION_KIND")

    def _run_retention(self) -> None:
        now = utc_now()
        self._operation_repository.delete_completed_before(now - timedelta(days=180))
        self._event_repository.delete_expired(
            general_cutoff=now - timedelta(days=90),
            security_cutoff=now - timedelta(days=180),
        )

    def _maintenance_loop(self) -> None:
        next_retention = time.monotonic() + 24 * 60 * 60
        while not self._maintenance_stop.wait(60):
            with suppress(Exception):
                self.clock.refresh_synchronization()
            if time.monotonic() < next_retention:
                continue
            with suppress(Exception):
                self._run_retention()
            next_retention = time.monotonic() + 24 * 60 * 60
