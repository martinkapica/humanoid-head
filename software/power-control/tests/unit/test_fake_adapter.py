from datetime import date, time

from humanoid_power.adapters.fake import FakePowerAdapter
from humanoid_power.domain.enums import (
    DataQuality,
    EventAction,
    OutletState,
    RepeatMode,
    ScheduleStatus,
)
from humanoid_power.domain.models import HardwareSchedule, ScheduleEvent


def test_fake_adapter_reads_and_sets_state() -> None:
    adapter = FakePowerAdapter()
    assert adapter.read_outlet_state(1).value is OutletState.OFF
    assert adapter.set_outlet_state(1, OutletState.ON).ok
    assert adapter.read_outlet_state(1).value is OutletState.ON


def test_fake_adapter_round_trips_schedule() -> None:
    adapter = FakePowerAdapter()
    schedule = HardwareSchedule(
        outlet_id=2,
        events=(ScheduleEvent(1, date(2026, 8, 15), time(10, 0), EventAction.ON),),
        repeat_mode=RepeatMode.DAILY,
        loop_minutes=1440,
        status=ScheduleStatus.ACTIVE,
        data_quality=DataQuality.GOOD,
    )
    assert adapter.write_schedule(2, schedule).ok
    readback = adapter.read_schedule(2)
    assert readback.ok
    assert readback.value is not None
    assert readback.value.structurally_matches(schedule)
    assert adapter.delete_schedule(2).ok
    assert adapter.read_schedule(2).value.status is ScheduleStatus.NONE


def test_fake_adapter_can_simulate_uncertain_write() -> None:
    adapter = FakePowerAdapter()
    adapter.fail_next_code = "TIMEOUT"
    adapter.uncertain_next_write = True
    result = adapter.set_outlet_state(1, OutletState.ON)
    assert not result.ok
    assert result.outcome_uncertain
