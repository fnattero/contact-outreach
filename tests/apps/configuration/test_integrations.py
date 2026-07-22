from __future__ import annotations

import json
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import Client, override_settings
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.configuration.forms import IntegrationConfigurationForm
from apps.configuration.integrations import (
    GMAIL_CLIENT_SECRET_PURPOSE,
    LLM_KEY_PURPOSE,
    OUTSCRAPER_KEY_PURPOSE,
    get_gmail_oauth_client_secret,
    get_llm_api_key,
    get_outscraper_api_key,
    redact_provider_error,
    runtime_integration_configuration,
    save_integration_configuration,
    validate_encrypted_integration_credentials,
)
from apps.configuration.models import IntegrationConfiguration
from apps.core.crypto import decrypt_secret, encrypt_secret
from apps.integrations.factory import (
    get_extractor_provider,
    get_gmail_provider,
    get_llm_provider,
)
from apps.integrations.gmail import GmailAPIProvider
from apps.integrations.llm import OpenAICompatibleProvider
from apps.integrations.outscraper import OutscraperProvider
from apps.mailbox.crypto import encrypt_token
from apps.mailbox.models import GmailConnection


def integration_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "extractor_provider": "fake",
        "outscraper_api_key": "",
        "remove_outscraper_api_key": False,
        "outscraper_base_url": "https://api.outscraper.cloud",
        "outscraper_max_cost_per_result": Decimal("0.010000"),
        "outscraper_batch_size": 20,
        "outscraper_poll_seconds": 30,
        "llm_provider": "fake",
        "llm_model": "fake-deterministic",
        "ollama_base_url": "http://127.0.0.1:11434",
        "openai_compatible_base_url": "",
        "llm_api_key": "",
        "remove_llm_api_key": False,
        "gmail_provider": "fake",
        "gmail_oauth_client_id": "",
        "gmail_oauth_client_secret": "",
        "remove_gmail_oauth_client_secret": False,
        "current_password": "correct-password",
    }
    values.update(overrides)
    return values


def test_secret_ciphertext_is_randomized_and_bound_to_its_purpose() -> None:
    first = encrypt_secret("provider-secret", purpose=LLM_KEY_PURPOSE)
    second = encrypt_secret("provider-secret", purpose=LLM_KEY_PURPOSE)

    assert first != second
    assert "provider-secret" not in first
    assert decrypt_secret(first, purpose=LLM_KEY_PURPOSE) == "provider-secret"
    with pytest.raises(ValidationError, match="descifrar"):
        decrypt_secret(first, purpose=OUTSCRAPER_KEY_PURPOSE)


@pytest.mark.django_db
def test_integration_page_requires_login_password_and_csrf(owner: User) -> None:
    assert Client().get(reverse("integrations")).status_code == 302

    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(owner)
    assert csrf_client.post(reverse("integrations"), integration_values()).status_code == 403

    client = Client()
    client.force_login(owner)
    response = client.post(
        reverse("integrations"),
        integration_values(
            current_password="wrong-password",
            llm_api_key="must-not-return-in-html",
        ),
    )
    assert response.status_code == 200
    assert b"contrase\xc3\xb1a actual no es correcta" in response.content.lower()
    assert b"must-not-return-in-html" not in response.content
    assert not IntegrationConfiguration.objects.exists()


@pytest.mark.django_db
def test_integration_password_reauthentication_is_throttled(client: Client, owner: User) -> None:
    cache.clear()
    client.force_login(owner)
    for _ in range(5):
        response = client.post(
            reverse("integrations"),
            integration_values(current_password="wrong-password"),
            REMOTE_ADDR="127.0.0.77",
        )
        assert response.status_code == 200

    blocked = client.post(
        reverse("integrations"),
        integration_values(current_password="correct-password"),
        REMOTE_ADDR="127.0.0.77",
    )

    assert blocked.status_code == 429
    assert b"Demasiados intentos" in blocked.content
    assert not IntegrationConfiguration.objects.exists()


@pytest.mark.django_db
def test_debug_error_report_redacts_submitted_credentials(
    client: Client, owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_unexpectedly(**kwargs: object) -> None:
        del kwargs
        raise RuntimeError("unexpected test failure")

    monkeypatch.setattr(
        "apps.configuration.views.save_integration_configuration",
        fail_unexpectedly,
    )
    client.force_login(owner)
    client.raise_request_exception = False
    submitted = integration_values(
        outscraper_api_key="debug-outscraper-credential",
        llm_api_key="debug-llm-credential",
        gmail_oauth_client_secret="debug-google-credential",
    )

    with override_settings(DEBUG=True):
        response = client.post(reverse("integrations"), submitted)

    assert response.status_code == 500
    for value in submitted.values():
        if isinstance(value, str) and value.startswith(("debug-", "correct-")):
            assert value.encode() not in response.content


@pytest.mark.django_db
def test_dashboard_refuses_secret_storage_without_external_root_key(
    client: Client, owner: User
) -> None:
    client.force_login(owner)
    with override_settings(FIELD_ENCRYPTION_KEY=""):
        response = client.post(
            reverse("integrations"),
            integration_values(llm_api_key="must-not-leak-with-missing-root"),
        )

    assert response.status_code == 200
    assert b"FIELD_ENCRYPTION_KEY" in response.content
    assert b"must-not-leak-with-missing-root" not in response.content
    assert not IntegrationConfiguration.objects.exists()


@pytest.mark.django_db
def test_dashboard_saves_write_only_purpose_bound_credentials(client: Client, owner: User) -> None:
    client.force_login(owner)
    response = client.post(
        reverse("integrations"),
        integration_values(
            extractor_provider="outscraper",
            outscraper_api_key="outscraper-dashboard-secret",
            outscraper_batch_size=40,
            llm_provider="openai-compatible",
            llm_model="provider-model",
            openai_compatible_base_url="https://llm.example.test/v1",
            llm_api_key="llm-dashboard-secret",
            gmail_provider="api",
            gmail_oauth_client_id="client-id.apps.googleusercontent.com",
            gmail_oauth_client_secret="google-dashboard-secret",
        ),
    )

    assert response.status_code == 302
    configuration = IntegrationConfiguration.objects.get(owner=owner)
    serialized_model = " ".join(
        (
            configuration.outscraper_api_key_encrypted,
            configuration.llm_api_key_encrypted,
            configuration.gmail_oauth_client_secret_encrypted,
        )
    )
    for secret in (
        "outscraper-dashboard-secret",
        "llm-dashboard-secret",
        "google-dashboard-secret",
    ):
        assert secret not in serialized_model
    assert (
        decrypt_secret(configuration.outscraper_api_key_encrypted, purpose=OUTSCRAPER_KEY_PURPOSE)
        == "outscraper-dashboard-secret"
    )
    assert (
        decrypt_secret(configuration.llm_api_key_encrypted, purpose=LLM_KEY_PURPOSE)
        == "llm-dashboard-secret"
    )
    assert (
        decrypt_secret(
            configuration.gmail_oauth_client_secret_encrypted,
            purpose=GMAIL_CLIENT_SECRET_PURPOSE,
        )
        == "google-dashboard-secret"
    )

    runtime = runtime_integration_configuration(owner.pk)
    assert runtime.extractor_provider == "outscraper"
    assert runtime.llm_provider == "openai-compatible"
    assert runtime.gmail_provider == "api"
    assert runtime.outscraper_batch_size == 40

    extractor = get_extractor_provider("outscraper", owner_id=owner.pk)
    llm = get_llm_provider(
        "openai-compatible",
        base_url="https://llm.example.test/v1",
        model="provider-model",
        owner_id=owner.pk,
    )
    gmail = get_gmail_provider(owner_id=owner.pk)
    assert isinstance(extractor, OutscraperProvider)
    assert extractor._api_key == "outscraper-dashboard-secret"
    assert isinstance(llm, OpenAICompatibleProvider)
    assert llm.api_key == "llm-dashboard-secret"
    assert isinstance(gmail, GmailAPIProvider)
    assert gmail.client_secret == "google-dashboard-secret"

    audit = AuditEvent.objects.get(action="integration_configuration.saved")
    audit_json = json.dumps({"before": audit.before, "after": audit.after})
    assert "dashboard-secret" not in audit_json
    assert configuration.outscraper_api_key_encrypted not in audit_json

    page = client.get(reverse("integrations"))
    assert page.headers["Cache-Control"] == (
        "max-age=0, no-cache, no-store, must-revalidate, private"
    )
    assert b"dashboard-secret" not in page.content
    assert b'value="llm-dashboard-secret"' not in page.content


@pytest.mark.django_db
def test_blank_secret_keeps_it_and_explicit_removal_disables_environment_fallback(
    owner: User,
) -> None:
    save_integration_configuration(
        owner=owner,
        values=integration_values(llm_api_key="first-secret"),
    )
    original = IntegrationConfiguration.objects.get(owner=owner).llm_api_key_encrypted

    save_integration_configuration(owner=owner, values=integration_values(llm_model="new-model"))
    configuration = IntegrationConfiguration.objects.get(owner=owner)
    assert configuration.llm_api_key_encrypted == original
    assert get_llm_api_key(owner.pk) == "first-secret"

    with override_settings(LLM_API_KEY="environment-secret"):
        save_integration_configuration(
            owner=owner,
            values=integration_values(remove_llm_api_key=True),
        )
        configuration.refresh_from_db()
        assert configuration.llm_api_key_source == IntegrationConfiguration.SecretSource.NONE
        assert configuration.llm_api_key_encrypted == ""
        assert get_llm_api_key(owner.pk) == ""


@pytest.mark.django_db
def test_connected_gmail_credentials_cannot_be_replaced(owner: User) -> None:
    GmailConnection.objects.create(
        owner=owner,
        email="owner@example.invalid",
        refresh_token_encrypted=encrypt_token("refresh-token"),
        status=GmailConnection.Status.CONNECTED,
    )

    with pytest.raises(ValidationError, match="Desconectá Gmail"):
        save_integration_configuration(
            owner=owner,
            values=integration_values(
                gmail_provider="api",
                gmail_oauth_client_id="new-client-id",
                gmail_oauth_client_secret="new-client-secret",
            ),
        )
    assert not IntegrationConfiguration.objects.exists()


@pytest.mark.django_db
def test_google_sign_in_uses_dashboard_oauth_configuration(client: Client, owner: User) -> None:
    save_integration_configuration(
        owner=owner,
        values=integration_values(
            gmail_provider="api",
            gmail_oauth_client_id="dashboard-client-id.apps.googleusercontent.com",
            gmail_oauth_client_secret="dashboard-google-secret",
        ),
    )
    client.force_login(owner)

    response = client.post(reverse("gmail-connect"))

    assert response.status_code == 302
    target = urlsplit(response["Location"])
    assert target.netloc == "accounts.google.com"
    query = parse_qs(target.query)
    assert query["client_id"] == ["dashboard-client-id.apps.googleusercontent.com"]
    assert query["redirect_uri"] == ["http://testserver/gmail/oauth/callback/"]
    assert "dashboard-google-secret" not in response["Location"]


@pytest.mark.django_db
def test_integration_form_rejects_secret_bearing_and_insecure_remote_urls(owner: User) -> None:
    runtime = runtime_integration_configuration(owner.pk)
    form = IntegrationConfigurationForm(
        integration_values(
            outscraper_base_url="https://attacker.example/collect",
            openai_compatible_base_url="http://attacker.example/v1?key=leak",
        ),
        user=owner,
        runtime=runtime,
    )

    assert not form.is_valid()
    assert "outscraper_base_url" in form.errors
    assert "openai_compatible_base_url" in form.errors


@pytest.mark.django_db
def test_integration_service_rejects_unsafe_urls_before_persisting(owner: User) -> None:
    with pytest.raises(ValidationError, match="credenciales"):
        save_integration_configuration(
            owner=owner,
            values=integration_values(
                openai_compatible_base_url="https://user:secret@provider.example/v1"
            ),
        )

    assert not IntegrationConfiguration.objects.exists()
    assert not AuditEvent.objects.filter(action="integration_configuration.saved").exists()


@pytest.mark.django_db
def test_restore_validation_reports_ciphertext_without_disclosing_it(owner: User) -> None:
    configuration = save_integration_configuration(
        owner=owner,
        values=integration_values(outscraper_api_key="valid-secret"),
    )
    configuration.outscraper_api_key_encrypted = "v1:invalid-ciphertext"
    configuration.save(update_fields=("outscraper_api_key_encrypted", "updated_at"))

    failures = validate_encrypted_integration_credentials()

    assert failures == [f"credencial Outscraper de {configuration.pk} no descifrable"]
    assert "invalid-ciphertext" not in failures[0]


@pytest.mark.django_db
def test_environment_credentials_remain_a_backward_compatible_fallback(owner: User) -> None:
    with override_settings(
        OUTSCRAPER_API_KEY="environment-outscraper",
        LLM_API_KEY="environment-llm",
        GMAIL_OAUTH_CLIENT_SECRET="environment-google",
    ):
        assert get_outscraper_api_key(owner.pk) == "environment-outscraper"
        assert get_llm_api_key(owner.pk) == "environment-llm"
        assert get_gmail_oauth_client_secret(owner.pk) == "environment-google"


@pytest.mark.django_db
def test_provider_errors_redact_dashboard_credentials(owner: User) -> None:
    save_integration_configuration(
        owner=owner,
        values=integration_values(llm_api_key="credential-that-must-not-persist"),
    )

    error = RuntimeError("upstream echoed credential-that-must-not-persist in its response")
    redacted = redact_provider_error(error, owner_id=owner.pk)

    assert "credential-that-must-not-persist" not in redacted
    assert "[REDACTED]" in redacted
