from __future__ import annotations

import uuid

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse


def _csrf(client: Client) -> str:
    return str(client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])


@pytest.mark.django_db
@pytest.mark.parametrize("body", ["[1, 2]", '"text"', "7"])
def test_overture_sync_rejects_non_object_json_with_400_not_500(owner: User, body: str) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    response = client.post(
        reverse("api-overture-sync"),
        data=body,
        content_type="application/json",
        HTTP_X_CSRFTOKEN=_csrf(client),
    )

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


@pytest.mark.django_db
def test_job_retry_rejects_non_object_json_with_400_not_500(owner: User) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    response = client.post(
        reverse("api-background-job-retry", kwargs={"job_id": uuid.uuid4()}),
        data="[1, 2]",
        content_type="application/json",
        HTTP_X_CSRFTOKEN=_csrf(client),
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
