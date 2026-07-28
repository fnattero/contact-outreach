from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from apps.integrations.contracts import (
    EmbeddingProvider,
    ExtractedBusiness,
    ExtractedEmail,
    ExtractionBatch,
    GmailAccountInfo,
    GmailConnectionData,
    GmailCursor,
    GmailHistoryExpired,
    GmailInboundMessage,
    GmailProvider,
    GmailReplyRequest,
    GmailSendRequest,
    GmailSendResult,
    GmailSyncBatch,
    LLMProvider,
    SearchRequest,
    ValidationProviderError,
    WebsiteEmailCandidate,
    WebsiteFetcher,
    WebsitePage,
    WebsiteRequest,
    WebsiteResult,
)
from apps.integrations.embeddings import FakeEmbeddingProvider
from apps.integrations.llm import MockLLMProvider

FAKE_GMAIL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
)


class MockExtractorProvider:
    """Deterministic fixture provider; it never opens a socket."""

    def search(self, request: SearchRequest) -> ExtractionBatch:
        scope = "|".join(
            (
                request.query,
                request.category,
                request.zone,
                request.location,
                request.dataset_snapshot_id,
            )
        )
        scope_hash = sha256(scope.encode()).hexdigest()[:12]
        rows: list[dict[str, Any]] = [
            {
                "place_id": f"fake-{scope_hash}-1",
                "name": "Taller Electromecánico Demo",
                "full_address": "CABA, Argentina",
                "site": "https://taller-demo.example",
                "emails": ["ventas@taller-demo.example"],
                "category": "Taller electromecánico",
            },
            {
                "place_id": f"fake-{scope_hash}-2",
                "name": "Negocio sin email",
                "full_address": "CABA, Argentina",
            },
        ]
        start = 0
        if request.cursor:
            try:
                start = next(
                    index + 1 for index, row in enumerate(rows) if row["place_id"] == request.cursor
                )
            except StopIteration as exc:
                raise ValidationProviderError("El cursor fake no pertenece a la consulta.") from exc
        selected = rows[start : start + request.limit]
        has_more = start + len(selected) < len(rows)
        next_cursor = str(selected[-1]["place_id"]) if selected and has_more else ""
        raw_payload: dict[str, Any] = {
            "status": "Success",
            "data": [selected],
            "next_cursor": next_cursor,
            "fixture_unknown": {"schema_can_change": True},
        }
        return ExtractionBatch(
            status="SUCCEEDED",
            raw_payload=raw_payload,
            operation="fake_places_query",
            next_cursor=next_cursor,
            units=Decimal(len(selected)),
            estimated_cost=Decimal("0"),
            actual_cost=Decimal("0"),
            currency="USD",
            usage_metadata={
                "provider": "fake",
                "next_cursor": next_cursor,
                "structured_query": {
                    "category": request.category,
                    "zone": request.zone,
                    "location": request.location,
                },
            },
            records=self.parse_response(raw_payload),
        )

    # Compatibility alias for fixtures and callers outside the durable campaign path.
    def extract(self, request: SearchRequest) -> ExtractionBatch:
        return self.search(request)

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
        hostname = (urlsplit(request.url).hostname or "example.invalid").casefold()
        email = f"contacto@{hostname}"
        body = f"Servicio de reparación de motores eléctricos. Contacto: {email}"
        content_hash = sha256(body.encode()).hexdigest()
        return WebsiteResult(
            pages=(
                WebsitePage(
                    requested_url=request.url,
                    final_url=request.url,
                    status_code=200,
                    text=body,
                    content_type="text/html",
                    content_hash=content_hash,
                    byte_count=len(body.encode()),
                    email_candidates=(
                        WebsiteEmailCandidate(
                            value=email,
                            source="visible_text",
                            page_url=request.url,
                            page_content_hash=content_hash,
                        ),
                    ),
                ),
            )
        )


FakeLLMProvider = MockLLMProvider


class FakeGmailProvider:
    """In-memory Gmail contract implementation with Message-ID deduplication."""

    def __init__(
        self, account_email: str = "owner@example.invalid", *, persist: bool = False
    ) -> None:
        self.account_email = account_email
        self.persist = persist
        self.revoked = False
        self._sent: dict[str, GmailSendResult] = {}

    def authorization_url(self, state: str, redirect_uri: str) -> str:
        parts = urlsplit(redirect_uri)
        query = dict(parse_qsl(parts.query))
        query.update({"state": state, "code": "fake-code"})
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))

    def exchange_code(self, code: str, redirect_uri: str) -> GmailConnectionData:
        del code, redirect_uri
        self.revoked = False
        return GmailConnectionData(
            email=self.account_email,
            refresh_token="fake-refresh-token",
            scopes=FAKE_GMAIL_SCOPES,
            history_id=self._current_history_id(),
        )

    def revoke(self) -> None:
        self.revoked = True

    def test_connection(self) -> GmailAccountInfo:
        return GmailAccountInfo(
            email=self.account_email,
            history_id=self._current_history_id(),
        )

    def _current_history_id(self) -> str:
        if not self.persist:
            return "0"
        from django.db.models import Max

        from apps.mailbox.models import FakeGmailMessage

        value = FakeGmailMessage.objects.aggregate(value=Max("history_id"))["value"]
        return str(value or 0)

    def send(self, request: GmailSendRequest) -> GmailSendResult:
        if self.persist:
            from apps.mailbox.models import FakeGmailMessage

            existing_record = FakeGmailMessage.objects.filter(
                rfc_message_id=request.message_id
            ).first()
            if existing_record is not None:
                return GmailSendResult(
                    message_id=existing_record.gmail_message_id,
                    thread_id=existing_record.gmail_thread_id,
                )
        existing = self._sent.get(request.message_id)
        if existing is not None:
            return existing
        digest = sha256(request.message_id.encode()).hexdigest()[:16]
        result = GmailSendResult(
            message_id=f"fake-message-{digest}", thread_id=f"fake-thread-{digest}"
        )
        self._sent[request.message_id] = result
        if self.persist:
            FakeGmailMessage.objects.create(
                rfc_message_id=request.message_id,
                gmail_message_id=result.message_id,
                gmail_thread_id=result.thread_id,
                recipient=request.recipient,
                raw_message=request.raw_message,
                idempotency_key=request.idempotency_key,
                direction=FakeGmailMessage.Direction.OUTBOUND,
            )
        return result

    def find_by_message_id(self, message_id: str) -> GmailSendResult | None:
        if self.persist:
            from apps.mailbox.models import FakeGmailMessage

            record = FakeGmailMessage.objects.filter(
                rfc_message_id=message_id,
                direction=FakeGmailMessage.Direction.OUTBOUND,
            ).first()
            if record is not None:
                return GmailSendResult(
                    message_id=record.gmail_message_id,
                    thread_id=record.gmail_thread_id,
                )
        return self._sent.get(message_id)

    def reply(self, request: GmailReplyRequest) -> GmailSendResult:
        if self.persist:
            from apps.mailbox.models import FakeGmailMessage

            existing_record = FakeGmailMessage.objects.filter(
                rfc_message_id=request.message_id
            ).first()
            if existing_record is not None:
                return GmailSendResult(
                    message_id=existing_record.gmail_message_id,
                    thread_id=existing_record.gmail_thread_id,
                )
        existing = self._sent.get(request.message_id)
        if existing is not None:
            return existing
        digest = sha256(request.message_id.encode()).hexdigest()[:16]
        result = GmailSendResult(message_id=f"fake-message-{digest}", thread_id=request.thread_id)
        self._sent[request.message_id] = result
        if self.persist:
            FakeGmailMessage.objects.create(
                rfc_message_id=request.message_id,
                gmail_message_id=result.message_id,
                gmail_thread_id=result.thread_id,
                recipient=request.recipient,
                raw_message=request.raw_message,
                idempotency_key=request.idempotency_key,
                direction=FakeGmailMessage.Direction.OUTBOUND,
            )
        return result

    def sync(self, cursor: GmailCursor | None) -> GmailSyncBatch:
        if not self.persist:
            current = int(cursor.history_id) if cursor is not None else 0
            return GmailSyncBatch(messages=(), next_cursor=GmailCursor(history_id=str(current)))
        from django.utils import timezone

        from apps.mailbox.models import FakeGmailMessage

        try:
            current = int(cursor.history_id) if cursor is not None else -1
        except ValueError as exc:
            raise GmailHistoryExpired("El historyId fake ya no es válido.") from exc
        queryset = FakeGmailMessage.objects.filter(direction=FakeGmailMessage.Direction.INBOUND)
        if cursor is not None:
            queryset = queryset.filter(history_id__gt=current)
        records = list(queryset.order_by("history_id")[:1000])
        latest = int(self._current_history_id())
        messages = tuple(
            GmailInboundMessage(
                message_id=item.gmail_message_id,
                thread_id=item.gmail_thread_id,
                rfc_message_id=item.rfc_message_id,
                in_reply_to=str(item.headers.get("In-Reply-To", "")),
                references=tuple(item.headers.get("References", [])),
                sender=item.sender,
                recipients=(item.recipient,) if item.recipient else (),
                subject=item.subject,
                body_text=item.body_text,
                body_html=item.body_html,
                received_at=item.received_at or item.created_at or timezone.now(),
                headers={str(key): str(value) for key, value in item.headers.items()},
            )
            for item in records
        )
        return GmailSyncBatch(
            messages=messages,
            next_cursor=GmailCursor(history_id=str(latest)),
            used_fallback=cursor is None,
        )

    def inject_inbound(
        self,
        *,
        thread_id: str,
        sender: str,
        recipient: str,
        subject: str,
        body_text: str,
        in_reply_to: str = "",
        references: tuple[str, ...] = (),
        body_html: str = "",
        rfc_message_id: str = "",
    ) -> GmailInboundMessage:
        """Add a deterministic fake mailbox response without opening the network."""
        if not self.persist:
            raise ValueError("La inyección fake durable requiere persist=True.")
        from django.utils import timezone

        from apps.mailbox.models import FakeGmailMessage

        history_id = int(self._current_history_id()) + 1
        stable_rfc_id = rfc_message_id or f"<fake-inbound-{history_id}@example.invalid>"
        digest = sha256(stable_rfc_id.encode()).hexdigest()[:16]
        headers: dict[str, object] = {
            "Message-ID": stable_rfc_id,
            "In-Reply-To": in_reply_to,
            "References": list(references),
        }
        record = FakeGmailMessage.objects.create(
            rfc_message_id=stable_rfc_id,
            gmail_message_id=f"fake-inbound-{digest}",
            gmail_thread_id=thread_id,
            direction=FakeGmailMessage.Direction.INBOUND,
            sender=sender,
            recipient=recipient,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            headers=headers,
            received_at=timezone.now(),
            history_id=history_id,
        )
        return GmailInboundMessage(
            message_id=record.gmail_message_id,
            thread_id=record.gmail_thread_id,
            rfc_message_id=record.rfc_message_id,
            in_reply_to=in_reply_to,
            references=references,
            sender=sender,
            recipients=(recipient,),
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            received_at=record.received_at or timezone.now(),
            headers={str(key): str(value) for key, value in headers.items()},
        )


def assert_protocol_compatibility() -> tuple[
    LLMProvider, EmbeddingProvider, WebsiteFetcher, GmailProvider
]:
    """Static type-checking witness for the fake implementations."""
    return FakeLLMProvider(), FakeEmbeddingProvider(), FakeWebsiteFetcher(), FakeGmailProvider()
