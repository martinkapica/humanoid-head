from datetime import datetime, timedelta

import pytest

from humanoid_power.adapters.fake import FakePowerAdapter
from humanoid_power.config import PollingConfig
from humanoid_power.domain.errors import ValidationError
from humanoid_power.infrastructure.system_clock import SystemClock
from humanoid_power.services.schedule_service import ScheduleService
from humanoid_power.services.state_service import StateCache


def build_service() -> ScheduleService:
    service = object.__new__(ScheduleService)
    service.adapter = FakePowerAdapter()
    service.cache = StateCache(PollingConfig())
    service.clock = SystemClock("Europe/Berlin", force_synchronized=True)
    return service


def test_build_schedule_accepts_future_daily_events() -> None:
    service = build_service()
    future = service.clock.now_local() + timedelta(days=2)
    schedule = service._build_schedule(
        1,
        {
            "events": [
                {
                    "local_date": future.date().isoformat(),
                    "local_time": "08:00",
                    "action": "ON",
                },
                {
                    "local_date": future.date().isoformat(),
                    "local_time": "18:00",
                    "action": "OFF",
                },
            ],
            "repeat_mode": "DAILY",
        },
    )
    assert schedule.loop_minutes == 1440


def test_build_schedule_rejects_past_first_event() -> None:
    service = build_service()
    past = service.clock.now_local() - timedelta(days=1)
    with pytest.raises(ValidationError):
        service._build_schedule(
            1,
            {
                "events": [
                    {
                        "local_date": past.date().isoformat(),
                        "local_time": past.strftime("%H:%M"),
                        "action": "ON",
                    }
                ],
                "repeat_mode": "NONE",
            },
        )


def test_wall_time_rejects_ambiguous_dst_hour() -> None:
    service = build_service()
    with pytest.raises(ValidationError):
        service._validate_wall_time(datetime(2026, 10, 25, 2, 30))
