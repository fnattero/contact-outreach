from __future__ import annotations

import json
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.compliance.models import SuppressionEntry
from apps.compliance.services import suppress_email


def _client(user: User) -> tuple[Client, str]:
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    csrf = str(client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])
    return client, csrf


def _post(client: Client, csrf: str, payload: dict[str, Any]):
    return client.post(
        reverse("api-suppressions"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )


@pytest.mark.django_db
def test_admin_adds_a_manual_suppression_that_is_normalized_and_audited(owner: User) -> None:
    client, csrf = _client(owner)

    created = _post(
        client,
        csrf,
        {
            "email": "Persona@Cliente.EXAMPLE",
            "reason": "MANUAL",
            "evidence": "Pidió no recibir más",
        },
    )

    assert created.status_code == 201, created.content
    data = created.json()["data"]
    assert data["email"] == "persona@cliente.example"
    assert data["reason"] == SuppressionEntry.Reason.MANUAL
    assert data["evidence"] == "Pidió no recibir más"
    entry = SuppressionEntry.objects.get(normalized_email="persona@cliente.example")
    assert entry.created_by == owner
    assert AuditEvent.objects.filter(
        action="suppression.created", entity_id=str(entry.pk), actor=owner
    ).exists()


@pytest.mark.django_db
def test_repeating_an_address_merges_instead_of_duplicating(owner: User) -> None:
    client, csrf = _client(owner)
    payload = {"email": "repetido@cliente.example", "reason": "MANUAL"}

    first = _post(client, csrf, payload)
    second = _post(client, csrf, {**payload, "email": "REPETIDO@cliente.example"})

    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json()["data"]["id"] == first.json()["data"]["id"]
    assert SuppressionEntry.objects.filter(normalized_email="repetido@cliente.example").count() == 1


@pytest.mark.django_db
def test_an_unsubscribe_cannot_be_overridden_through_the_api(owner: User) -> None:
    suppress_email(email="baja@cliente.example", reason="UNSUBSCRIBE", actor=None)
    client, csrf = _client(owner)

    response = _post(client, csrf, {"email": "baja@cliente.example", "reason": "MANUAL"})

    assert response.status_code == 200
    assert response.json()["data"]["reason"] == SuppressionEntry.Reason.UNSUBSCRIBE
    assert SuppressionEntry.objects.get(normalized_email="baja@cliente.example").reason == (
        SuppressionEntry.Reason.UNSUBSCRIBE
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "payload",
    [
        {"email": "no-es-un-correo", "reason": "MANUAL"},
        {"email": "ok@cliente.example", "reason": "NOPE"},
        {"reason": "MANUAL"},
        {"email": "ok@cliente.example"},
    ],
)
def test_invalid_suppressions_are_rejected_without_creating_anything(
    owner: User, payload: dict[str, Any]
) -> None:
    client, csrf = _client(owner)

    response = _post(client, csrf, payload)

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert SuppressionEntry.objects.count() == 0


@pytest.mark.django_db
def test_listing_is_paginated_searchable_and_newest_first(owner: User) -> None:
    for name in ("a", "b", "c"):
        suppress_email(email=f"{name}@lista.example", reason="MANUAL", actor=None)
    suppress_email(email="otro@distinto.example", reason="BOUNCE", actor=None)
    client, _ = _client(owner)
    url = reverse("api-suppressions")

    page = client.get(url, {"page_size": 2})
    assert page.status_code == 200
    assert page.json()["meta"] == {"page": 1, "page_size": 2, "total": 4}
    assert len(page.json()["data"]) == 2
    assert page.json()["data"][0]["email"] == "otro@distinto.example"

    filtered = client.get(url, {"q": "LISTA.example"})
    assert filtered.json()["meta"]["total"] == 3


@pytest.mark.django_db
def test_suppressions_are_admin_only(owner: User) -> None:
    suppress_email(email="privado@cliente.example", reason="MANUAL", actor=None)
    vendor = User.objects.create_user(username="supp-vendor", password="vendor-password-1234")
    client, csrf = _client(vendor)

    assert Client().get(reverse("api-suppressions")).status_code == 401
    listing = client.get(reverse("api-suppressions"))
    assert listing.status_code == 403
    assert "privado@cliente.example" not in listing.content.decode()
    denied = _post(client, csrf, {"email": "nuevo@cliente.example", "reason": "MANUAL"})
    assert denied.status_code == 403
    assert not SuppressionEntry.objects.filter(normalized_email="nuevo@cliente.example").exists()
