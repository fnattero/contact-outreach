from __future__ import annotations

import pytest
from django.test import Client

from apps.catalogs.storage import private_catalog_storage


def test_schema_and_docs_are_not_served_outside_debug() -> None:
    client = Client()
    assert client.get("/api/schema/").status_code == 404
    assert client.get("/api/docs/").status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path",
    ["/api/v1/auth/csrf/", "/api/v1/auth/session/", "/api/v1/health/live/", "/api/v1/nope/"],
)
def test_every_api_response_is_marked_private_and_uncacheable(path: str) -> None:
    response = Client().get(path)

    cache_control = response["Cache-Control"]
    assert "no-store" in cache_control
    assert "private" in cache_control


def test_private_storage_never_hands_out_a_url() -> None:
    # Catalogs stream through the authenticated API; no presigned or public link may exist.
    with pytest.raises(NotImplementedError):
        private_catalog_storage.url("catalogs/secret.pdf")
