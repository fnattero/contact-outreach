from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

ABSENT = uuid.UUID("00000000-0000-4000-8000-000000000001")

# (method, url name, url args). Every view here declares its capability at the API boundary, so a
# caller without it is turned away before the view body, the serializer or the service runs.
ADMIN_ONLY: list[tuple[str, str, tuple[Any, ...]]] = [
    ("get", "api-automation-writing-instructions", ()),
    ("patch", "api-automation-writing-instructions", ()),
    ("post", "api-automation-action", ("disable-live",)),
    ("post", "api-campaign-action", (ABSENT, "pause")),
    ("post", "api-human-task-action", (ABSENT, "resolve")),
    ("get", "api-overture-status", ()),
    ("post", "api-overture-sync", ()),
]


def _client(user: User) -> tuple[Client, str]:
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    csrf = str(client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])
    return client, csrf


def _call(client: Client, csrf: str, method: str, url: str):
    headers = {"HTTP_X_CSRFTOKEN": csrf, "HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}
    if method == "get":
        return client.get(url, **headers)
    return getattr(client, method)(url, data="{}", content_type="application/json", **headers)


@pytest.mark.django_db
@pytest.mark.parametrize(("method", "name", "args"), ADMIN_ONLY)
def test_administrative_endpoints_reject_anonymous_and_vendedor(
    owner: User, method: str, name: str, args: tuple[Any, ...]
) -> None:
    url = reverse(name, args=args)
    anonymous = Client()
    assert _call(anonymous, "", method, url).status_code in {401, 403}

    vendor = User.objects.create_user(username="matrix-vendor", password="vendor-password-1234")
    client, csrf = _client(vendor)
    denied = _call(client, csrf, method, url)

    assert denied.status_code == 403, f"{method.upper()} {name}"
    assert denied.json()["code"] == "permission_denied"


@pytest.mark.django_db
def test_writing_instructions_are_readable_by_admin_only(owner: User) -> None:
    url = reverse("api-automation-writing-instructions")
    vendor = User.objects.create_user(username="prompt-vendor", password="vendor-password-1234")
    vendor_client, _ = _client(vendor)
    admin_client, _ = _client(owner)

    assert Client().get(url).status_code == 401
    leaked = vendor_client.get(url)
    assert leaked.status_code == 403
    assert "automatic_reply_prompt" not in leaked.content.decode()
    allowed = admin_client.get(url)
    assert allowed.status_code == 200
    assert allowed.json()["data"]["automatic_reply_prompt"]


@pytest.mark.django_db
def test_attention_queue_stays_readable_by_vendedor_but_not_anonymous(owner: User) -> None:
    url = reverse("api-attention")
    vendor = User.objects.create_user(username="attention-vendor", password="vendor-password-1234")
    vendor_client, _ = _client(vendor)

    assert Client().get(url).status_code == 401
    assert vendor_client.get(url).status_code == 200
