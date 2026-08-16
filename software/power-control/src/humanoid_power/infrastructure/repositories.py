from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from humanoid_power.domain.enums import (
    Criticality,
    OperationKind,
    OperationSource,
    OperationStatus,
    OutletState,
    Profile,
    Severity,
)
from humanoid_power.domain.errors import DomainError
from humanoid_power.domain.models import (
    HardwareOperation,
    OutletConfig,
    SystemSettings,
    utc_now,
)
from humanoid_power.infrastructure.database import Database


def _parse_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: int
    username: str
    password_hash: str
    is_active: bool
    created_at_utc: datetime
    password_changed_at_utc: datetime
    failed_login_count: int
    locked_until_utc: datetime | None
    last_failed_login_at_utc: datetime | None


class SettingsRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def initialize_defaults(self) -> None:
        now = utc_now().isoformat()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO system_settings(
                    id, active_profile, timezone, show_schedules_tab, show_activity_tab,
                    technical_details_default, config_version, updated_at_utc
                ) VALUES (1, 'TIMED', 'Europe/Berlin', 1, 1, 0, 1, ?)
                """,
                (now,),
            )
            for outlet_id in range(1, 5):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO outlet_settings(
                        outlet_id, name, description, dashboard_visible, control_enabled,
                        criticality, confirm_on, confirm_off, revision, updated_at_utc
                    ) VALUES (?, ?, '', 1, 1, 'NORMAL', 0, 0, 1, ?)
                    """,
                    (outlet_id, f"Power outlet {outlet_id}", now),
                )

    def get_system(self) -> SystemSettings:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM system_settings WHERE id = 1").fetchone()
        if row is None:
            raise RuntimeError("System settings were not initialized.")
        return SystemSettings(
            active_profile=Profile(row["active_profile"]),
            timezone=row["timezone"],
            show_schedules_tab=bool(row["show_schedules_tab"]),
            show_activity_tab=bool(row["show_activity_tab"]),
            technical_details_default=bool(row["technical_details_default"]),
            config_version=row["config_version"],
        )

    def list_outlets(self) -> tuple[OutletConfig, ...]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM outlet_settings ORDER BY outlet_id").fetchall()
        return tuple(self._outlet_from_row(row) for row in rows)

    def get_outlet(self, outlet_id: int) -> OutletConfig:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM outlet_settings WHERE outlet_id = ?", (outlet_id,)
            ).fetchone()
        if row is None:
            raise DomainError("VALIDATION_FAILED", "Unknown outlet.", 404)
        return self._outlet_from_row(row)

    @staticmethod
    def _outlet_from_row(row: sqlite3.Row) -> OutletConfig:
        return OutletConfig(
            outlet_id=row["outlet_id"],
            name=row["name"],
            description=row["description"],
            dashboard_visible=bool(row["dashboard_visible"]),
            control_enabled=bool(row["control_enabled"]),
            criticality=Criticality(row["criticality"]),
            confirm_on=bool(row["confirm_on"]),
            confirm_off=bool(row["confirm_off"]),
            revision=row["revision"],
        )

    def update_profile(
        self, profile: Profile, expected_version: int, user_id: int
    ) -> SystemSettings:
        now = utc_now().isoformat()
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT config_version FROM system_settings WHERE id = 1"
            ).fetchone()
            if row is None or row["config_version"] != expected_version:
                raise DomainError(
                    "CONFIG_VERSION_CONFLICT",
                    "Settings changed in another client. Reload before saving.",
                    409,
                )
            connection.execute(
                """
                UPDATE system_settings
                SET active_profile = ?, config_version = config_version + 1,
                    updated_at_utc = ?, updated_by = ?
                WHERE id = 1
                """,
                (profile.value, now, user_id),
            )
        return self.get_system()

    def update_interface(
        self,
        *,
        show_schedules_tab: bool,
        show_activity_tab: bool,
        technical_details_default: bool,
        expected_version: int,
        user_id: int,
    ) -> SystemSettings:
        now = utc_now().isoformat()
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT config_version FROM system_settings WHERE id = 1"
            ).fetchone()
            if row is None or row["config_version"] != expected_version:
                raise DomainError(
                    "CONFIG_VERSION_CONFLICT",
                    "Settings changed in another client. Reload before saving.",
                    409,
                )
            connection.execute(
                """
                UPDATE system_settings
                SET show_schedules_tab = ?, show_activity_tab = ?,
                    technical_details_default = ?, config_version = config_version + 1,
                    updated_at_utc = ?, updated_by = ?
                WHERE id = 1
                """,
                (
                    int(show_schedules_tab),
                    int(show_activity_tab),
                    int(technical_details_default),
                    now,
                    user_id,
                ),
            )
        return self.get_system()

    def update_outlet(
        self, outlet: OutletConfig, expected_version: int, user_id: int
    ) -> tuple[OutletConfig, SystemSettings]:
        now = utc_now().isoformat()
        with self.database.transaction(immediate=True) as connection:
            system = connection.execute(
                "SELECT config_version FROM system_settings WHERE id = 1"
            ).fetchone()
            current = connection.execute(
                "SELECT revision FROM outlet_settings WHERE outlet_id = ?", (outlet.outlet_id,)
            ).fetchone()
            if system is None or system["config_version"] != expected_version:
                raise DomainError(
                    "CONFIG_VERSION_CONFLICT",
                    "Settings changed in another client. Reload before saving.",
                    409,
                )
            if current is None or current["revision"] != outlet.revision:
                raise DomainError(
                    "CONFIG_VERSION_CONFLICT",
                    "Outlet settings changed in another client. Reload before saving.",
                    409,
                )
            connection.execute(
                """
                UPDATE outlet_settings
                SET name = ?, description = ?, dashboard_visible = ?, control_enabled = ?,
                    criticality = ?, confirm_on = ?, confirm_off = ?,
                    revision = revision + 1, updated_at_utc = ?, updated_by = ?
                WHERE outlet_id = ?
                """,
                (
                    outlet.name,
                    outlet.description,
                    int(outlet.dashboard_visible),
                    int(outlet.control_enabled),
                    outlet.criticality.value,
                    int(outlet.confirm_on),
                    int(outlet.confirm_off),
                    now,
                    user_id,
                    outlet.outlet_id,
                ),
            )
            connection.execute(
                """
                UPDATE system_settings
                SET config_version = config_version + 1, updated_at_utc = ?, updated_by = ?
                WHERE id = 1
                """,
                (now, user_id),
            )
        return self.get_outlet(outlet.outlet_id), self.get_system()


class UserRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _from_row(row: sqlite3.Row | None) -> UserRecord | None:
        if row is None:
            return None
        return UserRecord(
            id=row["id"],
            username=row["username"],
            password_hash=row["password_hash"],
            is_active=bool(row["is_active"]),
            created_at_utc=datetime.fromisoformat(row["created_at_utc"]),
            password_changed_at_utc=datetime.fromisoformat(row["password_changed_at_utc"]),
            failed_login_count=row["failed_login_count"],
            locked_until_utc=_parse_datetime(row["locked_until_utc"]),
            last_failed_login_at_utc=_parse_datetime(row["last_failed_login_at_utc"]),
        )

    def get_by_id(self, user_id: int) -> UserRecord | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._from_row(row)

    def get_by_username(self, username: str) -> UserRecord | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
        return self._from_row(row)

    def upsert_admin(self, username: str, password_hash: str) -> UserRecord:
        now = utc_now().isoformat()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO users(
                    username, password_hash, is_active, created_at_utc, password_changed_at_utc
                ) VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                    password_hash = excluded.password_hash,
                    is_active = 1,
                    password_changed_at_utc = excluded.password_changed_at_utc,
                    failed_login_count = 0,
                    locked_until_utc = NULL,
                    last_failed_login_at_utc = NULL
                """,
                (username, password_hash, now, now),
            )
        user = self.get_by_username(username)
        if user is None:
            raise RuntimeError("Unable to create administrator.")
        return user

    def record_failed_login(self, user: UserRecord) -> UserRecord:
        now = utc_now()
        inside_window = (
            user.last_failed_login_at_utc is not None
            and now - user.last_failed_login_at_utc <= timedelta(minutes=15)
        )
        failures = user.failed_login_count + 1 if inside_window else 1
        locked_until = now + timedelta(minutes=15) if failures >= 5 else None
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE users
                SET failed_login_count = ?, locked_until_utc = ?, last_failed_login_at_utc = ?
                WHERE id = ?
                """,
                (
                    failures,
                    locked_until.isoformat() if locked_until else None,
                    now.isoformat(),
                    user.id,
                ),
            )
        updated = self.get_by_id(user.id)
        if updated is None:
            raise RuntimeError("User disappeared during login update.")
        return updated

    def record_successful_login(self, user_id: int) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE users
                SET failed_login_count = 0, locked_until_utc = NULL,
                    last_failed_login_at_utc = NULL
                WHERE id = ?
                """,
                (user_id,),
            )


class OperationRepository:
    _active_statuses = (OperationStatus.QUEUED.value, OperationStatus.RUNNING.value)

    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, operation: HardwareOperation) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO hardware_operations(
                    operation_id, idempotency_key, kind, outlet_id, source, requested_by,
                    payload_json, status, requested_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(operation.operation_id),
                    str(operation.idempotency_key),
                    operation.kind.value,
                    operation.outlet_id,
                    operation.source.value,
                    operation.requested_by,
                    json.dumps(operation.payload, sort_keys=True, separators=(",", ":")),
                    operation.status.value,
                    operation.requested_at_utc.isoformat(),
                ),
            )

    def update(self, operation: HardwareOperation) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE hardware_operations
                SET status = ?, started_at_utc = ?, completed_at_utc = ?, state_before = ?,
                    state_after = ?, result_code = ?, technical_detail = ?
                WHERE operation_id = ?
                """,
                (
                    operation.status.value,
                    operation.started_at_utc.isoformat() if operation.started_at_utc else None,
                    operation.completed_at_utc.isoformat() if operation.completed_at_utc else None,
                    operation.state_before.value if operation.state_before else None,
                    operation.state_after.value if operation.state_after else None,
                    operation.result_code,
                    operation.technical_detail,
                    str(operation.operation_id),
                ),
            )

    @staticmethod
    def _from_row(row: sqlite3.Row | None) -> HardwareOperation | None:
        if row is None:
            return None
        return HardwareOperation(
            operation_id=UUID(row["operation_id"]),
            idempotency_key=UUID(row["idempotency_key"]),
            kind=OperationKind(row["kind"]),
            outlet_id=row["outlet_id"],
            source=OperationSource(row["source"]),
            requested_by=row["requested_by"],
            payload=json.loads(row["payload_json"]),
            status=OperationStatus(row["status"]),
            requested_at_utc=datetime.fromisoformat(row["requested_at_utc"]),
            started_at_utc=_parse_datetime(row["started_at_utc"]),
            completed_at_utc=_parse_datetime(row["completed_at_utc"]),
            state_before=OutletState(row["state_before"]) if row["state_before"] else None,
            state_after=OutletState(row["state_after"]) if row["state_after"] else None,
            result_code=row["result_code"],
            technical_detail=row["technical_detail"],
        )

    def get(self, operation_id: UUID | str) -> HardwareOperation | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM hardware_operations WHERE operation_id = ?", (str(operation_id),)
            ).fetchone()
        return self._from_row(row)

    def get_by_idempotency(self, key: UUID | str) -> HardwareOperation | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM hardware_operations WHERE idempotency_key = ?", (str(key),)
            ).fetchone()
        return self._from_row(row)

    def active_for_outlet(self, outlet_id: int) -> HardwareOperation | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM hardware_operations
                WHERE outlet_id = ? AND status IN (?, ?)
                ORDER BY requested_at_utc LIMIT 1
                """,
                (outlet_id, *self._active_statuses),
            ).fetchone()
        return self._from_row(row)

    def last_confirmed_switch(self, outlet_id: int) -> HardwareOperation | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM hardware_operations
                WHERE outlet_id = ? AND kind = 'SET_OUTLET_STATE' AND status = 'CONFIRMED'
                ORDER BY completed_at_utc DESC LIMIT 1
                """,
                (outlet_id,),
            ).fetchone()
        return self._from_row(row)

    def recover_incomplete(self) -> int:
        now = utc_now().isoformat()
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE hardware_operations
                SET status = 'UNKNOWN', completed_at_utc = ?, result_code = 'PROCESS_RESTARTED'
                WHERE status IN ('QUEUED', 'RUNNING')
                """,
                (now,),
            )
        return cursor.rowcount

    def delete_completed_before(self, cutoff: datetime) -> int:
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                DELETE FROM hardware_operations
                WHERE completed_at_utc IS NOT NULL AND completed_at_utc < ?
                  AND status NOT IN ('QUEUED', 'RUNNING')
                """,
                (cutoff.isoformat(),),
            )
        return cursor.rowcount


class EventRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def add(
        self,
        *,
        severity: Severity,
        category: str,
        actor: str,
        message_code: str,
        outlet_id: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO events(
                    occurred_at_utc, severity, category, actor, outlet_id, message_code,
                    details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_now().isoformat(),
                    severity.value,
                    category,
                    actor,
                    outlet_id,
                    message_code,
                    json.dumps(details or {}, sort_keys=True, separators=(",", ":")),
                ),
            )

    def list_recent(
        self,
        limit: int = 100,
        outlet_id: int | None = None,
        category: str | None = None,
        severity: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if outlet_id is not None:
            clauses.append("outlet_id = ?")
            parameters.append(outlet_id)
        if category:
            clauses.append("category = ?")
            parameters.append(category)
        if severity:
            clauses.append("severity = ?")
            parameters.append(severity)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(max(1, min(limit, 500)))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM events {where}
                ORDER BY occurred_at_utc DESC LIMIT ?
                """,  # noqa: S608 - where contains only fixed clauses
                parameters,
            ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "occurred_at_utc": row["occurred_at_utc"],
                "severity": row["severity"],
                "category": row["category"],
                "actor": row["actor"],
                "outlet_id": row["outlet_id"],
                "message_code": row["message_code"],
                "details": json.loads(row["details_json"]),
            }
            for row in rows
        ]

    def delete_expired(self, general_cutoff: datetime, security_cutoff: datetime) -> int:
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                DELETE FROM events
                WHERE (category = 'SECURITY' AND occurred_at_utc < ?)
                   OR (category <> 'SECURITY' AND occurred_at_utc < ?)
                """,
                (security_cutoff.isoformat(), general_cutoff.isoformat()),
            )
        return cursor.rowcount
