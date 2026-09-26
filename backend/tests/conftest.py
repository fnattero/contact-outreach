from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

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
def private_catalog_dir(tmp_path: Path, settings: Any) -> Iterator[Path]:
    settings.PRIVATE_STORAGE_ROOT = tmp_path
    private_catalog_storage.__dict__.pop("wrapped", None)
    yield tmp_path
    private_catalog_storage.__dict__.pop("wrapped", None)
