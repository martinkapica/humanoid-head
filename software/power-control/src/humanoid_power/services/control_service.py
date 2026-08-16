from __future__ import annotations

import threading
import time
from uuid import UUID

from humanoid_power.adapters.base import PowerAdapter
from humanoid_power.domain.enums import (
    ControllerStatus,
    DataQuality,
    OperationKind,
    OperationSource,
    OperationStatus,
    OutletState,
    Profile,
)
from humanoid_power.domain.errors import DomainError
from humanoid_power.domain.models import (
    HardwareOperation,
    OperationExecutionResult,
    OutletObservation,
    utc_now,
)
from humanoid_power.infrastructure.repositories import OperationRepository, SettingsRepository
from humanoid_power.services.operation_queue import OperationCoordinator
from humanoid_power.services.state_service import StateCache


class ControlService:
    def __init__(
        self,
        adapter: PowerAdapter,
        cache: StateCache,
        settings: SettingsRepository,
        operations: OperationRepository,
        coordinator: OperationCoordinator,
        hardware_lock: threading.RLock,
        minimum_switch_interval_seconds: float = 2.0,
    ) -> None:
        self.adapter = adapter
        self.cache = cache
        self.settings = settings
        self.operations = operations
        self.coordinator = coordinator
        self.hardware_lock = hardware_lock
        self.minimum_switch_interval_seconds = minimum_switch_interval_seconds

    def request_state_change(
        self,
        *,
        outlet_id: int,
        target: OutletState,
        idempotency_key: UUID,
        expected_config_version: int,
        confirmed_critical_action: bool,
        user_id: int,
    ) -> HardwareOperation:
        system = self.settings.get_system()
        if system.config_version != expected_config_version:
            raise DomainError(
                "CONFIG_VERSION_CONFLICT",
                "Configuration changed. Reload before sending a command.",
                409,
            )
        if system.active_profile is Profile.MONITOR:
            raise DomainError("PROFILE_NOT_ALLOWED", "MONITOR profile is read-only.", 409)
        outlet = self.settings.get_outlet(outlet_id)
        if not outlet.control_enabled:
            raise DomainError("OUTLET_DISABLED", "This outlet is disabled for control.", 409)
        if outlet.requires_confirmation(target) and not confirmed_critical_action:
            raise DomainError(
                "CRITICAL_CONFIRMATION_REQUIRED",
                "This critical action requires explicit confirmation.",
                409,
            )
        if self.cache.controller_status is not ControllerStatus.READY:
            raise DomainError(
                "CONTROLLER_UNAVAILABLE", "Controller is not ready for commands.", 503
            )
        observation = self.cache.get_observation(outlet_id)
        if observation.data_quality is not DataQuality.GOOD:
            raise DomainError(
                "CONTROLLER_UNAVAILABLE", "Current outlet state is not confirmed.", 503
            )
        previous = self.operations.last_confirmed_switch(outlet_id)
        if previous and previous.completed_at_utc:
            elapsed = (utc_now() - previous.completed_at_utc).total_seconds()
            switching_too_soon = elapsed < self.minimum_switch_interval_seconds
            if switching_too_soon and previous.state_after is not target:
                raise DomainError(
                    "MINIMUM_SWITCH_INTERVAL",
                    "Wait before switching this outlet again.",
                    409,
                )
        operation = HardwareOperation(
            kind=OperationKind.SET_OUTLET_STATE,
            outlet_id=outlet_id,
            source=OperationSource.WEB_MANUAL,
            requested_by=user_id,
            payload={"target_state": target.value},
            idempotency_key=idempotency_key,
        )
        return self.coordinator.submit(operation)

    def execute(self, operation: HardwareOperation) -> OperationExecutionResult:
        target = OutletState(operation.payload["target_state"])
        with self.hardware_lock:
            before_result = self.adapter.read_outlet_state(operation.outlet_id)
            state_before = before_result.value if before_result.ok else None
            if state_before is target:
                self._publish_confirmed(operation.outlet_id, target, operation.source.value)
                return OperationExecutionResult(
                    OperationStatus.CONFIRMED,
                    "ALREADY_IN_TARGET_STATE",
                    state_before=state_before,
                    state_after=target,
                )
            write_result = self.adapter.set_outlet_state(operation.outlet_id, target)
            last_known: OutletState | None = None
            for attempt in range(3):
                readback = self.adapter.read_outlet_state(operation.outlet_id)
                if readback.ok and readback.value is not None:
                    last_known = readback.value
                    if last_known is target:
                        self._publish_confirmed(operation.outlet_id, target, operation.source.value)
                        return OperationExecutionResult(
                            OperationStatus.CONFIRMED,
                            "CONTROLLER_CONFIRMED",
                            state_before=state_before,
                            state_after=target,
                        )
                if attempt < 2:
                    time.sleep(0.15 if attempt == 0 else 0.30)
        if last_known is not None:
            self.cache.set_observation(
                OutletObservation(
                    outlet_id=operation.outlet_id,
                    reported_state=last_known,
                    data_quality=DataQuality.GOOD,
                    observed_at_utc=utc_now(),
                    consecutive_failures=0,
                    last_change_at_utc=utc_now(),
                    last_known_source="CONTROLLER EVENT",
                )
            )
            return OperationExecutionResult(
                OperationStatus.MISMATCH,
                "READBACK_MISMATCH",
                state_before=state_before,
                state_after=last_known,
                technical_detail=write_result.error_code,
            )
        self.cache.record_observation_failure(operation.outlet_id)
        if write_result.ok or write_result.outcome_uncertain:
            return OperationExecutionResult(
                OperationStatus.UNKNOWN,
                "RESULT_UNKNOWN",
                state_before=state_before,
                technical_detail=write_result.error_code,
            )
        return OperationExecutionResult(
            OperationStatus.FAILED,
            write_result.error_code or "COMMAND_FAILED",
            state_before=state_before,
            technical_detail=write_result.detail,
        )

    def _publish_confirmed(self, outlet_id: int, state: OutletState, source: str) -> None:
        previous = self.cache.get_observation(outlet_id)
        changed = previous.reported_state is not state
        self.cache.set_observation(
            OutletObservation(
                outlet_id=outlet_id,
                reported_state=state,
                data_quality=DataQuality.GOOD,
                observed_at_utc=utc_now(),
                consecutive_failures=0,
                last_change_at_utc=utc_now() if changed else previous.last_change_at_utc,
                last_known_source=source,
            )
        )
