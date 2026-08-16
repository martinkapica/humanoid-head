from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from humanoid_power.domain.enums import (
    Criticality,
    DataQuality,
    EventAction,
    OutletState,
    RepeatMode,
    ScheduleStatus,
)
from humanoid_power.domain.errors import ValidationError
from humanoid_power.domain.models import (
    HardwareSchedule,
    OutletConfig,
    OutletObservation,
    ScheduleEvent,
)


def test_outlet_config_normalizes_text_and_confirmation() -> None:
    config = OutletConfig(
        outlet_id=1,
        name="  Servo power  ",
        criticality=Criticality.CRITICAL,
        confirm_on=True,
    )
    assert config.name == "Servo power"
    assert config.requires_confirmation(OutletState.ON)
    assert not config.requires_confirmation(OutletState.OFF)


def test_outlet_config_rejects_invalid_id() -> None:
    with pytest.raises(ValidationError):
        OutletConfig(outlet_id=5, name="Invalid")


def test_observation_age_is_timezone_aware() -> None:
    observed = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
    observation = OutletObservation(
        outlet_id=1,
        reported_state=OutletState.ON,
        data_quality=DataQuality.GOOD,
        observed_at_utc=observed,
    )
    assert observation.age_seconds(datetime(2026, 8, 14, 8, 0, 5, tzinfo=UTC)) == 5


def test_schedule_hash_is_structural() -> None:
    event = ScheduleEvent(1, date(2026, 8, 15), time(8, 30), EventAction.ON)
    first = HardwareSchedule(
        outlet_id=1,
        events=(event,),
        repeat_mode=RepeatMode.DAILY,
        loop_minutes=1440,
        status=ScheduleStatus.ACTIVE,
        data_quality=DataQuality.GOOD,
    )
    second = HardwareSchedule(
        outlet_id=1,
        events=(event,),
        repeat_mode=RepeatMode.CUSTOM,
        loop_minutes=1440,
        status=ScheduleStatus.ACTIVE,
        data_quality=DataQuality.STALE,
    )
    assert first.structurally_matches(second)


def test_schedule_calculates_next_loop_event() -> None:
    timezone = ZoneInfo("Europe/Berlin")
    event = ScheduleEvent(1, date(2026, 8, 14), time(8, 0), EventAction.ON)
    schedule = HardwareSchedule(
        outlet_id=1,
        events=(event,),
        repeat_mode=RepeatMode.DAILY,
        loop_minutes=1440,
        status=ScheduleStatus.ACTIVE,
        data_quality=DataQuality.GOOD,
    )
    updated = schedule.with_next_event(timezone, datetime(2026, 8, 14, 9, 0, tzinfo=timezone))
    assert updated.next_event_local == datetime(2026, 8, 15, 8, 0, tzinfo=timezone)
