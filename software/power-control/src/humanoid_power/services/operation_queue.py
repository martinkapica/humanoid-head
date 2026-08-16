from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from contextlib import suppress

from humanoid_power.domain.enums import OperationStatus, Severity
from humanoid_power.domain.errors import DomainError
from humanoid_power.domain.models import (
    HardwareOperation,
    OperationExecutionResult,
    utc_now,
)
from humanoid_power.infrastructure.repositories import EventRepository, OperationRepository
from humanoid_power.services.state_service import StateCache

OperationExecutor = Callable[[HardwareOperation], OperationExecutionResult]


class OperationCoordinator:
    def __init__(
        self,
        repository: OperationRepository,
        events: EventRepository,
        cache: StateCache,
        maximum_operations: int = 20,
    ) -> None:
        self.repository = repository
        self.events = events
        self.cache = cache
        self._queue: queue.Queue[HardwareOperation | None] = queue.Queue(maximum_operations)
        self._submit_lock = threading.RLock()
        self._executor: OperationExecutor | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def set_executor(self, executor: OperationExecutor) -> None:
        self._executor = executor

    def start(self) -> None:
        if self._executor is None:
            raise RuntimeError("Operation executor must be configured before start.")
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.repository.recover_incomplete()
        self._thread = threading.Thread(
            target=self._worker,
            name="power-operation-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 6.0) -> None:
        self._stop.set()
        with suppress(queue.Full):
            self._queue.put_nowait(None)
        if self._thread:
            self._thread.join(timeout)

    def submit(self, operation: HardwareOperation) -> HardwareOperation:
        with self._submit_lock:
            existing = self.repository.get_by_idempotency(operation.idempotency_key)
            if existing is not None:
                same_request = (
                    existing.kind is operation.kind
                    and existing.outlet_id == operation.outlet_id
                    and existing.payload == operation.payload
                )
                if same_request:
                    return existing
                raise DomainError(
                    "IDEMPOTENCY_CONFLICT",
                    "The idempotency key was already used for another request.",
                    409,
                )
            active = self.repository.active_for_outlet(operation.outlet_id)
            if active is not None:
                same_requested_result = (
                    active.kind is operation.kind and active.payload == operation.payload
                )
                if same_requested_result:
                    return active
                raise DomainError(
                    "OUTLET_BUSY",
                    "Another operation is already queued or running for this outlet.",
                    409,
                )
            if self._queue.full():
                raise DomainError("QUEUE_FULL", "The hardware queue is full.", 503)
            self.repository.create(operation)
            self.cache.set_active_operation(operation)
            try:
                self._queue.put_nowait(operation)
            except queue.Full as exc:
                operation.status = OperationStatus.REJECTED
                operation.completed_at_utc = utc_now()
                operation.result_code = "QUEUE_FULL"
                self.repository.update(operation)
                self.cache.complete_operation(operation)
                raise DomainError("QUEUE_FULL", "The hardware queue is full.", 503) from exc
            return operation

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                operation = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if operation is None:
                self._queue.task_done()
                break
            operation.status = OperationStatus.RUNNING
            operation.started_at_utc = utc_now()
            self.repository.update(operation)
            self.cache.set_active_operation(operation)
            try:
                if self._executor is None:
                    raise RuntimeError("No executor configured.")
                result = self._executor(operation)
            except Exception as exc:
                result = OperationExecutionResult(
                    OperationStatus.UNKNOWN,
                    "UNHANDLED_OPERATION_ERROR",
                    technical_detail=type(exc).__name__,
                )
            operation.status = result.status
            operation.result_code = result.result_code
            operation.state_before = result.state_before
            operation.state_after = result.state_after
            operation.technical_detail = result.technical_detail
            operation.completed_at_utc = utc_now()
            self.repository.update(operation)
            self.cache.complete_operation(operation)
            severity = (
                Severity.INFO
                if operation.status is OperationStatus.CONFIRMED
                else Severity.WARNING
                if operation.status in {OperationStatus.MISMATCH, OperationStatus.UNKNOWN}
                else Severity.ERROR
            )
            self.events.add(
                severity=severity,
                category="HARDWARE_OPERATION",
                actor=str(operation.requested_by or "SYSTEM"),
                outlet_id=operation.outlet_id,
                message_code=operation.result_code or operation.status.value,
                details={
                    "operation_id": str(operation.operation_id),
                    "kind": operation.kind.value,
                    "status": operation.status.value,
                },
            )
            self._queue.task_done()
