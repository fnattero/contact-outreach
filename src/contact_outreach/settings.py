from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parents[2]


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ImproperlyConfigured(f"{name} must be a boolean value")


def env_list(name: str, default: str = "") -> list[str]:
    return [part.strip() for part in os.getenv(name, default).split(",") if part.strip()]


APP_ENV = os.getenv("APP_ENV", "development")
DEBUG = env_bool("DJANGO_DEBUG", APP_ENV == "development")
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "development-only-change-me")

if APP_ENV == "production" and SECRET_KEY == "development-only-change-me":
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set outside development")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.accounts.apps.AccountsConfig",
    "apps.audit.apps.AuditConfig",
    "apps.configuration.apps.ConfigurationConfig",
    "apps.catalogs.apps.CatalogsConfig",
    "apps.campaigns.apps.CampaignsConfig",
    "apps.prospects.apps.ProspectsConfig",
    "apps.compliance.apps.ComplianceConfig",
    "apps.dashboard.apps.DashboardConfig",
    "apps.health.apps.HealthConfig",
    "apps.integrations.apps.IntegrationsConfig",
    "apps.mailbox.apps.MailboxConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "contact_outreach.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.dashboard.context_processors.runtime_safety",
            ],
        },
    }
]

WSGI_APPLICATION = "contact_outreach.wsgi.application"
ASGI_APPLICATION = "contact_outreach.asgi.application"

DATABASES: dict[str, dict[str, object]]
if os.getenv("DATABASE_ENGINE", "postgresql") == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.getenv("SQLITE_PATH", str(BASE_DIR / "db.sqlite3")),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "contact_outreach"),
            "USER": os.getenv("POSTGRES_USER", "contact_outreach"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", ""),
            "HOST": os.getenv("POSTGRES_HOST", "postgres"),
            "PORT": int(os.getenv("POSTGRES_PORT", "5432")),
            "CONN_MAX_AGE": 60,
            "CONN_HEALTH_CHECKS": True,
        }
    }

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "contact-outreach",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

LANGUAGE_CODE = "es-ar"
TIME_ZONE = "America/Argentina/Buenos_Aires"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
PRIVATE_STORAGE_ROOT = Path(os.getenv("PRIVATE_STORAGE_ROOT", str(BASE_DIR / "private")))
CATALOG_MAX_BYTES = 15 * 1024 * 1024
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

SESSION_COOKIE_AGE = int(os.getenv("SESSION_COOKIE_AGE", "28800"))
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", False)
CSRF_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", False)
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

SEND_MODE = os.getenv("SEND_MODE", "dry-run")
SEND_KILL_SWITCH = env_bool("SEND_KILL_SWITCH", True)
if SEND_MODE not in {"dry-run", "live"}:
    raise ImproperlyConfigured("SEND_MODE must be dry-run or live")

EXTRACTOR_PROVIDER = os.getenv("EXTRACTOR_PROVIDER", "fake")
OUTSCRAPER_API_KEY = os.getenv("OUTSCRAPER_API_KEY", "")
OUTSCRAPER_BASE_URL = os.getenv("OUTSCRAPER_BASE_URL", "https://api.outscraper.cloud")
try:
    OUTSCRAPER_MAX_COST_PER_RESULT = Decimal(
        os.getenv("OUTSCRAPER_MAX_COST_PER_RESULT", "0.010000")
    )
except InvalidOperation as exc:
    raise ImproperlyConfigured("OUTSCRAPER_MAX_COST_PER_RESULT must be a decimal") from exc
if OUTSCRAPER_MAX_COST_PER_RESULT < 0:
    raise ImproperlyConfigured("OUTSCRAPER_MAX_COST_PER_RESULT must not be negative")
OUTSCRAPER_BATCH_SIZE = int(os.getenv("OUTSCRAPER_BATCH_SIZE", "20"))
OUTSCRAPER_POLL_SECONDS = int(os.getenv("OUTSCRAPER_POLL_SECONDS", "30"))
if OUTSCRAPER_BATCH_SIZE <= 0 or OUTSCRAPER_POLL_SECONDS <= 0:
    raise ImproperlyConfigured("Outscraper batch and polling values must be positive")
WEBSITE_FETCHER = os.getenv("WEBSITE_FETCHER", "fake")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "fake")
LLM_MODEL = os.getenv("LLM_MODEL", "fake-deterministic")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OPENAI_COMPATIBLE_BASE_URL = os.getenv("OPENAI_COMPATIBLE_BASE_URL", "")
GMAIL_PROVIDER = os.getenv("GMAIL_PROVIDER", "fake")
GMAIL_OAUTH_CLIENT_ID = os.getenv("GMAIL_OAUTH_CLIENT_ID", "")
GMAIL_OAUTH_CLIENT_SECRET = os.getenv("GMAIL_OAUTH_CLIENT_SECRET", "")
GMAIL_OAUTH_REDIRECT_URI = os.getenv("GMAIL_OAUTH_REDIRECT_URI", "")
GMAIL_FAKE_ACCOUNT_EMAIL = os.getenv("GMAIL_FAKE_ACCOUNT_EMAIL", "owner@example.invalid")
FIELD_ENCRYPTION_KEY = os.getenv("FIELD_ENCRYPTION_KEY", "")

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_IMPORTS = (
    "contact_outreach.tasks",
    "apps.campaigns.tasks",
    "apps.prospects.tasks",
    "apps.mailbox.tasks",
)
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_TRACK_STARTED = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_SOFT_TIME_LIMIT = 30
CELERY_TASK_TIME_LIMIT = 45
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "recover-extraction-runs": {
        "task": "campaigns.recover_extraction_runs",
        "schedule": 60.0,
    },
    "recover-prospect-pipeline": {
        "task": "prospects.recover_pipeline",
        "schedule": 60.0,
    },
    "deliver-outbound-messages": {
        "task": "mailbox.deliver_outbound_messages",
        "schedule": 60.0,
    },
    "recover-ambiguous-gmail-sends": {
        "task": "mailbox.recover_ambiguous_sends",
        "schedule": 60.0,
    },
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
}
