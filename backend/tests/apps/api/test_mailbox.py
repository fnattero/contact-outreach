from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
def test_mailbox_and_outbound_endpoints_require_authentication(owner: User) -> None:
    anonymous = Client()
    for name in ("api-inbound-messages", "api-outbound-messages"):
        response = anonymous.get(reverse(name))
        assert response.status_code == 401
        assert response.json()["code"] == "authentication_required"

    client = Client()
    client.force_login(owner)
    assert client.get(reverse("api-inbound-messages")).status_code == 200
    assert client.get(reverse("api-outbound-messages")).status_code == 200
