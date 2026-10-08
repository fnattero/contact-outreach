from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from tests.apps.configuration.test_integrations import integration_values

from apps.audit.models import AuditEvent
from apps.campaigns.models import Campaign
from apps.campaigns.services import _prompt_snapshot
from apps.configuration.integrations import (
    RELEVANCE_LLM_KEY_PURPOSE,
    get_llm_api_key,
    get_relevance_llm_api_key,
    redact_provider_error,
    runtime_integration_configuration,
    save_integration_configuration,
    validate_encrypted_integration_credentials,
)
from apps.configuration.models import IntegrationConfiguration
from apps.core.crypto import decrypt_secret
from apps.integrations.factory import get_screening_provider
from apps.integrations.llm import OpenAICompatibleProvider

OPENAI = "https://api.replies.example/v1"
OTHER = "https://api.audience.example/v1"


def _replies_on_openai(owner: User) -> None:
    save_integration_configuration(
        owner=owner,
        values=integration_values(
            llm_provider="openai-compatible",
            llm_model="big-model",
            openai_compatible_base_url=OPENAI,
            llm_api_key="replies-key",
        ),
    )


@pytest.mark.django_db
def test_by_default_the_audience_filter_shares_the_reply_connection(owner: User) -> None:
    _replies_on_openai(owner)

    runtime = runtime_integration_configuration(owner.pk)

    assert not runtime.relevance_separate
    assert runtime.relevance_connection() == ("openai-compatible", OPENAI)
    assert runtime.relevance_model() == "big-model"
    assert get_relevance_llm_api_key(owner.pk) == "replies-key"
    assert runtime.relevance_llm_credential_configured


@pytest.mark.django_db
def test_the_filter_can_use_another_service_with_its_own_key_and_the_reply_key_is_untouched(
    owner: User,
) -> None:
    _replies_on_openai(owner)

    save_integration_configuration(
        owner=owner,
        values=integration_values(
            llm_provider="openai-compatible",
            llm_model="big-model",
            openai_compatible_base_url=OPENAI,
            relevance_llm_provider="openai-compatible",
            relevance_llm_base_url=OTHER,
            relevance_llm_model="cheap-model",
            relevance_llm_api_key="audience-key",
        ),
    )

    runtime = runtime_integration_configuration(owner.pk)
    assert runtime.relevance_connection() == ("openai-compatible", OTHER)
    assert runtime.relevance_model() == "cheap-model"
    assert get_relevance_llm_api_key(owner.pk) == "audience-key"
    assert get_llm_api_key(owner.pk) == "replies-key"
    configuration = IntegrationConfiguration.objects.get(owner=owner)
    assert "audience-key" not in configuration.relevance_llm_api_key_encrypted
    assert (
        decrypt_secret(
            configuration.relevance_llm_api_key_encrypted, purpose=RELEVANCE_LLM_KEY_PURPOSE
        )
        == "audience-key"
    )
    event = AuditEvent.objects.filter(action="integration_configuration.saved").latest("created_at")
    assert "audience-key" not in str(event.after) + str(event.before)
    assert not validate_encrypted_integration_credentials()


@pytest.mark.django_db
def test_a_reply_key_is_never_sent_to_a_different_service(owner: User) -> None:
    _replies_on_openai(owner)

    with pytest.raises(ValidationError, match="propia clave"):
        save_integration_configuration(
            owner=owner,
            values=integration_values(
                llm_provider="openai-compatible",
                llm_model="big-model",
                openai_compatible_base_url=OPENAI,
                relevance_llm_provider="openai-compatible",
                relevance_llm_base_url=OTHER,
                relevance_llm_model="cheap-model",
            ),
        )


@pytest.mark.django_db
def test_the_same_service_may_reuse_the_reply_key(owner: User) -> None:
    _replies_on_openai(owner)

    save_integration_configuration(
        owner=owner,
        values=integration_values(
            llm_provider="openai-compatible",
            llm_model="big-model",
            openai_compatible_base_url=OPENAI,
            relevance_llm_provider="openai-compatible",
            relevance_llm_model="cheap-model",
        ),
    )

    runtime = runtime_integration_configuration(owner.pk)
    assert runtime.relevance_llm_credential_source == "SHARED"
    assert get_relevance_llm_api_key(owner.pk) == "replies-key"


@pytest.mark.django_db
def test_a_separate_service_needs_its_own_model(owner: User) -> None:
    with pytest.raises(ValidationError, match="modelo del filtro"):
        save_integration_configuration(
            owner=owner,
            values=integration_values(relevance_llm_provider="ollama"),
        )


@pytest.mark.django_db
def test_the_filter_can_run_locally_while_replies_use_a_paid_service(owner: User) -> None:
    _replies_on_openai(owner)

    save_integration_configuration(
        owner=owner,
        values=integration_values(
            llm_provider="openai-compatible",
            llm_model="big-model",
            openai_compatible_base_url=OPENAI,
            relevance_llm_provider="ollama",
            relevance_llm_model="llama-small",
        ),
    )

    runtime = runtime_integration_configuration(owner.pk)
    assert runtime.relevance_connection() == ("ollama", "http://127.0.0.1:11434")
    assert get_relevance_llm_api_key(owner.pk) == ""


@pytest.mark.django_db
def test_going_back_to_the_shared_connection_forgets_the_filters_own_key(owner: User) -> None:
    _replies_on_openai(owner)
    save_integration_configuration(
        owner=owner,
        values=integration_values(
            llm_provider="openai-compatible",
            llm_model="big-model",
            openai_compatible_base_url=OPENAI,
            relevance_llm_provider="openai-compatible",
            relevance_llm_base_url=OTHER,
            relevance_llm_model="cheap-model",
            relevance_llm_api_key="audience-key",
        ),
    )

    save_integration_configuration(
        owner=owner,
        values=integration_values(
            llm_provider="openai-compatible",
            llm_model="big-model",
            openai_compatible_base_url=OPENAI,
            relevance_llm_provider="",
        ),
    )

    configuration = IntegrationConfiguration.objects.get(owner=owner)
    assert configuration.relevance_llm_api_key_encrypted == ""
    assert configuration.relevance_llm_base_url == ""
    assert get_relevance_llm_api_key(owner.pk) == "replies-key"


@pytest.mark.django_db
def test_provider_errors_redact_the_filters_key_too(owner: User) -> None:
    _replies_on_openai(owner)
    save_integration_configuration(
        owner=owner,
        values=integration_values(
            llm_provider="openai-compatible",
            llm_model="big-model",
            openai_compatible_base_url=OPENAI,
            relevance_llm_provider="openai-compatible",
            relevance_llm_base_url=OTHER,
            relevance_llm_model="cheap-model",
            relevance_llm_api_key="audience-key-123",
        ),
    )

    message = redact_provider_error(RuntimeError("rejected audience-key-123"), owner_id=owner.pk)

    assert "audience-key-123" not in message


@pytest.mark.django_db
def test_the_screening_provider_uses_the_filters_address_and_key(owner: User) -> None:
    _replies_on_openai(owner)
    save_integration_configuration(
        owner=owner,
        values=integration_values(
            llm_provider="openai-compatible",
            llm_model="big-model",
            openai_compatible_base_url=OPENAI,
            relevance_llm_provider="openai-compatible",
            relevance_llm_base_url=OTHER,
            relevance_llm_model="cheap-model",
            relevance_llm_api_key="audience-key",
        ),
    )

    provider = get_screening_provider(
        "openai-compatible", base_url=OTHER, model="cheap-model", owner_id=owner.pk
    )

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.base_url == OTHER
    assert provider.api_key == "audience-key"


@pytest.mark.django_db
def test_a_campaign_freezes_the_filters_connection_but_never_its_key(owner: User) -> None:
    _replies_on_openai(owner)
    save_integration_configuration(
        owner=owner,
        values=integration_values(
            llm_provider="openai-compatible",
            llm_model="big-model",
            openai_compatible_base_url=OPENAI,
            relevance_llm_provider="openai-compatible",
            relevance_llm_base_url=OTHER,
            relevance_llm_model="cheap-model",
            relevance_llm_api_key="audience-key",
        ),
    )
    campaign = Campaign(created_by=owner, llm_model="big-model")

    screening = _prompt_snapshot(campaign)["prospect_screening"]

    assert screening["provider"] == "openai-compatible"
    assert screening["base_url"] == OTHER
    assert screening["model"] == "cheap-model"
    assert "audience-key" not in str(screening)
