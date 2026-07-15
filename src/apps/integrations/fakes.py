from __future__ import annotations

from hashlib import sha256
from typing import Any
from urllib.parse import urlencode

from apps.integrations.contracts import (
    ExtractedBusiness,
    ExtractedEmail,
    ExtractionBatch,
    GmailAccountInfo,
    GmailConnectionData,
    GmailCursor,
    GmailProvider,
    GmailReplyRequest,
    GmailSendRequest,
    GmailSendResult,
    GmailSyncBatch,
    LLMProvider,
    SearchRequest,
    WebsiteFetcher,
    WebsitePage,
    WebsiteRequest,
    WebsiteResult,
)
from apps.integrations.llm import MockLLMProvider

FAKE_GMAIL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
)


class MockExtractorProvider:
    """Deterministic fixture provider; it never opens a socket."""

    def submit(self, request: SearchRequest) -> ExtractionBatch:
        request_hash = sha256(request.idempotency_key.encode()).hexdigest()[:12]
        request_id = f"fake-request-{request_hash}"
        raw_payload: dict[str, Any] = {
            "id": request_id,
            "status": "Success",
            "data": [
                [
                    {
                        "place_id": f"fake-{request_hash}-1",
                        "name": "Taller Electromecánico Demo",
                        "full_address": "CABA, Argentina",
                        "site": "https://taller-demo.example",
                        "emails": ["ventas@taller-demo.example"],
                        "category": "Taller electromecánico",
                    },
                    {
                        "place_id": f"fake-{request_hash}-2",
                        "name": "Negocio sin email",
                        "full_address": "CABA, Argentina",
                    },
                ]
            ],
            "fixture_unknown": {"schema_can_change": True},
        }
        return ExtractionBatch(
            status="SUCCEEDED",
            request_id=request_id,
            raw_payload=raw_payload,
            records=self.parse_response(raw_payload),
        )

    def extract(self, request: SearchRequest) -> ExtractionBatch:
        return self.submit(request)

    def poll(self, *, request_id: str, timeout_seconds: float = 30.0) -> ExtractionBatch:
        del timeout_seconds
        return ExtractionBatch(
            status="SUCCEEDED",
            request_id=request_id,
            raw_payload={"id": request_id, "status": "Success", "data": []},
        )

    def parse_response(self, raw_payload: dict[str, Any]) -> tuple[ExtractedBusiness, ...]:
        groups = raw_payload.get("data", [])
        if not isinstance(groups, list):
            return ()
        rows = [row for group in groups if isinstance(group, list) for row in group]
        records: list[ExtractedBusiness] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            emails = row.get("emails", [])
            email_values = emails if isinstance(emails, list) else []
            records.append(
                ExtractedBusiness(
                    provider_id=str(row.get("place_id", "")),
                    name=str(row.get("name", "")),
                    address=str(row.get("full_address", "")),
                    email_candidates=tuple(
                        ExtractedEmail(value=str(value), order=index)
                        for index, value in enumerate(email_values)
                    ),
                    website=str(row["site"]) if row.get("site") else None,
                    category=str(row["category"]) if row.get("category") else None,
                    provider_data={"place_id": row.get("place_id")},
                )
            )
        return tuple(records)


# Backward-compatible name for existing imports; documentation calls this provider Mock.
FakeExtractorProvider = MockExtractorProvider


class FakeWebsiteFetcher:
    """Returns bounded local fixtures and performs no DNS or HTTP calls."""

    def fetch(self, request: WebsiteRequest) -> WebsiteResult:
        return WebsiteResult(
            pages=(
                WebsitePage(
                    requested_url=request.url,
                    final_url=request.url,
                    status_code=200,
                    text="Servicio de reparación de motores eléctricos.",
                    content_type="text/html",
                    content_hash=sha256(
                        b"Servicio de reparacion de motores electricos."
                    ).hexdigest(),
                    byte_count=48,
                ),
            )
        )


FakeLLMProvider = MockLLMProvider


class FakeGmailProvider:
    """In-memory Gmail contract implementation with Message-ID deduplication."""

    def __init__(self, account_email: str = "owner@example.invalid") -> None:
        self.account_email = account_email
        self.revoked = False
        self._sent: dict[str, GmailSendResult] = {}

    def authorization_url(self, state: str, redirect_uri: str) -> str:
        query = urlencode({"state": state, "redirect_uri": redirect_uri})
        return f"https://fake-gmail.invalid/authorize?{query}"

    def exchange_code(self, code: str, redirect_uri: str) -> GmailConnectionData:
        del code, redirect_uri
        self.revoked = False
        return GmailConnectionData(
            email=self.account_email,
            refresh_token="fake-refresh-token",
            scopes=FAKE_GMAIL_SCOPES,
        )

    def revoke(self) -> None:
        self.revoked = True

    def test_connection(self) -> GmailAccountInfo:
        return GmailAccountInfo(email=self.account_email)

    def send(self, request: GmailSendRequest) -> GmailSendResult:
        existing = self._sent.get(request.message_id)
        if existing is not None:
            return existing
        digest = sha256(request.message_id.encode()).hexdigest()[:16]
        result = GmailSendResult(
            message_id=f"fake-message-{digest}", thread_id=f"fake-thread-{digest}"
        )
        self._sent[request.message_id] = result
        return result

    def reply(self, request: GmailReplyRequest) -> GmailSendResult:
        existing = self._sent.get(request.message_id)
        if existing is not None:
            return existing
        digest = sha256(request.message_id.encode()).hexdigest()[:16]
        result = GmailSendResult(message_id=f"fake-message-{digest}", thread_id=request.thread_id)
        self._sent[request.message_id] = result
        return result

    def sync(self, cursor: GmailCursor | None) -> GmailSyncBatch:
        current = int(cursor.history_id) if cursor is not None else 0
        return GmailSyncBatch(messages=(), next_cursor=GmailCursor(history_id=str(current + 1)))


def assert_protocol_compatibility() -> tuple[LLMProvider, WebsiteFetcher, GmailProvider]:
    """Static type-checking witness for the fake implementations."""
    return FakeLLMProvider(), FakeWebsiteFetcher(), FakeGmailProvider()
