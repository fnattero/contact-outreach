from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from apps.integrations.factory import (
    _require_fake,
    get_gmail_provider,
    get_llm_provider,
    get_website_fetcher,
)
from apps.integrations.gmail import GmailAPIProvider
from apps.integrations.llm import OllamaProvider, OpenAICompatibleProvider


def test_require_fake_accepts_fake_and_rejects_other_values() -> None:
    with override_settings(WEBSITE_FETCHER="fake"):
        _require_fake("WEBSITE_FETCHER")
    with override_settings(WEBSITE_FETCHER="http"):
        with pytest.raises(ImproperlyConfigured, match="configure 'fake'"):
            _require_fake("WEBSITE_FETCHER")


def test_unknown_website_fetcher_is_rejected() -> None:
    with pytest.raises(ImproperlyConfigured, match="not supported"):
        get_website_fetcher("carrier-pigeon")


def test_get_llm_provider_builds_ollama_and_openai_compatible_from_explicit_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ollama = get_llm_provider("ollama", base_url="http://ollama:11434", model="llama-x")
    assert isinstance(ollama, OllamaProvider)
    assert ollama.base_url == "http://ollama:11434"
    assert ollama.model == "llama-x"

    monkeypatch.setattr("apps.integrations.factory.get_llm_api_key", lambda owner_id=None: "key")
    compatible = get_llm_provider(
        "openai-compatible",
        base_url="https://llm.example/v1",
        model="model-x",
    )
    assert isinstance(compatible, OpenAICompatibleProvider)
    assert compatible.base_url == "https://llm.example/v1"


def test_unknown_llm_provider_is_rejected() -> None:
    with pytest.raises(ImproperlyConfigured, match="not supported"):
        get_llm_provider("carrier-pigeon", model="x")


def test_get_gmail_provider_builds_api_provider_from_runtime_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "apps.integrations.factory.runtime_integration_configuration",
        lambda owner_id=None: SimpleNamespace(
            gmail_provider="api",
            gmail_oauth_client_id="client-id",
        ),
    )
    monkeypatch.setattr(
        "apps.integrations.factory.get_gmail_oauth_client_secret",
        lambda owner_id=None: "client-secret",
    )

    provider = get_gmail_provider(refresh_token="refresh")

    assert isinstance(provider, GmailAPIProvider)
    assert provider.client_id == "client-id"
    assert provider.client_secret == "client-secret"


def test_unknown_gmail_provider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.integrations.factory.runtime_integration_configuration",
        lambda owner_id=None: SimpleNamespace(gmail_provider="carrier-pigeon"),
    )

    with pytest.raises(ImproperlyConfigured, match="not supported"):
        get_gmail_provider()
