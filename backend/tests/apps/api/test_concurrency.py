from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse


def _csrf(client: Client) -> str:
    return str(client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])


@pytest.mark.django_db
def test_profile_mutation_requires_current_etag_and_rejects_stale_writes(owner) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf = _csrf(client)
    url = reverse("api-workspace-profile")

    created = client.patch(
        url,
        data=json.dumps(
            {
                "company_name": "Nueva empresa",
                "salesperson_name": "Ana",
                "address": "Calle 1",
                "signature": "Firma",
            }
        ),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert created.status_code == 200
    current = client.get(url)
    etag = current["ETag"]

    missing = client.patch(
        url,
        data=json.dumps({"company_name": "Sin versión"}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert missing.status_code == 428
    assert missing.json()["code"] == "precondition_required"

    updated = client.patch(
        url,
        data=json.dumps({"company_name": "Primera versión"}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
        HTTP_IF_MATCH=etag,
    )
    assert updated.status_code == 200

    stale = client.patch(
        url,
        data=json.dumps({"company_name": "Versión vieja"}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
        HTTP_IF_MATCH=etag,
    )
    assert stale.status_code == 412
    assert stale.json()["code"] == "precondition_failed"
