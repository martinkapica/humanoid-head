from __future__ import annotations

from datetime import UTC, datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from humanoid_power.domain.enums import Severity
from humanoid_power.domain.errors import ValidationError
from humanoid_power.infrastructure.repositories import (
    EventRepository,
    UserRecord,
    UserRepository,
)


class AppUser(UserMixin):  # type: ignore[misc]
    def __init__(self, record: UserRecord) -> None:
        self.record = record
        self.id = str(record.id)
        self.username = record.username
        self._is_active = record.is_active

    @property
    def is_active(self) -> bool:
        return self._is_active


class AuthService:
    def __init__(self, users: UserRepository, events: EventRepository) -> None:
        self.users = users
        self.events = events
        self._dummy_hash = generate_password_hash("not-the-real-password")

    @staticmethod
    def validate_password(password: str) -> None:
        if not 12 <= len(password) <= 128:
            raise ValidationError("Password must contain 12 to 128 characters.")

    def set_admin_password(self, password: str, username: str = "admin") -> UserRecord:
        self.validate_password(password)
        user = self.users.upsert_admin(username, generate_password_hash(password))
        self.events.add(
            severity=Severity.INFO,
            category="SECURITY",
            actor=str(user.id),
            message_code="ADMIN_PASSWORD_SET",
        )
        return user

    def authenticate(self, username: str, password: str, remote_address: str) -> AppUser | None:
        record = self.users.get_by_username(username.strip())
        hash_to_check = record.password_hash if record else self._dummy_hash
        password_ok = check_password_hash(hash_to_check, password)
        now = datetime.now(UTC)
        if record and record.locked_until_utc and record.locked_until_utc > now:
            self.events.add(
                severity=Severity.WARNING,
                category="SECURITY",
                actor=username or "UNKNOWN",
                message_code="LOGIN_BLOCKED",
                details={"remote_address": remote_address},
            )
            return None
        if record is None or not record.is_active or not password_ok:
            if record:
                self.users.record_failed_login(record)
            self.events.add(
                severity=Severity.WARNING,
                category="SECURITY",
                actor=username or "UNKNOWN",
                message_code="LOGIN_FAILED",
                details={"remote_address": remote_address},
            )
            return None
        self.users.record_successful_login(record.id)
        self.events.add(
            severity=Severity.INFO,
            category="SECURITY",
            actor=str(record.id),
            message_code="LOGIN_SUCCEEDED",
            details={"remote_address": remote_address},
        )
        refreshed = self.users.get_by_id(record.id)
        return AppUser(refreshed or record)
