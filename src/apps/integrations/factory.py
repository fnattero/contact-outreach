from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from apps.integrations.contracts import (
    ExtractorProvider,
    GmailProvider,
    LLMProvider,
    WebsiteFetcher,
)
from apps.integrations.fakes import (
    FakeExtractorProvider,
    FakeGmailProvider,
    FakeWebsiteFetcher,
)
from apps.integrations.gmail import GmailAPIProvider
from apps.integrations.llm import MockLLMProvider, OllamaProvider, OpenAICompatibleProvider
from apps.integrations.outscraper import OutscraperProvider
from apps.integrations.website import HttpWebsiteFetcher


def _require_fake(setting_name: str) -> None:
    value = getattr(settings, setting_name)
    if value != "fake":
        raise ImproperlyConfigured(
            f"{setting_name}={value!r} is unavailable in this phase; configure 'fake'"
        )


def get_extractor_provider(provider_name: str | None = None) -> ExtractorProvider:
    selected = provider_name or settings.EXTRACTOR_PROVIDER
    if selected == "fake":
        return FakeExtractorProvider()
    if selected == "outscraper":
        return OutscraperProvider(
            api_key=settings.OUTSCRAPER_API_KEY,
            base_url=settings.OUTSCRAPER_BASE_URL,
        )
    raise ImproperlyConfigured(f"Extractor provider {selected!r} is not supported")


def get_website_fetcher(fetcher_name: str | None = None) -> WebsiteFetcher:
    selected = fetcher_name or settings.WEBSITE_FETCHER
    if selected == "fake":
        return FakeWebsiteFetcher()
    if selected == "http":
        return HttpWebsiteFetcher()
    raise ImproperlyConfigured(f"Website fetcher {selected!r} is not supported")


def get_llm_provider(
    provider_name: str | None = None,
    *,
    base_url: str = "",
    model: str = "",
) -> LLMProvider:
    selected = provider_name or settings.LLM_PROVIDER
    selected_model = model or settings.LLM_MODEL
    if selected == "fake":
        return MockLLMProvider()
    if selected == "ollama":
        return OllamaProvider(
            base_url=base_url or settings.OLLAMA_BASE_URL,
            model=selected_model,
        )
    if selected == "openai-compatible":
        return OpenAICompatibleProvider(
            base_url=base_url or settings.OPENAI_COMPATIBLE_BASE_URL,
            model=selected_model,
            api_key=settings.LLM_API_KEY,
        )
    raise ImproperlyConfigured(f"LLM provider {selected!r} is not supported")


def get_gmail_provider(
    *,
    refresh_token: str = "",
    code_verifier: str = "",
    code_challenge: str = "",
    persist_fake: bool = False,
) -> GmailProvider:
    if settings.GMAIL_PROVIDER == "fake":
        return FakeGmailProvider(
            account_email=settings.GMAIL_FAKE_ACCOUNT_EMAIL,
            persist=persist_fake,
        )
    if settings.GMAIL_PROVIDER == "api":
        return GmailAPIProvider(
            client_id=settings.GMAIL_OAUTH_CLIENT_ID,
            client_secret=settings.GMAIL_OAUTH_CLIENT_SECRET,
            refresh_token=refresh_token,
            code_verifier=code_verifier,
            code_challenge=code_challenge,
        )
    raise ImproperlyConfigured(f"Gmail provider {settings.GMAIL_PROVIDER!r} is not supported")
