from pathlib import Path

import pytest

from humanoid_power.app import create_app
from humanoid_power.config import load_config


def test_unknown_adapter_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "invalid.toml"
    path.write_text(
        '[controller]\nadapter = "typo"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="controller.adapter"):
        load_config(path)


def test_real_adapter_is_blocked_before_hardware_acceptance(
    tmp_path: Path,
) -> None:
    secret = tmp_path / "secret.key"
    secret.write_text("a" * 64, encoding="utf-8")

    database_path = (tmp_path / "power.db").as_posix()
    secret_path = secret.as_posix()
    config = tmp_path / "config.toml"

    config.write_text(
        "\n".join(
            [
                "[application]",
                f'database_path = "{database_path}"',
                f'secret_key_path = "{secret_path}"',
                "runtime_enabled = false",
                "[controller]",
                'adapter = "sispmctl"',
                "hardware_accepted = false",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Real hardware is blocked"):
        create_app(config, start_runtime=False)