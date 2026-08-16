from __future__ import annotations

import argparse
import getpass
import os
import secrets
from pathlib import Path

from humanoid_power.adapters.fake import FakePowerAdapter
from humanoid_power.app import create_app
from humanoid_power.config import load_config
from humanoid_power.infrastructure.database import Database
from humanoid_power.infrastructure.repositories import (
    EventRepository,
    SettingsRepository,
    UserRepository,
)
from humanoid_power.services.auth_service import AuthService


def _ensure_secret(path: str) -> None:
    target = Path(path)
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(secrets.token_hex(32), encoding="utf-8")
    os.chmod(target, 0o400)


def _initialize(config_path: str) -> tuple[Database, AuthService]:
    config = load_config(config_path)
    _ensure_secret(config.application.secret_key_path)
    database = Database(config.application.database_path)
    database.initialize()
    SettingsRepository(database).initialize_defaults()
    auth = AuthService(UserRepository(database), EventRepository(database))
    return database, auth


def main() -> None:
    parser = argparse.ArgumentParser(prog="humanoid-power")
    parser.add_argument("--config", required=True, help="Path to the TOML configuration file")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db", help="Initialize database, defaults and secret key")
    backup_parser = subparsers.add_parser(
        "backup-db", help="Create a consistent SQLite backup without overwriting a file"
    )
    backup_parser.add_argument("--output", required=True)
    password_parser = subparsers.add_parser(
        "set-admin-password", help="Create or update the single V1 administrator"
    )
    password_parser.add_argument("--username", default="admin")
    serve_parser = subparsers.add_parser("serve", help="Run the local development server")
    serve_parser.add_argument("--fake-adapter", action="store_true")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args()

    if arguments.command == "backup-db":
        config = load_config(arguments.config)
        target = Database(config.application.database_path).backup_to(arguments.output)
        print(f"Database backup created: {target}")
        return
    if arguments.command == "init-db":
        _initialize(arguments.config)
        print("Database and secret key initialized.")
        return
    if arguments.command == "set-admin-password":
        _, auth = _initialize(arguments.config)
        password = getpass.getpass("New administrator password: ")
        confirmation = getpass.getpass("Repeat password: ")
        if password != confirmation:
            raise SystemExit("Passwords do not match.")
        auth.set_admin_password(password, username=arguments.username)
        print("Administrator password updated.")
        return
    if arguments.command == "serve":
        _initialize(arguments.config)
        app = create_app(
            arguments.config,
            adapter=FakePowerAdapter() if arguments.fake_adapter else None,
        )
        app.run(host=arguments.host, port=arguments.port, debug=False)


if __name__ == "__main__":
    main()
