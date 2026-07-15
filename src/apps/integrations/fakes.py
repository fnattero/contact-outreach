from __future__ import annotations

from hashlib import sha256
from urllib.parse import urlencode

from apps.integrations.contracts import (
    AIAnalysisResult,
    AnalysisRequest,
    ExtractedBusiness,
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
    ReplyClassification,
    ReplyClassificationRequest,
    SearchRequest,
    WebsiteFetcher,
    WebsitePage,
    WebsiteRequest,
    WebsiteResult,
)

FAKE_GMAIL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
)


class FakeExtractorProvider:
    """Deterministic fixture provider; it never opens a socket."""

    def extract(self, request: SearchRequest) -> ExtractionBatch:
        request_hash = sha256(request.idempotency_key.encode()).hexdigest()[:12]
        return ExtractionBatch(
            records=(
                ExtractedBusiness(
                    provider_id=f"fake-{request_hash}-1",
                    name="Taller Electromecánico Demo",
                    address="CABA, Argentina",
                    email_candidates=("ventas@example.invalid",),
                    website="https://example.invalid",
                ),
                ExtractedBusiness(
                    provider_id=f"fake-{request_hash}-2",
                    name="Negocio sin email",
                    address="CABA, Argentina",
                    email_candidates=(),
                ),
            ),
            request_id=f"fake-request-{request_hash}",
        )


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
                ),
            )
        )


class FakeLLMProvider:
    """Rule-based provider used for development and automated tests."""

    def analyze(self, request: AnalysisRequest) -> AIAnalysisResult:
        evidence = tuple(fact.fact_id for fact in request.facts[:2])
        score = 80 if request.facts else 0
        return AIAnalysisResult(
            relevance_score=score,
            confidence=0.9 if request.facts else 0.0,
            relevance_reason="Los hechos provistos indican actividad electromecánica.",
            evidence=evidence,
            subject="Consulta por carbones para motores",
            body_text=(
                "Somos proveedores de carbones para motores y queremos conocer si nuestra línea "
                "puede ser útil para su actividad. ¿Qué día conviene que pase el vendedor?"
            ),
        )

    def classify_reply(self, request: ReplyClassificationRequest) -> ReplyClassification:
        normalized = request.body_text.casefold()
        if "baja" in normalized:
            return ReplyClassification(classification="UNSUBSCRIBE", confidence=1.0)
        if "interes" in normalized:
            return ReplyClassification(classification="INTERESTED", confidence=0.9)
        return ReplyClassification(classification="OTHER", confidence=0.6)


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
