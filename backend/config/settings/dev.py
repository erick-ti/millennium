import environ

from .base import *  # noqa: F403
from .base import BASE_DIR, env

# Local .env is loaded ONLY in dev. Prod/test never see it.
environ.Env.read_env(BASE_DIR.parent / ".env")

DEBUG = True
ALLOWED_HOSTS = ["*"]

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-not-secret-do-not-use-in-prod")

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://postgres:postgres@localhost:5432/millennium",
    ),
}

CACHES = {
    "default": env.cache("REDIS_URL", default="redis://localhost:6379/0"),
}

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/1")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/2")

# WhiteNoise: serve from app static dirs in dev so collectstatic isn't required.
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = True
