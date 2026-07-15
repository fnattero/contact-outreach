from __future__ import annotations

from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache

from apps.catalogs.storage import private_catalog_storage


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    cache.clear()


@pytest.fixture
def owner(db: None) -> User:
    del db
    return User.objects.create_user(username="owner", password="correct-password")


@pytest.fixture
def private_catalog_dir(tmp_path: Path) -> Path:
    private_catalog_storage._location = tmp_path
    private_catalog_storage.__dict__.pop("base_location", None)
    private_catalog_storage.__dict__.pop("location", None)
    return tmp_path
