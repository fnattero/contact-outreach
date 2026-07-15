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
    FakeLLMProvider,
    FakeWebsiteFetcher,
)


def _require_fake(setting_name: str) -> None:
    value = getattr(settings, setting_name)
    if value != "fake":
        raise ImproperlyConfigured(
            f"{setting_name}={value!r} is unavailable in this phase; configure 'fake'"
        )


def get_extractor_provider() -> ExtractorProvider:
    _require_fake("EXTRACTOR_PROVIDER")
    return FakeExtractorProvider()


def get_website_fetcher() -> WebsiteFetcher:
    _require_fake("WEBSITE_FETCHER")
    return FakeWebsiteFetcher()


def get_llm_provider() -> LLMProvider:
    _require_fake("LLM_PROVIDER")
    return FakeLLMProvider()


def get_gmail_provider() -> GmailProvider:
    _require_fake("GMAIL_PROVIDER")
    return FakeGmailProvider()
