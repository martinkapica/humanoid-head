from __future__ import annotations

from humanoid_power.domain.enums import ControllerStatus, DataQuality, Profile, Severity
from humanoid_power.domain.errors import DomainError
from humanoid_power.domain.models import OutletConfig, SystemSettings
from humanoid_power.infrastructure.repositories import EventRepository, SettingsRepository
from humanoid_power.services.state_service import StateCache


class SettingsService:
    def __init__(
        self,
        repository: SettingsRepository,
        events: EventRepository,
        cache: StateCache,
    ) -> None:
        self.repository = repository
        self.events = events
        self.cache = cache

    def change_profile(
        self, profile: Profile, expected_version: int, user_id: int
    ) -> SystemSettings:
        if self.cache.any_active_operation():
            raise DomainError(
                "OUTLET_BUSY", "A hardware operation is running. Try again after it finishes.", 409
            )
        schedules = tuple(self.cache.get_schedule(outlet_id) for outlet_id in range(1, 5))
        if profile is Profile.DIRECT:
            if any(schedule.data_quality is not DataQuality.GOOD for schedule in schedules):
                raise DomainError(
                    "SCHEDULE_STATE_UNKNOWN",
                    "DIRECT requires all hardware schedules to be readable.",
                    409,
                )
            if any(schedule.active for schedule in schedules):
                raise DomainError(
                    "ACTIVE_SCHEDULES_PRESENT",
                    "Delete all active schedules before enabling DIRECT.",
                    409,
                )
        if profile is Profile.TIMED:
            if self.cache.controller_status is not ControllerStatus.READY:
                raise DomainError(
                    "CONTROLLER_UNAVAILABLE", "TIMED requires a ready controller.", 409
                )
            if any(schedule.data_quality is not DataQuality.GOOD for schedule in schedules):
                raise DomainError(
                    "SCHEDULE_STATE_UNKNOWN",
                    "TIMED requires all hardware schedules to be readable.",
                    409,
                )
        updated = self.repository.update_profile(profile, expected_version, user_id)
        self.events.add(
            severity=Severity.INFO,
            category="SETTINGS",
            actor=str(user_id),
            message_code="PROFILE_CHANGED",
            details={"profile": profile.value, "config_version": updated.config_version},
        )
        return updated

    def update_outlet(
        self, outlet: OutletConfig, expected_version: int, user_id: int
    ) -> tuple[OutletConfig, SystemSettings]:
        updated, system = self.repository.update_outlet(outlet, expected_version, user_id)
        self.events.add(
            severity=Severity.INFO,
            category="SETTINGS",
            actor=str(user_id),
            outlet_id=outlet.outlet_id,
            message_code="OUTLET_SETTINGS_CHANGED",
            details={"config_version": system.config_version},
        )
        return updated, system

    def update_interface(
        self,
        *,
        show_schedules_tab: bool,
        show_activity_tab: bool,
        technical_details_default: bool,
        expected_version: int,
        user_id: int,
    ) -> SystemSettings:
        updated = self.repository.update_interface(
            show_schedules_tab=show_schedules_tab,
            show_activity_tab=show_activity_tab,
            technical_details_default=technical_details_default,
            expected_version=expected_version,
            user_id=user_id,
        )
        self.events.add(
            severity=Severity.INFO,
            category="SETTINGS",
            actor=str(user_id),
            message_code="INTERFACE_SETTINGS_CHANGED",
            details={"config_version": updated.config_version},
        )
        return updated
