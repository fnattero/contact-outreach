from __future__ import annotations

import json
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.configuration.integrations import runtime_integration_configuration

SECRET = "sk-test-credential-that-must-never-be-echoed"


class _Api:
    def __init__(self, user: User) -> None:
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(user)
        self.csrf = str(self.client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])
        self.url = reverse("api-integrations-configuration")

    def post(self, url: str, payload: dict[str, Any]):
        return self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=self.csrf,
        )

    def reauthenticate(self, password: str = "correct-password") -> None:
        response = self.post(reverse("api-auth-reauthenticate"), {"password": password})
        assert response.status_code == 200, response.content

    def patch(self, payload: dict[str, Any]):
        return self.client.patch(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=self.csrf,
        )


@pytest.mark.django_db
def test_configuration_is_admin_only_and_never_exposes_credentials(owner: User) -> None:
    url = reverse("api-integrations-configuration")
    vendor = User.objects.create_user(
        username="integration-vendor", password="vendor-password-1234"
    )

    assert Client().get(url).status_code == 401
    assert _Api(vendor).client.get(url).status_code == 403

    response = _Api(owner).client.get(url)
    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data["llm_credential"]) == {"configured", "source"}
    assert set(data["gmail_credential"]) == {"configured", "source"}
    assert not any("secret" in key or key.endswith("_api_key") for key in data)


@pytest.mark.django_db
def test_changing_integrations_requires_a_recent_password_confirmation(owner: User) -> None:
    api = _Api(owner)
    before = runtime_integration_configuration(owner.pk).llm_model

    refused = api.patch({"llm_model": "modelo-nuevo"})

    assert refused.status_code == 403
    assert "contraseña" in refused.json()["detail"]
    assert runtime_integration_configuration(owner.pk).llm_model == before


@pytest.mark.django_db
def test_vendedor_cannot_change_integrations_even_after_confirming_a_password(
    owner: User,
) -> None:
    vendor = User.objects.create_user(username="integration-seller", password="seller-password-1")
    api = _Api(vendor)
    api.reauthenticate("seller-password-1")

    assert api.patch({"llm_model": "x"}).status_code == 403


@pytest.mark.django_db
def test_admin_saves_settings_after_reauthentication_and_it_is_audited(owner: User) -> None:
    api = _Api(owner)
    api.reauthenticate()
    revision = runtime_integration_configuration(owner.pk).revision

    response = api.patch({"llm_model": "modelo-nuevo", "overture_min_confidence": "0.800"})

    assert response.status_code == 200, response.content
    data = response.json()["data"]
    assert data["llm_model"] == "modelo-nuevo"
    assert data["overture_min_confidence"] == "0.800"
    assert data["revision"] == revision + 1
    assert AuditEvent.objects.filter(action="integration_configuration.saved", actor=owner).exists()


@pytest.mark.django_db
def test_omitted_fields_are_left_exactly_as_they_were(owner: User) -> None:
    api = _Api(owner)
    api.reauthenticate()
    before = _Api(owner).client.get(api.url).json()["data"]

    api.patch({"llm_model": "solo-este-campo"})

    after = api.client.get(api.url).json()["data"]
    changed = {key for key in after if after[key] != before[key]}
    assert changed == {"llm_model", "revision"}


@pytest.mark.django_db
def test_credentials_are_write_only_encrypted_and_absent_from_every_record(owner: User) -> None:
    api = _Api(owner)
    api.reauthenticate()

    saved = api.patch(
        {
            "llm_provider": "openai-compatible",
            "openai_compatible_base_url": "https://api.example.invalid/v1",
            "llm_api_key": SECRET,
        }
    )

    assert saved.status_code == 200, saved.content
    assert SECRET not in saved.content.decode()
    assert saved.json()["data"]["llm_credential"] == {"configured": True, "source": "ENCRYPTED"}
    assert SECRET not in api.client.get(api.url).content.decode()
    audit = " ".join(
        f"{event.before} {event.after}"
        for event in AuditEvent.objects.filter(action="integration_configuration.saved")
    )
    assert SECRET not in audit

    removed = api.patch({"remove_llm_api_key": True, "llm_provider": "fake"})
    assert removed.status_code == 200, removed.content
    assert removed.json()["data"]["llm_credential"]["configured"] is False


@pytest.mark.django_db
def test_replacing_and_removing_the_same_secret_is_rejected(owner: User) -> None:
    api = _Api(owner)
    api.reauthenticate()

    response = api.patch({"llm_api_key": SECRET, "remove_llm_api_key": True})

    assert response.status_code == 400
    assert SECRET not in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest",
        "http://metadata.google.internal/",
        "https://user:pass@api.example.invalid/v1",
        "ftp://api.example.invalid",
        "http://public.example.invalid/v1",
    ],
)
def test_unsafe_base_urls_are_rejected_and_nothing_is_saved(owner: User, url: str) -> None:
    api = _Api(owner)
    api.reauthenticate()
    before = runtime_integration_configuration(owner.pk)

    response = api.patch({"openai_compatible_base_url": url, "llm_provider": "openai-compatible"})

    assert response.status_code == 400
    after = runtime_integration_configuration(owner.pk)
    assert after.openai_compatible_base_url == before.openai_compatible_base_url
    assert after.revision == before.revision


@pytest.mark.django_db
@pytest.mark.parametrize(
    "payload",
    [
        {"llm_provider": "not-a-provider"},
        {"overture_min_confidence": "1.5"},
        {"embedding_dimensions": 8},
        {"llm_model": "x" * 200},
    ],
)
def test_out_of_range_values_are_field_errors(owner: User, payload: dict[str, Any]) -> None:
    api = _Api(owner)
    api.reauthenticate()

    response = api.patch(payload)

    assert response.status_code == 400
    assert set(response.json()["field_errors"]) == set(payload)
