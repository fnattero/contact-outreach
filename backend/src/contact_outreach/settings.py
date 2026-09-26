from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

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


def _database_from_url(database_url: str) -> dict[str, object]:
    """Translate a Railway/Django PostgreSQL URL into Django settings."""
    try:
        parsed = urlsplit(database_url)
        port = parsed.port
    except ValueError as exc:
        raise ImproperlyConfigured("DATABASE_URL has an invalid port") from exc

    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ImproperlyConfigured("DATABASE_URL must use postgres:// or postgresql://")
    if not parsed.hostname or not parsed.path.strip("/"):
        raise ImproperlyConfigured("DATABASE_URL must include a host and database name")
    if parsed.fragment:
        raise ImproperlyConfigured("DATABASE_URL must not contain a fragment")
    if APP_ENV == "production" and (not parsed.username or not parsed.password):
        raise ImproperlyConfigured("DATABASE_URL must include a database user and password")

    database: dict[str, object] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname,
        "PORT": port or 5432,
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }
    options = {
        key: values[-1]
        for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
        if key in {"sslmode", "sslrootcert", "sslcert", "sslkey", "gssencmode"} and values
    }
    if options:
        database["OPTIONS"] = options
    return database


def _database_from_environment() -> dict[str, object]:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        return _database_from_url(database_url)

    host = os.getenv("PGHOST", os.getenv("POSTGRES_HOST", "postgres"))
    name = os.getenv("PGDATABASE", os.getenv("POSTGRES_DB", "contact_outreach"))
    user = os.getenv("PGUSER", os.getenv("POSTGRES_USER", "contact_outreach"))
    password = os.getenv("PGPASSWORD", os.getenv("POSTGRES_PASSWORD", ""))
    port = int(os.getenv("PGPORT", os.getenv("POSTGRES_PORT", "5432")))
    if APP_ENV == "production":
        if not os.getenv("PGHOST") and not os.getenv("POSTGRES_HOST"):
            raise ImproperlyConfigured(
                "DATABASE_URL or an explicit PGHOST/POSTGRES_HOST is required in production"
            )
        if not password:
            raise ImproperlyConfigured(
                "DATABASE_URL or PGPASSWORD/POSTGRES_PASSWORD is required in production"
            )

    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": name,
        "USER": user,
        "PASSWORD": password,
        "HOST": host,
        "PORT": port,
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }


def _validate_redis_url(redis_url: str) -> str:
    if not redis_url:
        raise ImproperlyConfigured("REDIS_URL is required in production")
    parsed = urlsplit(redis_url)
    if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
        raise ImproperlyConfigured("REDIS_URL must use redis:// or rediss:// with a host")
    return redis_url


def _redis_logical_database(redis_url: str, database: int) -> str:
    parsed = urlsplit(redis_url)
    return parsed._replace(path=f"/{database}").geturl()


APP_ENV = os.getenv("APP_ENV", "development")
DEBUG = env_bool("DJANGO_DEBUG", APP_ENV == "development")
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "development-only-change-me")

if APP_ENV == "production":
    if DEBUG:
        raise ImproperlyConfigured("DJANGO_DEBUG must be false in production")
    if SECRET_KEY == "development-only-change-me" or len(SECRET_KEY) < 50:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY must be a long, randomly generated production secret"
        )

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
railway_public_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
if railway_public_domain and railway_public_domain not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(railway_public_domain)
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
    "rest_framework",
    "drf_spectacular",
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
    "apps.api.middleware.InternalProxyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.accounts.middleware.MembershipSessionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
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
database_engine = os.getenv("DATABASE_ENGINE", "postgresql")
if database_engine == "sqlite":
    if APP_ENV == "production":
        raise ImproperlyConfigured("SQLite is not supported in production")
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.getenv("SQLITE_PATH", str(BASE_DIR / "db.sqlite3")),
        }
    }
else:
    DATABASES = {"default": _database_from_environment()}

redis_url_environment = os.getenv("REDIS_URL", "").strip()
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", redis_url_environment).strip()
CACHE_REDIS_URL = os.getenv("CACHE_REDIS_URL", "").strip()
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "").strip()
if not CELERY_BROKER_URL:
    CELERY_BROKER_URL = "redis://redis:6379/0"
if not CACHE_REDIS_URL:
    CACHE_REDIS_URL = _redis_logical_database(CELERY_BROKER_URL, 1)
if not CELERY_RESULT_BACKEND:
    CELERY_RESULT_BACKEND = _redis_logical_database(CELERY_BROKER_URL, 2)
if APP_ENV == "production":
    CELERY_BROKER_URL = _validate_redis_url(CELERY_BROKER_URL)
    CACHE_REDIS_URL = _validate_redis_url(CACHE_REDIS_URL)
    CELERY_RESULT_BACKEND = _validate_redis_url(CELERY_RESULT_BACKEND)
# Compatibility alias for existing diagnostics while callers migrate to purpose-specific URLs.
REDIS_URL = CELERY_BROKER_URL
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": CACHE_REDIS_URL,
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
PRIVATE_STORAGE_BACKEND = os.getenv(
    "PRIVATE_STORAGE_BACKEND", "s3" if APP_ENV == "production" else "filesystem"
)
if PRIVATE_STORAGE_BACKEND not in {"filesystem", "s3"}:
    raise ImproperlyConfigured("PRIVATE_STORAGE_BACKEND must be filesystem or s3")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "").rstrip("/")
S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID", "")
S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY", "")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "")
S3_REGION_NAME = os.getenv("S3_REGION_NAME", "us-east-1")
S3_ADDRESSING_STYLE = os.getenv("S3_ADDRESSING_STYLE", "path")
if S3_ADDRESSING_STYLE not in {"auto", "path", "virtual"}:
    raise ImproperlyConfigured("S3_ADDRESSING_STYLE must be auto, path, or virtual")
if PRIVATE_STORAGE_BACKEND == "s3" and not all(
    (S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, S3_BUCKET_NAME)
):
    raise ImproperlyConfigured(
        "S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, and S3_BUCKET_NAME are required for S3 storage"
    )
CATALOG_MAX_BYTES = 15 * 1024 * 1024
MIN_FREE_DISK_BYTES = int(os.getenv("MIN_FREE_DISK_BYTES", str(100 * 1024 * 1024)))
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

TRUSTED_PROXY_IPS = env_list("DJANGO_TRUSTED_PROXY_IPS")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
INTERNAL_PROXY_TOKEN = os.getenv("INTERNAL_PROXY_TOKEN", "").strip()
PROXY_HTTPS = env_bool("DJANGO_PROXY_HTTPS", False)
TRUST_RAILWAY_PROXY_HEADERS = env_bool("DJANGO_RAILWAY_PROXY", False)
if TRUST_RAILWAY_PROXY_HEADERS and "healthcheck.railway.app" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append("healthcheck.railway.app")
if APP_ENV == "production":
    try:
        public_url = urlsplit(PUBLIC_BASE_URL)
    except ValueError as exc:
        raise ImproperlyConfigured("PUBLIC_BASE_URL is invalid") from exc
    if (
        public_url.scheme != "https"
        or not public_url.hostname
        or public_url.path not in {"", "/"}
        or public_url.username
        or public_url.password
        or public_url.query
        or public_url.fragment
    ):
        raise ImproperlyConfigured("PUBLIC_BASE_URL must be an exact HTTPS origin")
    if public_url.hostname not in {host.split(":", 1)[0] for host in ALLOWED_HOSTS}:
        raise ImproperlyConfigured("PUBLIC_BASE_URL host must be included in DJANGO_ALLOWED_HOSTS")
    if not PROXY_HTTPS:
        raise ImproperlyConfigured("DJANGO_PROXY_HTTPS=true is required in production")
    if not TRUSTED_PROXY_IPS and not TRUST_RAILWAY_PROXY_HEADERS:
        raise ImproperlyConfigured(
            "Configure DJANGO_TRUSTED_PROXY_IPS or explicitly enable DJANGO_RAILWAY_PROXY"
        )
    if len(INTERNAL_PROXY_TOKEN) < 32:
        raise ImproperlyConfigured("INTERNAL_PROXY_TOKEN must be a long random production secret")
SESSION_COOKIE_AGE = int(os.getenv("SESSION_COOKIE_AGE", "43200"))
SESSION_COOKIE_NAME = os.getenv(
    "SESSION_COOKIE_NAME",
    "__Host-contact_outreach_session" if APP_ENV == "production" else "contact_outreach_session",
)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_PATH = "/"
CSRF_USE_SESSIONS = True
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
if APP_ENV == "production" and (
    not SESSION_COOKIE_SECURE
    or not CSRF_COOKIE_SECURE
    or not SECURE_SSL_REDIRECT
    or SECURE_HSTS_SECONDS <= 0
):
    raise ImproperlyConfigured(
        "Production requires secure cookies, HTTPS redirect, and a positive HSTS duration"
    )
USE_X_FORWARDED_HOST = False
if PROXY_HTTPS:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_FAILURE_VIEW = "apps.core.views.csrf_failure"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ("apps.api.authentication.ApiSessionAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_THROTTLE_CLASSES": ("apps.api.throttling.ApiRateThrottle",),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.api.exceptions.api_exception_handler",
    "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
}
API_PUBLIC_THROTTLE_RATE = os.getenv("API_PUBLIC_THROTTLE_RATE", "30/h")
API_READ_THROTTLE_RATE = os.getenv("API_READ_THROTTLE_RATE", "300/5m")
API_MUTATION_THROTTLE_RATE = os.getenv("API_MUTATION_THROTTLE_RATE", "60/5m")
API_SENSITIVE_THROTTLE_RATE = os.getenv("API_SENSITIVE_THROTTLE_RATE", "10/h")
API_EXPORT_THROTTLE_RATE = os.getenv("API_EXPORT_THROTTLE_RATE", "5/h")
SPECTACULAR_SETTINGS = {
    "TITLE": "Contact Outreach API",
    "DESCRIPTION": "REST API for the private outreach dashboard.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

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
if APP_ENV == "production" and len(FIELD_ENCRYPTION_KEY) < 32:
    raise ImproperlyConfigured(
        "FIELD_ENCRYPTION_KEY must be a long, randomly generated production secret"
    )

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
CELERY_RESULT_EXPIRES = 3600
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
