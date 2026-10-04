from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import override_settings

from apps.audit.models import AuditEvent
from apps.configuration.forms import IntegrationConfigurationForm
from apps.configuration.integrations import (
    GMAIL_CLIENT_SECRET_PURPOSE,
    LLM_KEY_PURPOSE,
    get_gmail_oauth_client_secret,
    get_llm_api_key,
    redact_provider_error,
    runtime_integration_configuration,
    save_integration_configuration,
    validate_encrypted_integration_credentials,
)
from apps.configuration.models import IntegrationConfiguration
from apps.core.crypto import decrypt_secret, encrypt_secret
from apps.mailbox.crypto import encrypt_token
from apps.mailbox.models import GmailConnection


def integration_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "extractor_provider": "fake",
        "overture_min_confidence": Decimal("0.750"),
        "website_fetcher": "fake",
        "llm_provider": "fake",
        "llm_model": "fake-deterministic",
        "ollama_base_url": "http://127.0.0.1:11434",
        "openai_compatible_base_url": "",
        "llm_api_key": "",
        "remove_llm_api_key": False,
        "embedding_provider": "fake",
        "embedding_model": "text-embedding-3-small",
        "embedding_dimensions": 1536,
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
        decrypt_secret(first, purpose=GMAIL_CLIENT_SECRET_PURPOSE)


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
def test_integration_form_rejects_secret_bearing_and_insecure_remote_urls(owner: User) -> None:
    runtime = runtime_integration_configuration(owner.pk)
    form = IntegrationConfigurationForm(
        integration_values(
            openai_compatible_base_url="http://attacker.example/v1?key=leak",
        ),
        user=owner,
        runtime=runtime,
    )

    assert not form.is_valid()
    assert "openai_compatible_base_url" in form.errors


@pytest.mark.django_db
def test_embedding_provider_requires_openai_connection_details(owner: User) -> None:
    runtime = runtime_integration_configuration(owner.pk)
    form = IntegrationConfigurationForm(
        integration_values(embedding_provider="openai-compatible"),
        user=owner,
        runtime=runtime,
    )

    assert not form.is_valid()
    assert "openai_compatible_base_url" in form.errors

    with pytest.raises(ValidationError, match="clave de acceso"):
        save_integration_configuration(
            owner=owner,
            values=integration_values(
                embedding_provider="openai-compatible",
                openai_compatible_base_url="https://llm.example.test/v1",
            ),
        )


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
        values=integration_values(llm_api_key="valid-secret"),
    )
    configuration.llm_api_key_encrypted = "v1:invalid-ciphertext"
    configuration.save(update_fields=("llm_api_key_encrypted", "updated_at"))

    failures = validate_encrypted_integration_credentials()

    assert failures == [f"credencial LLM de {configuration.pk} no descifrable"]
    assert "invalid-ciphertext" not in failures[0]


@pytest.mark.django_db
def test_environment_credentials_remain_a_backward_compatible_fallback(owner: User) -> None:
    with override_settings(
        LLM_API_KEY="environment-llm",
        GMAIL_OAUTH_CLIENT_SECRET="environment-google",
    ):
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
