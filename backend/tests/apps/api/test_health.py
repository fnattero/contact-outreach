from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
def test_api_degraded_health_requires_an_authenticated_admin(owner: User) -> None:
    url = reverse("api-health-degraded")
    anonymous = Client().get(url)
    assert anonymous.status_code == 401
    assert anonymous.json()["code"] == "authentication_required"

    vendor = User.objects.create_user(username="health-vendor", password="vendor-password-1234")
    vendor_client = Client()
    vendor_client.force_login(vendor)
    denied = vendor_client.get(url)
    assert denied.status_code == 403
    assert denied.json()["code"] == "permission_denied"

    admin = Client()
    admin.force_login(owner)
    allowed = admin.get(url)
    assert allowed.status_code == 200
    assert set(allowed.json()) == {"status", "components", "storage_free_bytes"}
    assert "DATABASE_URL" not in allowed.content.decode()
