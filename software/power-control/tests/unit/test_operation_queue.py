import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest

from humanoid_power.config import PollingConfig
from humanoid_power.domain.enums import (
    OperationKind,
    OperationSource,
    OperationStatus,
)
from humanoid_power.domain.errors import DomainError
from humanoid_power.domain.models import HardwareOperation, OperationExecutionResult
from humanoid_power.infrastructure.database import Database
from humanoid_power.infrastructure.repositories import EventRepository, OperationRepository
from humanoid_power.services.operation_queue import OperationCoordinator
from humanoid_power.services.state_service import StateCache


def test_queue_serializes_operations(tmp_path: Path) -> None:
    database = Database(tmp_path / "power.db")
    database.initialize()
    repository = OperationRepository(database)
    cache = StateCache(PollingConfig())
    coordinator = OperationCoordinator(repository, EventRepository(database), cache)
    active = 0
    maximum = 0
    lock = threading.Lock()

    def execute(operation: HardwareOperation) -> OperationExecutionResult:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return OperationExecutionResult(OperationStatus.CONFIRMED, "TEST_CONFIRMED")

    coordinator.set_executor(execute)
    coordinator.start()
    first = HardwareOperation(OperationKind.RECONCILE, 1, OperationSource.SYSTEM, None, {})
    second = HardwareOperation(OperationKind.RECONCILE, 2, OperationSource.SYSTEM, None, {})
    coordinator.submit(first)
    coordinator.submit(second)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if (
            repository.get(first.operation_id).status is OperationStatus.CONFIRMED
            and repository.get(second.operation_id).status is OperationStatus.CONFIRMED
        ):
            break
        time.sleep(0.01)
    coordinator.stop()
    assert maximum == 1


def test_same_target_while_running_returns_existing_operation(tmp_path: Path) -> None:
    database = Database(tmp_path / "power.db")
    database.initialize()
    repository = OperationRepository(database)
    coordinator = OperationCoordinator(
        repository, EventRepository(database), StateCache(PollingConfig())
    )
    release = threading.Event()

    def execute(operation: HardwareOperation) -> OperationExecutionResult:
        release.wait(1)
        return OperationExecutionResult(OperationStatus.CONFIRMED, "TEST_CONFIRMED")

    coordinator.set_executor(execute)
    coordinator.start()
    first = HardwareOperation(
        OperationKind.RECONCILE,
        1,
        OperationSource.SYSTEM,
        None,
        {"target_state": "ON"},
    )
    duplicate_target = HardwareOperation(
        OperationKind.RECONCILE,
        1,
        OperationSource.SYSTEM,
        None,
        {"target_state": "ON"},
        idempotency_key=uuid4(),
    )
    accepted = coordinator.submit(first)
    returned = coordinator.submit(duplicate_target)
    release.set()
    coordinator.stop()
    assert returned.operation_id == accepted.operation_id


def test_reused_idempotency_key_with_different_payload_is_rejected(tmp_path: Path) -> None:
    database = Database(tmp_path / "power.db")
    database.initialize()
    coordinator = OperationCoordinator(
        OperationRepository(database), EventRepository(database), StateCache(PollingConfig())
    )
    key = uuid4()
    first = HardwareOperation(
        OperationKind.RECONCILE,
        1,
        OperationSource.SYSTEM,
        None,
        {"target_state": "ON"},
        idempotency_key=key,
    )
    conflicting = HardwareOperation(
        OperationKind.RECONCILE,
        1,
        OperationSource.SYSTEM,
        None,
        {"target_state": "OFF"},
        idempotency_key=key,
    )
    coordinator.submit(first)
    with pytest.raises(DomainError, match="idempotency key"):
        coordinator.submit(conflicting)
