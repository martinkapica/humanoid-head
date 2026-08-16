from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from humanoid_power.domain.enums import OutletState
from humanoid_power.domain.models import ControllerInventory, HardwareSchedule


@dataclass(frozen=True, slots=True)
class AdapterResult[T]:
    ok: bool
    value: T | None = None
    error_code: str | None = None
    detail: str | None = None
    duration_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class AdapterWriteResult:
    ok: bool
    error_code: str | None = None
    detail: str | None = None
    duration_ms: float = 0.0
    outcome_uncertain: bool = False


class PowerAdapter(Protocol):
    def scan_controllers(self) -> AdapterResult[ControllerInventory]: ...

    def read_outlet_state(self, outlet_id: int) -> AdapterResult[OutletState]: ...

    def set_outlet_state(self, outlet_id: int, target: OutletState) -> AdapterWriteResult: ...

    def read_schedule(self, outlet_id: int) -> AdapterResult[HardwareSchedule]: ...

    def write_schedule(self, outlet_id: int, schedule: HardwareSchedule) -> AdapterWriteResult: ...

    def delete_schedule(self, outlet_id: int) -> AdapterWriteResult: ...
