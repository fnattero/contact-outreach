from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.automation.retrieval import approved_fact_revisions, current_global_context_revision


def _csrf(client: Client) -> str:
    return str(client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])


def _post(client: Client, url: str, payload: dict[str, object], csrf: str):
    return client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )


@pytest.fixture
def api(owner: User) -> tuple[Client, str]:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    return client, _csrf(client)


@pytest.mark.django_db
def test_a_saved_fact_is_a_draft_until_it_is_approved(owner: User, api: tuple[Client, str]) -> None:
    client, csrf = api
    workspace_id = owner.membership.workspace_id

    created = _post(
        client,
        reverse("api-knowledge-facts"),
        {"title": "Garantía", "category": "Producto", "text": "La garantía es de 12 meses."},
        csrf,
    )
    assert created.status_code == 201
    draft = created.json()["data"]
    assert draft["approved"] is False
    assert draft["state"] == "DRAFT"
    assert approved_fact_revisions(workspace_id) == []

    approved = _post(client, reverse("api-knowledge-fact-approve", args=(draft["id"],)), {}, csrf)
    assert approved.status_code == 200
    assert approved.json()["data"]["approved"] is True
    assert approved.json()["data"]["state"] == "APPROVED"
    assert [item.pk.hex for item in approved_fact_revisions(workspace_id)] == [
        draft["id"].replace("-", "")
    ]
    actions = set(AuditEvent.objects.values_list("action", flat=True))
    assert {"knowledge.revision_created", "knowledge.revision_approved"} <= actions


@pytest.mark.django_db
def test_approving_a_new_fact_version_supersedes_the_previous_one(
    owner: User, api: tuple[Client, str]
) -> None:
    client, csrf = api
    url = reverse("api-knowledge-facts")
    first = _post(client, url, {"title": "Garantía", "text": "12 meses."}, csrf).json()["data"]
    _post(client, reverse("api-knowledge-fact-approve", args=(first["id"],)), {}, csrf)
    second = _post(client, url, {"title": "Garantía", "text": "24 meses."}, csrf).json()["data"]

    # Until the new version is approved, the approved one keeps being used.
    assert [item.text for item in approved_fact_revisions(owner.membership.workspace_id)] == [
        "12 meses."
    ]
    _post(client, reverse("api-knowledge-fact-approve", args=(second["id"],)), {}, csrf)
    assert [item.text for item in approved_fact_revisions(owner.membership.workspace_id)] == [
        "24 meses."
    ]
    states = {
        item["version"]: item["state"]
        for item in client.get(url).json()["data"]
        if item["fact_id"] == first["fact_id"]
    }
    assert states == {1: "SUPERSEDED", 2: "APPROVED"}


@pytest.mark.django_db
def test_saved_context_is_a_draft_until_it_is_approved(
    owner: User, api: tuple[Client, str]
) -> None:
    client, csrf = api
    workspace_id = owner.membership.workspace_id

    created = _post(
        client,
        reverse("api-knowledge-context-revisions"),
        {"context_text": "Somos una empresa de componentes industriales."},
        csrf,
    )
    assert created.status_code == 201
    draft = created.json()["data"]
    assert draft["approved"] is False
    assert draft["state"] == "DRAFT"
    assert current_global_context_revision(workspace_id) is None

    approved = _post(
        client, reverse("api-knowledge-context-approve", args=(draft["id"],)), {}, csrf
    )
    assert approved.status_code == 200
    current = current_global_context_revision(workspace_id)
    assert current is not None
    assert str(current.pk) == draft["id"]


@pytest.mark.django_db
def test_a_seller_cannot_save_or_approve_knowledge(owner: User, api: tuple[Client, str]) -> None:
    client, csrf = api
    draft = _post(
        client, reverse("api-knowledge-facts"), {"title": "Plazo", "text": "5 días."}, csrf
    ).json()["data"]

    seller = User.objects.create_user(username="knowledge-seller", password="seller-password-1")
    seller_client = Client(enforce_csrf_checks=True)
    seller_client.force_login(seller)
    seller_csrf = _csrf(seller_client)

    assert (
        _post(
            seller_client,
            reverse("api-knowledge-facts"),
            {"title": "Otro", "text": "Texto."},
            seller_csrf,
        ).status_code
        == 403
    )
    assert (
        _post(
            seller_client,
            reverse("api-knowledge-fact-approve", args=(draft["id"],)),
            {},
            seller_csrf,
        ).status_code
        == 403
    )
    assert approved_fact_revisions(owner.membership.workspace_id) == []
