from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol


class ProviderError(RuntimeError):
    """Base exception translated by domain services at provider boundaries."""


class RetryableProviderError(ProviderError):
    pass


class AmbiguousProviderError(RetryableProviderError):
    """The provider may have accepted the effect; reconciliation is mandatory."""


class RateLimitError(RetryableProviderError):
    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class AuthenticationError(ProviderError):
    pass


class ValidationProviderError(ProviderError):
    pass


class CostLimitError(ProviderError):
    pass


class PermanentProviderError(ProviderError):
    pass


class GmailHistoryExpired(PermanentProviderError):
    """The incremental Gmail cursor is no longer accepted by Gmail."""


@dataclass(frozen=True, slots=True)
class SearchRequest:
    query: str
    correlation_id: str
    idempotency_key: str
    limit: int = 20
    timeout_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class ExtractedEmail:
    value: str
    source: str = "provider"
    is_primary: bool = False
    order: int = 0


@dataclass(frozen=True, slots=True)
class ExtractedBusiness:
    provider_id: str
    name: str
    address: str
    email_candidates: tuple[ExtractedEmail, ...]
    website: str | None = None
    phone: str | None = None
    category: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    provider_data: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ExtractionBatch:
    status: str
    request_id: str
    raw_payload: dict[str, Any]
    units: Decimal | None = None
    estimated_cost: Decimal | None = None
    actual_cost: Decimal | None = None
    currency: str | None = None
    usage_metadata: dict[str, Any] | None = None
    records: tuple[ExtractedBusiness, ...] = ()


@dataclass(frozen=True, slots=True)
class WebsiteRequest:
    url: str
    correlation_id: str
    timeout_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class WebsitePage:
    requested_url: str
    final_url: str
    status_code: int
    text: str
    content_type: str = "text/plain"
    content_hash: str = ""
    byte_count: int = 0


@dataclass(frozen=True, slots=True)
class WebsiteResult:
    pages: tuple[WebsitePage, ...]
    error: str | None = None
    error_kind: str | None = None


class WebsiteErrorKind(StrEnum):
    REJECTED = "REJECTED"
    FETCH_ERROR = "FETCH_ERROR"


@dataclass(frozen=True, slots=True)
class AnalysisFact:
    fact_id: str
    value: str


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    facts: tuple[AnalysisFact, ...]
    correlation_id: str
    idempotency_key: str
    system_prompt: str = ""
    user_prompt: str = ""
    json_schema: dict[str, Any] | None = None
    timeout_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class AIAnalysisResult:
    relevance_score: int
    confidence: float
    relevance_reason: str
    evidence: tuple[str, ...]
    subject: str
    body_text: str


@dataclass(frozen=True, slots=True)
class JSONResponse:
    status_code: int
    payload: dict[str, Any]


class JSONTransport(Protocol):
    def post_json(
        self,
        *,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> JSONResponse: ...


@dataclass(frozen=True, slots=True)
class ReplyClassificationRequest:
    body_text: str
    correlation_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ReplyClassification:
    classification: str
    confidence: float


@dataclass(frozen=True, slots=True)
class GmailConnectionData:
    email: str
    refresh_token: str
    scopes: tuple[str, ...]
    history_id: str = ""


@dataclass(frozen=True, slots=True)
class GmailAccountInfo:
    email: str
    history_id: str = ""


@dataclass(frozen=True, slots=True)
class GmailSendRequest:
    recipient: str
    raw_message: bytes
    message_id: str
    correlation_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class GmailReplyRequest:
    recipient: str
    raw_message: bytes
    message_id: str
    thread_id: str
    correlation_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class GmailSendResult:
    message_id: str
    thread_id: str


@dataclass(frozen=True, slots=True)
class GmailCursor:
    history_id: str


@dataclass(frozen=True, slots=True)
class GmailInboundMessage:
    message_id: str
    thread_id: str
    rfc_message_id: str
    in_reply_to: str
    references: tuple[str, ...]
    sender: str
    recipients: tuple[str, ...]
    subject: str
    body_text: str
    body_html: str
    received_at: datetime
    headers: dict[str, str]


@dataclass(frozen=True, slots=True)
class GmailSyncBatch:
    messages: tuple[GmailInboundMessage, ...]
    next_cursor: GmailCursor
    used_fallback: bool = False


class ExtractorProvider(Protocol):
    def extract(self, request: SearchRequest) -> ExtractionBatch: ...

    def submit(self, request: SearchRequest) -> ExtractionBatch: ...

    def poll(self, *, request_id: str, timeout_seconds: float = 30.0) -> ExtractionBatch: ...

    def parse_response(self, raw_payload: dict[str, Any]) -> tuple[ExtractedBusiness, ...]: ...


class WebsiteFetcher(Protocol):
    def fetch(self, request: WebsiteRequest) -> WebsiteResult: ...


class LLMProvider(Protocol):
    def analyze(self, request: AnalysisRequest) -> AIAnalysisResult: ...

    def classify_reply(self, request: ReplyClassificationRequest) -> ReplyClassification: ...


class GmailProvider(Protocol):
    def authorization_url(self, state: str, redirect_uri: str) -> str: ...

    def exchange_code(self, code: str, redirect_uri: str) -> GmailConnectionData: ...

    def revoke(self) -> None: ...

    def test_connection(self) -> GmailAccountInfo: ...

    def send(self, request: GmailSendRequest) -> GmailSendResult: ...

    def find_by_message_id(self, message_id: str) -> GmailSendResult | None: ...

    def reply(self, request: GmailReplyRequest) -> GmailSendResult: ...

    def sync(self, cursor: GmailCursor | None) -> GmailSyncBatch: ...
