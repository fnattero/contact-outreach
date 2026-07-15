from __future__ import annotations

import base64
import json
from dataclasses import dataclass
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
        return GmailAccountInfo(email=email)

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
        del cursor
        raise PermanentProviderError("La sincronización de respuestas pertenece a la fase 10.")
