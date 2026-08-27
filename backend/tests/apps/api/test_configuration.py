from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.configuration.models import SearchCategory, SearchZone


@pytest.mark.django_db
def test_configuration_reference_data_is_admin_only_and_workspace_scoped(owner: User) -> None:
    anonymous = Client().get(reverse("api-search-categories"))
    assert anonymous.status_code == 401

    vendor = User.objects.create_user(
        username="configuration-vendor", password="vendor-password-1234"
    )
    vendor_client = Client()
    vendor_client.force_login(vendor)
    assert vendor_client.get(reverse("api-search-categories")).status_code == 403

    admin = Client()
    admin.force_login(owner)
    categories = admin.get(reverse("api-search-categories"))
    assert categories.status_code == 200
    assert categories.json()["data"]
    first_category = SearchCategory.objects.order_by("name").first()
    assert first_category is not None
    category_detail = admin.get(reverse("api-search-category-rules", args=(first_category.pk,)))
    assert category_detail.status_code == 200
    assert category_detail.json()["data"]["id"] == str(first_category.pk)

    zones = admin.get(reverse("api-search-zones"), {"level": SearchZone.Level.PROVINCE})
    assert zones.status_code == 200
    assert all(item["level"] == SearchZone.Level.PROVINCE for item in zones.json()["data"])
