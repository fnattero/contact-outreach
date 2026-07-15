from __future__ import annotations

import socket

import pytest
from django.test import override_settings
from pytest_socket import SocketBlockedError

from apps.integrations.contracts import (
    AnalysisFact,
    AnalysisRequest,
    AuthenticationError,
    GmailCursor,
    GmailReplyRequest,
    GmailSendRequest,
    ReplyClassificationRequest,
    SearchRequest,
    WebsiteRequest,
)
from apps.integrations.factory import (
    get_extractor_provider,
    get_gmail_provider,
    get_llm_provider,
    get_website_fetcher,
)


def test_network_is_blocked_during_tests() -> None:
    with (
        pytest.warns(UserWarning, match="tried to use socket"),
        pytest.raises(SocketBlockedError),
    ):
        socket.socket()


def test_fake_extractor_and_website_are_deterministic() -> None:
    extractor = get_extractor_provider()
    request = SearchRequest(query="motores", correlation_id="correlation", idempotency_key="key")
    first = extractor.extract(request)
    second = extractor.extract(request)
    assert first == second
    assert len(first.records) == 2
    assert first.records[1].email_candidates == ()

    website = get_website_fetcher().fetch(
        WebsiteRequest(url="https://example.invalid", correlation_id="correlation")
    )
    assert website.pages[0].status_code == 200
    assert website.pages[0].final_url == "https://example.invalid"


def test_fake_llm_analyzes_and_classifies_without_external_calls() -> None:
    provider = get_llm_provider()
    result = provider.analyze(
        AnalysisRequest(
            facts=(AnalysisFact(fact_id="business.activity", value="reparaciones"),),
            correlation_id="correlation",
            idempotency_key="key",
        )
    )
    assert result.relevance_score == 80
    assert result.evidence == ("business.activity",)
    classification = provider.classify_reply(
        ReplyClassificationRequest(
            body_text="Por favor, dar de BAJA",
            correlation_id="correlation",
            idempotency_key="reply-key",
        )
    )
    assert classification.classification == "UNSUBSCRIBE"


def test_fake_gmail_deduplicates_message_id_and_keeps_reply_thread() -> None:
    provider = get_gmail_provider()
    assert "state=state" in provider.authorization_url("state", "http://localhost/callback")
    assert provider.exchange_code("code", "http://localhost/callback").email.endswith(".invalid")
    assert provider.test_connection().email.endswith(".invalid")

    request = GmailSendRequest(
        recipient="prospect@example.invalid",
        raw_message=b"fake mime",
        message_id="<stable@example.invalid>",
        correlation_id="correlation",
        idempotency_key="send-key",
    )
    assert provider.send(request) == provider.send(request)
    reply = provider.reply(
        GmailReplyRequest(
            recipient=request.recipient,
            raw_message=b"fake reply",
            message_id="<reply@example.invalid>",
            thread_id="existing-thread",
            correlation_id="correlation",
            idempotency_key="reply-key",
        )
    )
    assert reply.thread_id == "existing-thread"
    assert provider.sync(GmailCursor(history_id="4")).next_cursor.history_id == "5"
    provider.revoke()


@override_settings(EXTRACTOR_PROVIDER="outscraper")
def test_real_provider_requires_environment_credential() -> None:
    with pytest.raises(AuthenticationError, match="OUTSCRAPER_API_KEY"):
        get_extractor_provider()
