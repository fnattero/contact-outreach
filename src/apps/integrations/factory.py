from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from apps.configuration.integrations import (
    get_gmail_oauth_client_secret,
    get_llm_api_key,
    get_outscraper_api_key,
    runtime_integration_configuration,
)
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


def get_extractor_provider(
    provider_name: str | None = None, *, owner_id: int | None = None
) -> ExtractorProvider:
    runtime = runtime_integration_configuration(owner_id)
    selected = provider_name or runtime.extractor_provider
    if selected == "fake":
        return FakeExtractorProvider()
    if selected == "outscraper":
        return OutscraperProvider(
            api_key=get_outscraper_api_key(owner_id),
            base_url=runtime.outscraper_base_url,
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
    owner_id: int | None = None,
) -> LLMProvider:
    runtime = runtime_integration_configuration(owner_id)
    selected = provider_name or runtime.llm_provider
    selected_model = model or runtime.llm_model
    if selected == "fake":
        return MockLLMProvider()
    if selected == "ollama":
        return OllamaProvider(
            base_url=base_url or runtime.ollama_base_url,
            model=selected_model,
        )
    if selected == "openai-compatible":
        return OpenAICompatibleProvider(
            base_url=base_url or runtime.openai_compatible_base_url,
            model=selected_model,
            api_key=get_llm_api_key(owner_id),
        )
    raise ImproperlyConfigured(f"LLM provider {selected!r} is not supported")


def get_gmail_provider(
    *,
    refresh_token: str = "",
    code_verifier: str = "",
    code_challenge: str = "",
    persist_fake: bool = False,
    owner_id: int | None = None,
) -> GmailProvider:
    runtime = runtime_integration_configuration(owner_id)
    if runtime.gmail_provider == "fake":
        return FakeGmailProvider(
            account_email=settings.GMAIL_FAKE_ACCOUNT_EMAIL,
            persist=persist_fake,
        )
    if runtime.gmail_provider == "api":
        return GmailAPIProvider(
            client_id=runtime.gmail_oauth_client_id,
            client_secret=get_gmail_oauth_client_secret(owner_id),
            refresh_token=refresh_token,
            code_verifier=code_verifier,
            code_challenge=code_challenge,
        )
    raise ImproperlyConfigured(f"Gmail provider {runtime.gmail_provider!r} is not supported")
