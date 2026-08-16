"""Humanoid Control power module."""

from typing import Any


def create_app(*args: Any, **kwargs: Any) -> Any:
    """Load the Flask application factory without importing Flask for domain-only tools."""

    from humanoid_power.app import create_app as app_factory

    return app_factory(*args, **kwargs)


__all__ = ["create_app"]
