from pathlib import Path

from humanoid_power.domain.enums import Criticality, Profile
from humanoid_power.domain.models import OutletConfig
from humanoid_power.infrastructure.database import Database
from humanoid_power.infrastructure.repositories import SettingsRepository, UserRepository


def test_database_initializes_and_updates_settings(tmp_path: Path) -> None:
    database = Database(tmp_path / "power.db")
    database.initialize()
    repository = SettingsRepository(database)
    repository.initialize_defaults()
    user = UserRepository(database).upsert_admin("admin", "test-hash")
    settings = repository.get_system()
    assert settings.active_profile is Profile.TIMED
    updated = repository.update_profile(Profile.MONITOR, settings.config_version, user_id=user.id)
    assert updated.active_profile is Profile.MONITOR
    assert updated.config_version == settings.config_version + 1


def test_outlet_update_increments_revisions(tmp_path: Path) -> None:
    database = Database(tmp_path / "power.db")
    database.initialize()
    repository = SettingsRepository(database)
    repository.initialize_defaults()
    user = UserRepository(database).upsert_admin("admin", "test-hash")
    original = repository.get_outlet(1)
    replacement = OutletConfig(
        outlet_id=1,
        name="Servo power",
        criticality=Criticality.CRITICAL,
        confirm_on=True,
        revision=original.revision,
    )
    updated, system = repository.update_outlet(
        replacement, repository.get_system().config_version, user_id=user.id
    )
    assert updated.name == "Servo power"
    assert updated.revision == original.revision + 1
    assert system.config_version == 2


def test_database_backup_is_consistent_and_never_overwritten(tmp_path: Path) -> None:
    database = Database(tmp_path / "power.db")
    database.initialize()
    SettingsRepository(database).initialize_defaults()
    target = tmp_path / "backup.db"
    assert database.backup_to(target) == target
    backup = Database(target)
    assert SettingsRepository(backup).get_system().active_profile is Profile.TIMED
    try:
        database.backup_to(target)
    except FileExistsError:
        pass
    else:
        raise AssertionError("Existing backup must not be overwritten.")
