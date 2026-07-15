from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.integrations.contracts import SearchRequest
from apps.integrations.factory import get_extractor_provider
from contact_outreach.tasks import healthcheck


@pytest.mark.e2e
@pytest.mark.django_db
def test_owner_dashboard_fake_provider_and_worker_flow(client: Client) -> None:
    User.objects.create_user(username="owner", password="correct-password")
    assert client.login(username="owner", password="correct-password")
    assert client.get(reverse("dashboard")).status_code == 200

    batch = get_extractor_provider().extract(
        SearchRequest(query="demo", correlation_id="e2e", idempotency_key="e2e")
    )
    assert batch.records
    assert healthcheck.apply().get()["status"] == "ok"
