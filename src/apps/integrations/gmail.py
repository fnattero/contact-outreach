from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from apps.integrations.contracts import (
    AmbiguousProviderError,
    AuthenticationError,
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
    PermanentProviderError,
    RateLimitError,
    RetryableProviderError,
    ValidationProviderError,
)

GMAIL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
)
AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
FALLBACK_QUERY = "newer_than:30d"
MAX_SYNC_CANDIDATES = 1000
SYNC_PAGE_SIZE = 500
ALLOWED_INBOUND_HEADERS = frozenset(
    {
        "auto-submitted",
        "content-type",
        "date",
        "from",
        "in-reply-to",
        "message-id",
        "precedence",
        "references",
        "return-path",
        "subject",
        "to",
        "x-auto-response-suppress",
        "x-autoreply",
    }
)


@dataclass(frozen=True, slots=True)
class HTTPResponse:
    status_code: int
    payload: dict[str, Any]


class GmailHTTPTransport:
    def request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        form: dict[str, str] | None = None,
        json_payload: dict[str, Any] | None = None,
        timeout_seconds: float = 20.0,
    ) -> HTTPResponse:
        body: bytes | None = None
        request_headers = dict(headers or {})
        if form is not None:
            body = urlencode(form).encode("ascii")
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif json_payload is not None:
            body = json.dumps(json_payload).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = Request(url, data=body, headers=request_headers, method=method)
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                response_body = response.read()
                payload = json.loads(response_body.decode("utf-8")) if response_body.strip() else {}
                return HTTPResponse(status_code=response.status, payload=payload)
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                payload = {}
            self._raise_http(exc.code, payload, exc.headers.get("Retry-After"))
        except TimeoutError as exc:
            raise AmbiguousProviderError(
                "Gmail no confirmó el resultado antes del timeout."
            ) from exc
        except URLError as exc:
            raise RetryableProviderError("No se pudo conectar con Gmail.") from exc
        raise AssertionError("unreachable")

    @staticmethod
    def _raise_http(status: int, payload: dict[str, Any], retry_after: str | None) -> None:
        message = str(payload.get("error", {}).get("message", "Error Gmail"))[:300]
        if status in {401, 403}:
            if status == 401:
                raise AuthenticationError(message)
            raise RateLimitError(message, retry_after=_parse_retry_after(retry_after))
        if status == 429:
            raise RateLimitError(message, retry_after=_parse_retry_after(retry_after))
        if status >= 500:
            raise RetryableProviderError(message)
        if status == 404:
            raise GmailHistoryExpired(message)
        if status == 400:
            raise ValidationProviderError(message)
        raise PermanentProviderError(message)


def _parse_retry_after(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


class GmailAPIProvider(GmailProvider):
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str = "",
        code_verifier: str = "",
        code_challenge: str = "",
        transport: GmailHTTPTransport | None = None,
    ) -> None:
        if not client_id or not client_secret:
            raise AuthenticationError("Faltan GMAIL_OAUTH_CLIENT_ID/CLIENT_SECRET.")
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.code_verifier = code_verifier
        self.code_challenge = code_challenge
        self.transport = transport or GmailHTTPTransport()
        self._access_token = ""

    def authorization_url(self, state: str, redirect_uri: str) -> str:
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(GMAIL_SCOPES),
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "false",
                "state": state,
                "code_challenge": self.code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{AUTHORIZATION_ENDPOINT}?{query}"

    def exchange_code(self, code: str, redirect_uri: str) -> GmailConnectionData:
        response = self.transport.request(
            method="POST",
            url=TOKEN_ENDPOINT,
            form={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": self.code_verifier,
            },
        )
        refresh_token = str(response.payload.get("refresh_token", ""))
        self.refresh_token = refresh_token
        self._access_token = str(response.payload.get("access_token", ""))
        scopes = tuple(str(response.payload.get("scope", "")).split())
        profile = self.test_connection()
        return GmailConnectionData(
            email=profile.email,
            refresh_token=refresh_token,
            scopes=scopes,
            history_id=profile.history_id,
        )

    def _token(self) -> str:
        if self._access_token:
            return self._access_token
        if not self.refresh_token:
            raise AuthenticationError("La conexión Gmail no tiene refresh token.")
        response = self.transport.request(
            method="POST",
            url=TOKEN_ENDPOINT,
            form={
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
            },
        )
        self._access_token = str(response.payload.get("access_token", ""))
        if not self._access_token:
            raise AuthenticationError("Google no devolvió un access token.")
        return self._access_token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}"}

    def revoke(self) -> None:
        if not self.refresh_token:
            return
        self.transport.request(
            method="POST",
            url=REVOKE_ENDPOINT,
            form={"token": self.refresh_token},
        )

    def test_connection(self) -> GmailAccountInfo:
        response = self.transport.request(
            method="GET",
            url=f"{GMAIL_API}/users/me/profile",
            headers=self._headers(),
        )
        email = str(response.payload.get("emailAddress", ""))
        if not email:
            raise ValidationProviderError("Gmail no devolvió la dirección de la cuenta.")
        return GmailAccountInfo(
            email=email,
            history_id=str(response.payload.get("historyId", "")),
        )

    def _send(self, raw_message: bytes, *, thread_id: str = "") -> GmailSendResult:
        payload: dict[str, Any] = {
            "raw": base64.urlsafe_b64encode(raw_message).decode("ascii").rstrip("=")
        }
        if thread_id:
            payload["threadId"] = thread_id
        response = self.transport.request(
            method="POST",
            url=f"{GMAIL_API}/users/me/messages/send",
            headers=self._headers(),
            json_payload=payload,
        )
        message_id = str(response.payload.get("id", ""))
        result_thread = str(response.payload.get("threadId", ""))
        if not message_id or not result_thread:
            raise ValidationProviderError("Gmail no devolvió IDs del mensaje enviado.")
        return GmailSendResult(message_id=message_id, thread_id=result_thread)

    def send(self, request: GmailSendRequest) -> GmailSendResult:
        return self._send(request.raw_message)

    def reply(self, request: GmailReplyRequest) -> GmailSendResult:
        return self._send(request.raw_message, thread_id=request.thread_id)

    def find_by_message_id(self, message_id: str) -> GmailSendResult | None:
        query = quote(f"rfc822msgid:{message_id}")
        response = self.transport.request(
            method="GET",
            url=f"{GMAIL_API}/users/me/messages?q={query}&maxResults=2",
            headers=self._headers(),
        )
        messages = response.payload.get("messages", [])
        if not isinstance(messages, list) or not messages:
            return None
        item = messages[0]
        if not isinstance(item, dict) or not item.get("id") or not item.get("threadId"):
            raise ValidationProviderError("Respuesta de reconciliación Gmail inválida.")
        return GmailSendResult(message_id=str(item["id"]), thread_id=str(item["threadId"]))

    def sync(self, cursor: GmailCursor | None) -> GmailSyncBatch:
        if cursor is None:
            profile = self.test_connection()
            if not profile.history_id:
                raise ValidationProviderError("Gmail no devolvió historyId para el fallback.")
            message_ids = self._fallback_message_ids()
            next_cursor = GmailCursor(history_id=profile.history_id)
            used_fallback = True
        else:
            message_ids, history_id = self._incremental_message_ids(cursor)
            next_cursor = GmailCursor(history_id=history_id)
            used_fallback = False
        messages = tuple(self._get_message(message_id) for message_id in message_ids)
        return GmailSyncBatch(
            messages=messages,
            next_cursor=next_cursor,
            used_fallback=used_fallback,
        )

    def _incremental_message_ids(self, cursor: GmailCursor) -> tuple[tuple[str, ...], str]:
        page_token = ""
        latest_history_id = cursor.history_id
        seen: set[str] = set()
        message_ids: list[str] = []
        while True:
            query = {
                "startHistoryId": cursor.history_id,
                "historyTypes": "messageAdded",
                "maxResults": str(SYNC_PAGE_SIZE),
            }
            if page_token:
                query["pageToken"] = page_token
            response = self.transport.request(
                method="GET",
                url=f"{GMAIL_API}/users/me/history?{urlencode(query)}",
                headers=self._headers(),
            )
            latest_history_id = str(response.payload.get("historyId", latest_history_id))
            history = response.payload.get("history", [])
            if not isinstance(history, list):
                raise ValidationProviderError("Gmail devolvió un historial inválido.")
            for event in history:
                if not isinstance(event, dict):
                    continue
                additions = event.get("messagesAdded", [])
                if not isinstance(additions, list):
                    continue
                for addition in additions:
                    item = addition.get("message") if isinstance(addition, dict) else None
                    message_id = str(item.get("id", "")) if isinstance(item, dict) else ""
                    if message_id and message_id not in seen:
                        seen.add(message_id)
                        message_ids.append(message_id)
            page_token = str(response.payload.get("nextPageToken", ""))
            if not page_token:
                return tuple(message_ids), latest_history_id

    def _fallback_message_ids(self) -> tuple[str, ...]:
        page_token = ""
        message_ids: list[str] = []
        while len(message_ids) < MAX_SYNC_CANDIDATES:
            query = {"q": FALLBACK_QUERY, "maxResults": str(SYNC_PAGE_SIZE)}
            if page_token:
                query["pageToken"] = page_token
            response = self.transport.request(
                method="GET",
                url=f"{GMAIL_API}/users/me/messages?{urlencode(query)}",
                headers=self._headers(),
            )
            items = response.payload.get("messages", [])
            if not isinstance(items, list):
                raise ValidationProviderError("Gmail devolvió candidatos inválidos.")
            for item in items:
                message_id = str(item.get("id", "")) if isinstance(item, dict) else ""
                if message_id:
                    message_ids.append(message_id)
                    if len(message_ids) >= MAX_SYNC_CANDIDATES:
                        break
            page_token = str(response.payload.get("nextPageToken", ""))
            if not page_token:
                break
        return tuple(message_ids)

    def _get_message(self, message_id: str) -> GmailInboundMessage:
        response = self.transport.request(
            method="GET",
            url=f"{GMAIL_API}/users/me/messages/{quote(message_id)}?format=full",
            headers=self._headers(),
        )
        payload = response.payload.get("payload", {})
        if not isinstance(payload, dict):
            raise ValidationProviderError("Gmail devolvió un mensaje sin payload válido.")
        headers = _allowed_headers(payload.get("headers", []))
        text_parts, html_parts = _message_bodies(
            payload,
            detached_loader=lambda attachment_id: self._get_text_attachment(
                message_id,
                attachment_id,
            ),
        )
        try:
            received_at = datetime.fromtimestamp(
                int(str(response.payload.get("internalDate", "0"))) / 1000,
                tz=UTC,
            )
        except (ValueError, OSError, OverflowError) as exc:
            raise ValidationProviderError("Gmail devolvió una fecha de mensaje inválida.") from exc
        gmail_message_id = str(response.payload.get("id", ""))
        thread_id = str(response.payload.get("threadId", ""))
        if not gmail_message_id or not thread_id:
            raise ValidationProviderError("Gmail devolvió un mensaje sin IDs.")
        return GmailInboundMessage(
            message_id=gmail_message_id,
            thread_id=thread_id,
            rfc_message_id=headers.get("Message-ID", ""),
            in_reply_to=headers.get("In-Reply-To", ""),
            references=tuple(headers.get("References", "").split()),
            sender=headers.get("From", ""),
            recipients=tuple(
                item.strip() for item in headers.get("To", "").split(",") if item.strip()
            ),
            subject=headers.get("Subject", ""),
            body_text="\n".join(text_parts),
            body_html="\n".join(html_parts),
            received_at=received_at,
            headers=headers,
        )

    def _get_text_attachment(self, message_id: str, attachment_id: str) -> str:
        response = self.transport.request(
            method="GET",
            url=(
                f"{GMAIL_API}/users/me/messages/{quote(message_id)}/attachments/"
                f"{quote(attachment_id)}"
            ),
            headers=self._headers(),
        )
        return _decode_body(response.payload.get("data"))


def _decode_body(data: object) -> str:
    if not isinstance(data, str) or not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


def _message_bodies(
    payload: dict[str, Any],
    *,
    detached_loader: Callable[[str], str] | None = None,
) -> tuple[list[str], list[str]]:
    text_parts: list[str] = []
    html_parts: list[str] = []

    def visit(part: dict[str, Any]) -> None:
        if str(part.get("filename", "")).strip():
            return
        mime_type = str(part.get("mimeType", "")).casefold()
        body = part.get("body", {})
        data = body.get("data") if isinstance(body, dict) else None
        decoded = _decode_body(data)
        attachment_id = str(body.get("attachmentId", "")) if isinstance(body, dict) else ""
        if (
            not decoded
            and attachment_id
            and detached_loader is not None
            and mime_type in {"text/plain", "text/html"}
        ):
            decoded = detached_loader(attachment_id)
        if decoded and mime_type == "text/plain":
            text_parts.append(decoded)
        elif decoded and mime_type == "text/html":
            html_parts.append(decoded)
        children = part.get("parts", [])
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    visit(child)

    visit(payload)
    return text_parts, html_parts


def _allowed_headers(raw_headers: object) -> dict[str, str]:
    result: dict[str, str] = {}
    if not isinstance(raw_headers, list):
        return result
    for item in raw_headers:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if name.casefold() not in ALLOWED_INBOUND_HEADERS:
            continue
        canonical = "-".join(part.capitalize() for part in name.split("-"))
        if canonical.casefold() == "message-id":
            canonical = "Message-ID"
        result[canonical] = str(item.get("value", ""))[:4000]
    return result
