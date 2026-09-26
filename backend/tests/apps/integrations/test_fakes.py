from __future__ import annotations

import socket

import pytest
from django.core.exceptions import ImproperlyConfigured
from pytest_socket import SocketBlockedError

from apps.integrations.contracts import (
    AnalysisFact,
    AnalysisRequest,
    EmbeddingRequest,
    GmailCursor,
    GmailReplyRequest,
    GmailSendRequest,
    ReplyClassificationRequest,
    SearchRequest,
    WebsiteRequest,
)
from apps.integrations.factory import (
    get_embedding_provider,
    get_extractor_provider,
    get_gmail_provider,
    get_llm_provider,
    get_website_fetcher,
)
from apps.integrations.overture import OverturePlacesProvider


def test_network_is_blocked_during_tests() -> None:
    with (
        pytest.warns(UserWarning, match="tried to use socket"),
        pytest.raises(SocketBlockedError),
    ):
        socket.socket()


def test_fake_extractor_and_website_are_deterministic() -> None:
    extractor = get_extractor_provider()
    request = SearchRequest(query="motores", correlation_id="correlation", idempotency_key="key")
    first = extractor.search(request)
    second = extractor.search(request)
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


def test_fake_embeddings_work_without_external_calls() -> None:
    provider = get_embedding_provider()
    result = provider.embed(
        EmbeddingRequest(
            texts=("componentes",),
            correlation_id="correlation",
            idempotency_key="embedding-key",
            model="fake-embedding",
            dimensions=128,
        )
    )

    assert len(result.vectors) == 1
    assert len(result.vectors[0]) == 128


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
    assert provider.sync(GmailCursor(history_id="4")).next_cursor.history_id == "4"
    provider.revoke()


def test_unknown_extractor_provider_cannot_be_executed() -> None:
    with pytest.raises(ImproperlyConfigured, match="not supported"):
        get_extractor_provider("retired-provider")


def test_overture_factory_returns_local_snapshot_provider() -> None:
    assert isinstance(get_extractor_provider("overture"), OverturePlacesProvider)
