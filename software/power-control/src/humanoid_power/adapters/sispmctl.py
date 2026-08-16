from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from humanoid_power.adapters.base import AdapterResult, AdapterWriteResult
from humanoid_power.domain.enums import (
    ControllerStatus,
    DataQuality,
    EventAction,
    OutletState,
    RepeatMode,
    ScheduleStatus,
)
from humanoid_power.domain.models import (
    ControllerInventory,
    HardwareSchedule,
    ScheduleEvent,
    utc_now,
)


@dataclass(frozen=True, slots=True)
class _RunResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    error_code: str | None = None
    detail: str | None = None
    duration_ms: float = 0.0
    outcome_uncertain: bool = False


class SispmctlAdapter:
    """Conservative adapter for one four-outlet SiS-PM controller."""

    _event_pattern = re.compile(
        r"On\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})(?::\d{2})?\s+"
        r"switch\s+(on|off)",
        re.IGNORECASE,
    )
    _loop_pattern = re.compile(r"--Aloop\s+(\d+)")
    _device_pattern = re.compile(
    r"^Gembird\s+#(\d+)(?:\s+device\s+type:.*)?\s*$",
    re.MULTILINE,
)

    def __init__(self, binary_path: str = "/usr/bin/sispmctl", timeout_seconds: float = 3.0):
        self.binary_path = str(Path(binary_path))
        self.timeout_seconds = max(0.25, timeout_seconds)
        self._environment = {
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C",
            "LC_ALL": "C",
        }

    @staticmethod
    def _valid_outlet(outlet_id: int) -> bool:
        return outlet_id in {1, 2, 3, 4}

    def _run(self, *arguments: str, write: bool = False) -> _RunResult:
        started = time.monotonic()
        if not os.path.isabs(self.binary_path) or not Path(self.binary_path).is_file():
            return _RunResult(
                False,
                error_code="BINARY_NOT_FOUND",
                detail="Configured sispmctl binary was not found.",
                duration_ms=(time.monotonic() - started) * 1000,
            )
        try:
            completed = subprocess.run(
                [self.binary_path, *arguments],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                env=self._environment,
            )
        except subprocess.TimeoutExpired:
            return _RunResult(
                False,
                error_code="TIMEOUT",
                detail="sispmctl exceeded the configured timeout.",
                duration_ms=(time.monotonic() - started) * 1000,
                outcome_uncertain=write,
            )
        except OSError as exc:
            return _RunResult(
                False,
                error_code="NONZERO_EXIT",
                detail=f"Unable to execute sispmctl: {type(exc).__name__}",
                duration_ms=(time.monotonic() - started) * 1000,
            )
        duration_ms = (time.monotonic() - started) * 1000
        stdout = completed.stdout[:32_768]
        stderr = completed.stderr[:8_192]
        if completed.returncode != 0:
            return _RunResult(
                False,
                stdout=stdout,
                stderr=stderr,
                error_code="NONZERO_EXIT",
                detail=f"sispmctl returned exit code {completed.returncode}.",
                duration_ms=duration_ms,
            )
        return _RunResult(True, stdout=stdout, stderr=stderr, duration_ms=duration_ms)

    def scan_controllers(self) -> AdapterResult[ControllerInventory]:
        result = self._run("-s")
        if not result.ok:
            return AdapterResult(
                False,
                error_code=result.error_code,
                detail=result.detail,
                duration_ms=result.duration_ms,
            )
        indices = set(self._device_pattern.findall(result.stdout))
        count = len(indices)
        if count == 0:
            inventory = ControllerInventory(0, ControllerStatus.OFFLINE, "No controller found")
        elif count == 1:
            inventory = ControllerInventory(1, ControllerStatus.READY, result.stdout.strip()[:240])
        else:
            inventory = ControllerInventory(
                count, ControllerStatus.AMBIGUOUS, "More than one controller found"
            )
        return AdapterResult(True, inventory, duration_ms=result.duration_ms)

    def read_outlet_state(self, outlet_id: int) -> AdapterResult[OutletState]:
        if not self._valid_outlet(outlet_id):
            return AdapterResult(False, error_code="INVALID_OUTLET")
        result = self._run("-nqg", str(outlet_id))
        if not result.ok:
            return AdapterResult(
                False,
                error_code=result.error_code,
                detail=result.detail,
                duration_ms=result.duration_ms,
            )
        value = result.stdout.strip()
        if value == "1":
            state = OutletState.ON
        elif value == "0":
            state = OutletState.OFF
        else:
            return AdapterResult(
                False,
                error_code="UNEXPECTED_OUTPUT",
                detail="Controller state output was neither 0 nor 1.",
                duration_ms=result.duration_ms,
            )
        return AdapterResult(True, state, duration_ms=result.duration_ms)

    def set_outlet_state(self, outlet_id: int, target: OutletState) -> AdapterWriteResult:
        if not self._valid_outlet(outlet_id) or target not in {OutletState.ON, OutletState.OFF}:
            return AdapterWriteResult(False, error_code="INVALID_OUTLET")
        option = "-o" if target is OutletState.ON else "-f"
        result = self._run(option, str(outlet_id), write=True)
        return AdapterWriteResult(
            result.ok,
            error_code=result.error_code,
            detail=result.detail,
            duration_ms=result.duration_ms,
            outcome_uncertain=result.outcome_uncertain,
        )

    def read_schedule(self, outlet_id: int) -> AdapterResult[HardwareSchedule]:
        if not self._valid_outlet(outlet_id):
            return AdapterResult(False, error_code="INVALID_OUTLET")
        result = self._run("-a", str(outlet_id))
        if not result.ok:
            return AdapterResult(
                False,
                error_code=result.error_code,
                detail=result.detail,
                duration_ms=result.duration_ms,
            )
        events: list[ScheduleEvent] = []
        for position, match in enumerate(self._event_pattern.finditer(result.stdout), start=1):
            try:
                local_datetime = datetime.strptime(
                    f"{match.group(1)} {match.group(2)}", "%Y-%m-%d %H:%M"
                )
            except ValueError:
                return AdapterResult(
                    False,
                    error_code="PARSE_ERROR",
                    detail="Schedule contains an invalid date or time.",
                    duration_ms=result.duration_ms,
                )
            events.append(
                ScheduleEvent(
                    position=position,
                    local_date=local_datetime.date(),
                    local_time=local_datetime.time(),
                    action=EventAction(match.group(3).upper()),
                )
            )
        if re.search(r"\bOn\s+\d", result.stdout, re.IGNORECASE) and not events:
            return AdapterResult(
                False,
                error_code="PARSE_ERROR",
                detail="Schedule output contained unparsed event data.",
                duration_ms=result.duration_ms,
            )
        loop_match = self._loop_pattern.search(result.stdout)
        loop_minutes = int(loop_match.group(1)) if loop_match else 0
        if loop_minutes == 1440:
            repeat_mode = RepeatMode.DAILY
        elif loop_minutes == 10080:
            repeat_mode = RepeatMode.WEEKLY
        elif loop_minutes > 0:
            repeat_mode = RepeatMode.CUSTOM
        else:
            repeat_mode = RepeatMode.NONE
        schedule = HardwareSchedule(
            outlet_id=outlet_id,
            events=tuple(events),
            repeat_mode=repeat_mode,
            loop_minutes=loop_minutes,
            status=ScheduleStatus.ACTIVE if events else ScheduleStatus.NONE,
            data_quality=DataQuality.GOOD,
            observed_at_utc=utc_now(),
        )
        return AdapterResult(True, schedule, duration_ms=result.duration_ms)

    def write_schedule(self, outlet_id: int, schedule: HardwareSchedule) -> AdapterWriteResult:
        if not self._valid_outlet(outlet_id) or schedule.outlet_id != outlet_id:
            return AdapterWriteResult(False, error_code="INVALID_OUTLET")
        if not schedule.events or len(schedule.events) > 16:
            return AdapterWriteResult(False, error_code="VALIDATION_FAILED")
        arguments = ["-A", str(outlet_id)]
        for event in schedule.events:
            arguments.extend(
                [
                    "--Aat",
                    event.local_datetime().strftime("%Y-%m-%d %H:%M"),
                    "--Ado",
                    event.action.value.lower(),
                ]
            )
        if schedule.loop_minutes > 0:
            arguments.extend(["--Aloop", str(schedule.loop_minutes)])
        result = self._run(*arguments, write=True)
        return AdapterWriteResult(
            result.ok,
            error_code=result.error_code,
            detail=result.detail,
            duration_ms=result.duration_ms,
            outcome_uncertain=result.outcome_uncertain,
        )

    def delete_schedule(self, outlet_id: int) -> AdapterWriteResult:
        if not self._valid_outlet(outlet_id):
            return AdapterWriteResult(False, error_code="INVALID_OUTLET")
        result = self._run("-A", str(outlet_id), write=True)
        return AdapterWriteResult(
            result.ok,
            error_code=result.error_code,
            detail=result.detail,
            duration_ms=result.duration_ms,
            outcome_uncertain=result.outcome_uncertain,
        )
