from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.automation.models import ReplyAutomationConfiguration


def _csrf(client: Client) -> str:
    return str(client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])


def _patch(client: Client, url: str, payload: dict[str, object], csrf: str):
    return client.patch(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )


@pytest.mark.django_db
def test_configuration_api_exposes_safe_templates_and_persists_prompt_revision(owner: User) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf = _csrf(client)

    templates = client.get(reverse("api-message-template-revisions"))
    assert templates.status_code == 200
    assert {item["kind"] for item in templates.json()["data"]} == {
        "INITIAL",
        "REMINDER",
        "REFERRED_PROPOSAL",
    }

    prompts = client.get(reverse("api-prompts"))
    assert prompts.status_code == 200
    changed = _patch(
        client,
        reverse("api-prompts"),
        {"email_drafting_prompt": "Usá un tono breve y prudente."},
        csrf,
    )
    assert changed.status_code == 200
    assert changed.json()["data"]["email_drafting_prompt"] == "Usá un tono breve y prudente."
    assert changed.json()["data"]["revision"] == prompts.json()["data"]["revision"] + 1


@pytest.mark.django_db
def test_automation_api_defaults_to_shadow_and_live_needs_reauthentication(owner: User) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf = _csrf(client)
    url = reverse("api-automation-configuration")

    current = client.get(url)
    assert current.status_code == 200
    assert current.json()["data"]["mode"] == ReplyAutomationConfiguration.Mode.SHADOW

    enabled = client.post(
        reverse("api-automation-action", args=("enable-live",)),
        data="{}",
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert enabled.status_code == 403
    assert enabled.json()["code"] == "permission_denied"
    assert "ingresar" in enabled.json()["detail"].lower()

    disabled = _patch(client, url, {"mode": "OFF"}, csrf)
    assert disabled.status_code == 200
    assert disabled.json()["data"]["mode"] == "OFF"
