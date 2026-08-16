from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


@dataclass(frozen=True, slots=True)
class ApplicationConfig:
    timezone: str = "Europe/Berlin"
    database_path: str = "/var/lib/humanoid-control/power.db"
    secret_key_path: str = "/etc/humanoid-control/secret.key"
    runtime_enabled: bool = True
    require_time_sync: bool = True


@dataclass(frozen=True, slots=True)
class ControllerConfig:
    adapter: str = "sispmctl"
    hardware_accepted: bool = False
    binary_path: str = "/usr/bin/sispmctl"
    allowed_outlets: tuple[int, ...] = (1, 2, 3, 4)
    command_timeout_seconds: float = 3.0
    minimum_switch_interval_seconds: float = 2.0


@dataclass(frozen=True, slots=True)
class PollingConfig:
    outlet_interval_seconds: float = 2.0
    stale_after_seconds: float = 5.0
    offline_after_seconds: float = 15.0
    schedule_interval_seconds: float = 30.0
    backoff_max_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class QueueConfig:
    maximum_operations: int = 20


@dataclass(frozen=True, slots=True)
class WebConfig:
    trusted_hosts: tuple[str, ...] = ("humanoid.local",)
    proxy_count: int = 1
    session_idle_minutes: int = 60
    session_absolute_hours: int = 12


@dataclass(frozen=True, slots=True)
class AppConfig:
    application: ApplicationConfig = ApplicationConfig()
    controller: ControllerConfig = ControllerConfig()
    polling: PollingConfig = PollingConfig()
    queue: QueueConfig = QueueConfig()
    web: WebConfig = WebConfig()


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"Configuration section [{name}] must be a table.")
    return value


def load_config(path: str | Path | None = None) -> AppConfig:
    data: dict[str, Any] = {}
    if path is not None:
        config_path = Path(path)
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)

    application = _section(data, "application")
    controller = _section(data, "controller")
    polling = _section(data, "polling")
    queue = _section(data, "queue")
    web = _section(data, "web")

    allowed_outlets = tuple(int(value) for value in controller.get("allowed_outlets", [1, 2, 3, 4]))
    if allowed_outlets != (1, 2, 3, 4):
        raise ValueError("V1 requires exactly allowed_outlets = [1, 2, 3, 4].")

    result = AppConfig(
        application=ApplicationConfig(
            timezone=str(application.get("timezone", "Europe/Berlin")),
            database_path=str(
                application.get("database_path", "/var/lib/humanoid-control/power.db")
            ),
            secret_key_path=str(
                application.get("secret_key_path", "/etc/humanoid-control/secret.key")
            ),
            runtime_enabled=bool(application.get("runtime_enabled", True)),
            require_time_sync=bool(application.get("require_time_sync", True)),
        ),
        controller=ControllerConfig(
            adapter=str(controller.get("adapter", "sispmctl")),
            hardware_accepted=bool(controller.get("hardware_accepted", False)),
            binary_path=str(controller.get("binary_path", "/usr/bin/sispmctl")),
            allowed_outlets=allowed_outlets,
            command_timeout_seconds=float(controller.get("command_timeout_seconds", 3.0)),
            minimum_switch_interval_seconds=float(
                controller.get("minimum_switch_interval_seconds", 2.0)
            ),
        ),
        polling=PollingConfig(
            outlet_interval_seconds=float(polling.get("outlet_interval_seconds", 2.0)),
            stale_after_seconds=float(polling.get("stale_after_seconds", 5.0)),
            offline_after_seconds=float(polling.get("offline_after_seconds", 15.0)),
            schedule_interval_seconds=float(polling.get("schedule_interval_seconds", 30.0)),
            backoff_max_seconds=float(polling.get("backoff_max_seconds", 30.0)),
        ),
        queue=QueueConfig(maximum_operations=int(queue.get("maximum_operations", 20))),
        web=WebConfig(
            trusted_hosts=tuple(str(item) for item in web.get("trusted_hosts", ["humanoid.local"])),
            proxy_count=int(web.get("proxy_count", 1)),
            session_idle_minutes=int(web.get("session_idle_minutes", 60)),
            session_absolute_hours=int(web.get("session_absolute_hours", 12)),
        ),
    )
    if result.controller.command_timeout_seconds <= 0:
        raise ValueError("command_timeout_seconds must be positive.")
    if result.controller.adapter not in {"fake", "sispmctl"}:
        raise ValueError("controller.adapter must be 'fake' or 'sispmctl'.")
    if (
        result.controller.adapter == "sispmctl"
        and not PurePosixPath(result.controller.binary_path).is_absolute()
    ):
        raise ValueError("controller.binary_path must be absolute for sispmctl.")
    if result.queue.maximum_operations < 1:
        raise ValueError("maximum_operations must be positive.")
    if result.polling.stale_after_seconds <= result.polling.outlet_interval_seconds:
        raise ValueError("stale_after_seconds must be greater than outlet_interval_seconds.")
    if result.polling.offline_after_seconds <= result.polling.stale_after_seconds:
        raise ValueError("offline_after_seconds must be greater than stale_after_seconds.")
    if result.web.proxy_count not in {0, 1}:
        raise ValueError("web.proxy_count must be 0 or 1.")
    if result.web.session_idle_minutes < 1 or result.web.session_absolute_hours < 1:
        raise ValueError("Session timeouts must be positive.")
    return result
