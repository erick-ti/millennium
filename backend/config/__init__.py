import os

if "DJANGO_SETTINGS_MODULE" not in os.environ:
    raise RuntimeError(
        "DJANGO_SETTINGS_MODULE must be set explicitly "
        "(e.g. config.settings.dev, config.settings.prod). "
        "Server entrypoints fail closed by design — manage.py is the only "
        "developer-facing tool that defaults DJANGO_SETTINGS_MODULE."
    )

from .celery import app as celery_app

__all__ = ("celery_app",)
