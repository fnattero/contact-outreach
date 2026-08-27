from __future__ import annotations

from contact_outreach.settings import *

DEBUG = False
SECRET_KEY = "test-secret-key-not-used-outside-tests"
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

# Tests must remain incapable of selecting live delivery or network-backed providers,
# regardless of the environment that invoked pytest.
SEND_MODE = "dry-run"
SEND_KILL_SWITCH = True
AUTO_REPLY_KILL_SWITCH = True
RELATIONSHIP_KILL_SWITCH = True
WEBSITE_FETCHER = "fake"
CONTACT_EMAIL_MX_RESOLVER = "mock"
LLM_PROVIDER = "fake"
EMBEDDING_PROVIDER = "fake"
EMBEDDING_MODEL = "fake-embedding"
EMBEDDING_DIMENSIONS = 128
GMAIL_PROVIDER = "fake"
GMAIL_FAKE_ACCOUNT_EMAIL = "owner@example.invalid"
FIELD_ENCRYPTION_KEY = "test-only-field-encryption-key"
MFA_ENFORCEMENT_ENABLED = False
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0
STORAGES = {
    **STORAGES,
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
MIDDLEWARE = [
    middleware
    for middleware in MIDDLEWARE
    if middleware != "whitenoise.middleware.WhiteNoiseMiddleware"
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

PRIVATE_STORAGE_ROOT = BASE_DIR / ".test-private"
PRIVATE_STORAGE_BACKEND = "filesystem"

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "contact-outreach-tests",
    }
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.Argon2PasswordHasher"]

CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
