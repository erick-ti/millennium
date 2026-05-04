"""Regression tests for cross-environment settings safety."""

import inspect

from config.settings import base


def test_base_settings_does_not_load_dotenv() -> None:
    """base.py must NOT load .env so prod settings fail closed even when a
    dev .env file exists at the repo root. dotenv loading belongs in dev.py.

    Without this guarantee, a developer accidentally running
    `DJANGO_SETTINGS_MODULE=config.settings.prod` locally would silently
    inherit dev values from .env and bypass prod's no-default env validation.
    """
    source = inspect.getsource(base)
    assert "read_env" not in source, (
        "base.py loads a dotenv file. Move dotenv loading to dev.py only — "
        "prod fails open against a dev .env otherwise."
    )
