from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

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
if APP_ENV == "production":
    if not ALLOWED_HOSTS or any(
        not host
        or host == "*"
        or host.startswith(".")
        or "*" in host
        or "://" in host
        or "/" in host
        for host in ALLOWED_HOSTS
    ):
        raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must contain exact production hosts")

    def exact_https_origin(value: str) -> bool:
        try:
            parsed = urlsplit(value)
            parsed_port = parsed.port
        except ValueError:
            return False
        del parsed_port
        return bool(
            parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
            and "*" not in value
        )

    if not CSRF_TRUSTED_ORIGINS or any(
        not exact_https_origin(origin) for origin in CSRF_TRUSTED_ORIGINS
    ):
        raise ImproperlyConfigured(
            "DJANGO_CSRF_TRUSTED_ORIGINS must contain exact HTTPS production origins"
        )

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "apps.accounts.apps.AccountsConfig",
    "apps.contacts.apps.ContactsConfig",
    "apps.automation.apps.AutomationConfig",
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
    "apps.overture.apps.OvertureConfig",
]

MIDDLEWARE = [
    "apps.core.security.TrustedProxySecurityMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "apps.audit.middleware.RequestObservabilityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.accounts.middleware.MembershipSessionMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "apps.accounts.middleware.MFARequiredMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.security.ApplicationSecurityHeadersMiddleware",
]

ROOT_URLCONF = "contact_outreach.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "builtins": ["apps.dashboard.templatetags.ui_extras"],
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.accounts.permissions.capabilities_context",
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
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
PRIVATE_STORAGE_ROOT = Path(os.getenv("PRIVATE_STORAGE_ROOT", str(BASE_DIR / "private")))
CATALOG_MAX_BYTES = 15 * 1024 * 1024
MIN_FREE_DISK_BYTES = int(os.getenv("MIN_FREE_DISK_BYTES", str(100 * 1024 * 1024)))
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

TRUSTED_PROXY_IPS = env_list("DJANGO_TRUSTED_PROXY_IPS")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
MFA_ENFORCEMENT_ENABLED = True

SESSION_COOKIE_AGE = int(os.getenv("SESSION_COOKIE_AGE", "604800"))
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", APP_ENV == "production")
CSRF_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", APP_ENV == "production")
SESSION_EXPIRE_AT_BROWSER_CLOSE = env_bool("SESSION_EXPIRE_AT_BROWSER_CLOSE", False)
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", APP_ENV == "production")
SECURE_HSTS_SECONDS = int(
    os.getenv("DJANGO_HSTS_SECONDS", "300" if APP_ENV == "production" else "0")
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD", False)
USE_X_FORWARDED_HOST = False
if env_bool("DJANGO_PROXY_HTTPS", False):
    if not TRUSTED_PROXY_IPS:
        raise ImproperlyConfigured("DJANGO_TRUSTED_PROXY_IPS is required for proxy HTTPS")
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_FAILURE_VIEW = "apps.core.views.csrf_failure"

SEND_MODE = os.getenv("SEND_MODE", "dry-run")
SEND_KILL_SWITCH = env_bool("SEND_KILL_SWITCH", True)
AUTO_REPLY_KILL_SWITCH = env_bool("AUTO_REPLY_KILL_SWITCH", True)
RELATIONSHIP_KILL_SWITCH = env_bool("RELATIONSHIP_KILL_SWITCH", True)
AUTOMATIC_REPLY_CONVERSATION_DAILY_LIMIT = int(
    os.getenv("AUTOMATIC_REPLY_CONVERSATION_DAILY_LIMIT", "3")
)
AUTOMATIC_REPLY_WORKSPACE_DAILY_LIMIT = int(
    os.getenv("AUTOMATIC_REPLY_WORKSPACE_DAILY_LIMIT", "20")
)
if SEND_MODE not in {"dry-run", "live"}:
    raise ImproperlyConfigured("SEND_MODE must be dry-run or live")

WEBSITE_FETCHER = os.getenv("WEBSITE_FETCHER", "fake")
CONTACT_EMAIL_MX_RESOLVER = os.getenv("CONTACT_EMAIL_MX_RESOLVER", "dns")
if CONTACT_EMAIL_MX_RESOLVER not in {"dns", "mock"}:
    raise ImproperlyConfigured("CONTACT_EMAIL_MX_RESOLVER must be dns or mock")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "fake")
LLM_MODEL = os.getenv("LLM_MODEL", "fake-deterministic")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OPENAI_COMPATIBLE_BASE_URL = os.getenv("OPENAI_COMPATIBLE_BASE_URL", "")
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "fake")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "1536"))
if EMBEDDING_PROVIDER not in {"fake", "openai-compatible"}:
    raise ImproperlyConfigured("EMBEDDING_PROVIDER must be fake or openai-compatible")
if not 64 <= EMBEDDING_DIMENSIONS <= 3072:
    raise ImproperlyConfigured("EMBEDDING_DIMENSIONS must be between 64 and 3072")
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
    "apps.contacts.tasks",
    "apps.automation.tasks",
    "apps.campaigns.tasks",
    "apps.prospects.tasks",
    "apps.mailbox.tasks",
    "apps.overture.tasks",
)
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_TRACK_STARTED = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_SOFT_TIME_LIMIT = 30
CELERY_TASK_TIME_LIMIT = 45
CELERY_TASK_ROUTES = {
    "overture.*": {"queue": "maintenance"},
}
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "discover-overture-releases": {
        "task": "overture.discover_releases",
        "schedule": 86_400.0,
    },
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
    "sync-gmail-replies": {
        "task": "mailbox.sync_gmail_replies",
        "schedule": 60.0,
    },
    "dispatch-authorized-manual-replies": {
        "task": "mailbox.dispatch_manual_replies",
        "schedule": 60.0,
    },
    "recover-automatic-actions-and-alerts": {
        "task": "automation.recover_actions",
        "schedule": 60.0,
    },
    "dispatch-scheduled-contacts": {
        "task": "automation.dispatch_scheduled_contacts",
        "schedule": 60.0,
    },
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "apps.audit.observability.RedactingJsonFormatter"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "json"},
    },
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
}
