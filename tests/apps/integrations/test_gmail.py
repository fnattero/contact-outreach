from __future__ import annotations

import base64
import json
from email.message import Message
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from apps.integrations.contracts import (
    AuthenticationError,
    GmailCursor,
    GmailReplyRequest,
    GmailSendRequest,
    PermanentProviderError,
    RateLimitError,
    RetryableProviderError,
    ValidationProviderError,
)
from apps.integrations.gmail import (
    GMAIL_SCOPES,
    GmailAPIProvider,
    GmailHTTPTransport,
    HTTPResponse,
)


class StubTransport(GmailHTTPTransport):
    def __init__(self, responses: list[HTTPResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def request(self, **kwargs: object) -> HTTPResponse:
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _provider(transport: StubTransport, **kwargs: str) -> GmailAPIProvider:
    return GmailAPIProvider(
        client_id="client-id",
        client_secret="client-secret",
        transport=transport,
        **kwargs,
    )


def test_gmail_api_oauth_refresh_profile_send_reply_reconcile_and_revoke() -> None:
    transport = StubTransport(
        [
            HTTPResponse(
                200,
                {
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "scope": " ".join(GMAIL_SCOPES),
                },
            ),
            HTTPResponse(200, {"emailAddress": "owner@gmail.com"}),
            HTTPResponse(200, {"id": "gmail-1", "threadId": "thread-1"}),
            HTTPResponse(200, {"id": "gmail-2", "threadId": "thread-existing"}),
            HTTPResponse(200, {"messages": []}),
            HTTPResponse(200, {"messages": [{"id": "gmail-1", "threadId": "thread-1"}]}),
            HTTPResponse(200, {}),
        ]
    )
    provider = _provider(
        transport,
        code_verifier="verifier",
        code_challenge="challenge",
        refresh_token="refresh",
    )
    url = provider.authorization_url("state", "http://localhost/callback")
    assert "code_challenge=challenge" in url
    assert "access_type=offline" in url
    data = provider.exchange_code("code", "http://localhost/callback")
    assert data.email == "owner@gmail.com"
    assert set(data.scopes) == set(GMAIL_SCOPES)

    request = GmailSendRequest(
        recipient="prospect@example.com",
        raw_message=b"raw mime",
        message_id="<stable@example.com>",
        correlation_id="correlation",
        idempotency_key="send",
    )
    assert provider.send(request).message_id == "gmail-1"
    reply = provider.reply(
        GmailReplyRequest(
            recipient=request.recipient,
            raw_message=b"reply mime",
            message_id="<reply@example.com>",
            thread_id="thread-existing",
            correlation_id="correlation",
            idempotency_key="reply",
        )
    )
    assert reply.thread_id == "thread-existing"
    assert provider.find_by_message_id("<missing@example.com>") is None
    assert provider.find_by_message_id(request.message_id).message_id == "gmail-1"  # type: ignore[union-attr]
    provider.revoke()
    assert transport.calls[0]["form"] == {
        "code": "code",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "redirect_uri": "http://localhost/callback",
        "grant_type": "authorization_code",
        "code_verifier": "verifier",
    }
    assert "q=in%3Asent%20rfc822msgid%3A%3Cmissing%40example.com%3E" in str(
        transport.calls[4]["url"]
    )


def test_gmail_reconciliation_rejects_duplicate_sent_message_ids() -> None:
    transport = StubTransport(
        [
            HTTPResponse(200, {"access_token": "fresh"}),
            HTTPResponse(
                200,
                {
                    "messages": [
                        {"id": "gmail-1", "threadId": "thread-1"},
                        {"id": "gmail-2", "threadId": "thread-2"},
                    ]
                },
            ),
        ]
    )
    provider = _provider(transport, refresh_token="refresh")

    with pytest.raises(ValidationProviderError, match="más de un mensaje enviado"):
        provider.find_by_message_id("<stable@example.com>")

    assert "q=in%3Asent%20rfc822msgid%3A%3Cstable%40example.com%3E" in str(
        transport.calls[1]["url"]
    )


def test_gmail_api_refreshes_access_token_and_validates_responses() -> None:
    transport = StubTransport(
        [
            HTTPResponse(200, {"access_token": "fresh"}),
            HTTPResponse(200, {"emailAddress": "owner@gmail.com"}),
            HTTPResponse(200, {}),
            HTTPResponse(200, {"emailAddress": "owner@gmail.com", "historyId": "55"}),
            HTTPResponse(200, {"messages": []}),
        ]
    )
    provider = _provider(transport, refresh_token="refresh")
    assert provider.test_connection().email == "owner@gmail.com"
    with pytest.raises(ValidationProviderError, match="IDs"):
        provider.send(
            GmailSendRequest(
                recipient="a@example.com",
                raw_message=b"raw",
                message_id="<id@example.com>",
                correlation_id="c",
                idempotency_key="i",
            )
        )

    with pytest.raises(AuthenticationError, match="refresh token"):
        _provider(StubTransport([])).test_connection()
    with pytest.raises(AuthenticationError, match="CLIENT"):
        GmailAPIProvider(client_id="", client_secret="")
    batch = provider.sync(None)
    assert batch.used_fallback is True
    assert batch.next_cursor.history_id == "55"
    assert str(transport.calls[-2]["url"]).endswith("/users/me/profile")
    assert "q=newer_than%3A30d" in str(transport.calls[-1]["url"])


def test_gmail_api_incremental_sync_maps_allowed_headers_and_bodies() -> None:
    text = base64.urlsafe_b64encode(b"Hola, me interesa").decode().rstrip("=")
    transport = StubTransport(
        [
            HTTPResponse(
                200,
                {
                    "historyId": "11",
                    "history": [{"messagesAdded": [{"message": {"id": "incoming-1"}}]}],
                },
            ),
            HTTPResponse(
                200,
                {
                    "id": "incoming-1",
                    "threadId": "thread-1",
                    "internalDate": "1784203200000",
                    "payload": {
                        "mimeType": "multipart/mixed",
                        "headers": [
                            {"name": "Message-ID", "value": "<incoming@example.com>"},
                            {"name": "In-Reply-To", "value": "<root@example.com>"},
                            {"name": "References", "value": "<root@example.com>"},
                            {"name": "From", "value": "Prospecto <ventas@example.com>"},
                            {"name": "To", "value": "owner@gmail.com"},
                            {"name": "Subject", "value": "PUBLICIDAD - Consulta"},
                            {"name": "X-Untrusted-Secret", "value": "discard-me"},
                        ],
                        "parts": [
                            {"mimeType": "text/plain", "body": {"data": text}},
                            {
                                "mimeType": "text/plain",
                                "filename": "attachment.txt",
                                "body": {
                                    "data": base64.urlsafe_b64encode(b"do not import").decode()
                                },
                            },
                            {
                                "mimeType": "text/plain",
                                "body": {"attachmentId": "detached-text"},
                            },
                        ],
                    },
                },
            ),
            HTTPResponse(
                200,
                {"data": base64.urlsafe_b64encode(b"Texto grande separado").decode()},
            ),
        ]
    )
    provider = _provider(transport, refresh_token="refresh")
    provider._access_token = "access"

    batch = provider.sync(GmailCursor("10"))

    assert batch.used_fallback is False
    assert batch.next_cursor.history_id == "11"
    assert len(batch.messages) == 1
    message = batch.messages[0]
    assert message.body_text == "Hola, me interesa\nTexto grande separado"
    assert message.in_reply_to == "<root@example.com>"
    assert message.references == ("<root@example.com>",)
    assert "X-Untrusted-Secret" not in message.headers
    assert "startHistoryId=10" in str(transport.calls[0]["url"])
    assert str(transport.calls[2]["url"]).endswith("/messages/incoming-1/attachments/detached-text")


class URLResponse:
    def __init__(self, payload: dict[str, object] | None, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self) -> URLResponse:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def read(self) -> bytes:
        return b"" if self.payload is None else json.dumps(self.payload).encode()


def test_http_transport_serializes_and_translates_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = GmailHTTPTransport()
    monkeypatch.setattr(
        "apps.integrations.gmail.urlopen",
        lambda request, timeout: URLResponse({"ok": True}),
    )
    assert transport.request(
        method="POST", url="https://example.test", form={"a": "b"}
    ).payload == {"ok": True}
    assert (
        transport.request(
            method="POST", url="https://example.test", json_payload={"a": "b"}
        ).status_code
        == 200
    )
    monkeypatch.setattr(
        "apps.integrations.gmail.urlopen",
        lambda request, timeout: URLResponse(None),
    )
    assert transport.request(method="POST", url="https://example.test/revoke").payload == {}

    headers = Message()
    headers["Retry-After"] = "42"

    def http_error(request: object, timeout: float) -> URLResponse:
        del request, timeout
        raise HTTPError(
            "https://example.test",
            429,
            "limited",
            headers,
            BytesIO(b'{"error":{"message":"limited"}}'),
        )

    monkeypatch.setattr("apps.integrations.gmail.urlopen", http_error)
    with pytest.raises(RateLimitError) as limited:
        transport.request(method="GET", url="https://example.test")
    assert limited.value.retry_after == 42

    for status, error_type in (
        (401, AuthenticationError),
        (403, RateLimitError),
        (500, RetryableProviderError),
        (400, ValidationProviderError),
        (418, PermanentProviderError),
    ):
        with pytest.raises(error_type):
            GmailHTTPTransport._raise_http(status, {}, "invalid")

    monkeypatch.setattr(
        "apps.integrations.gmail.urlopen",
        lambda request, timeout: (_ for _ in ()).throw(URLError("offline")),
    )
    with pytest.raises(RetryableProviderError):
        transport.request(method="GET", url="https://example.test")
