from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from apps.integrations.contracts import (
    AuthenticationError,
    CostLimitError,
    ExtractedBusiness,
    ExtractedEmail,
    ExtractionBatch,
    PermanentProviderError,
    RateLimitError,
    RetryableProviderError,
    SearchRequest,
    ValidationProviderError,
)

OUTSCRAPER_SCHEMA_VERSION = "google-maps-search-2026-07"
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
OFFICIAL_OUTSCRAPER_HOSTS = frozenset({"api.outscraper.cloud", "api.outscraper.com"})


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


@dataclass(frozen=True, slots=True)
class HTTPResponse:
    status_code: int
    payload: dict[str, Any]
    headers: Mapping[str, str]


class HTTPTransport(Protocol):
    def get(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        params: Sequence[tuple[str, str]],
        timeout_seconds: float,
    ) -> HTTPResponse: ...


class UrllibHTTPTransport:
    """Small injectable JSON transport; credentials are sent only in headers."""

    def get(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        params: Sequence[tuple[str, str]],
        timeout_seconds: float,
    ) -> HTTPResponse:
        request_url = f"{url}?{urlencode(params)}" if params else url
        request = Request(request_url, headers=dict(headers), method="GET")
        opener = build_opener(NoRedirectHandler())
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise PermanentProviderError("Outscraper devolvió un payload demasiado grande.")
                return HTTPResponse(
                    status_code=response.status,
                    payload=_decode_json(body) if body else {},
                    headers=dict(response.headers.items()),
                )
        except HTTPError as exc:
            body = exc.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                body = b""
            return HTTPResponse(
                status_code=exc.code,
                payload=_decode_json(body) if body else {},
                headers=dict(exc.headers.items()) if exc.headers else {},
            )
        except (TimeoutError, URLError) as exc:
            raise RetryableProviderError("Outscraper no respondió dentro del plazo.") from exc


def _decode_json(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetryableProviderError("Outscraper devolvió una respuesta JSON inválida.") from exc
    if not isinstance(payload, dict):
        raise RetryableProviderError("Outscraper devolvió un payload inesperado.")
    return payload


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _usage(
    payload: Mapping[str, Any],
) -> tuple[
    Decimal | None,
    Decimal | None,
    Decimal | None,
    str | None,
    dict[str, Any],
]:
    usage = payload.get("usage")
    billing = payload.get("billing")
    usage_map = usage if isinstance(usage, Mapping) else {}
    billing_map = billing if isinstance(billing, Mapping) else {}
    units = _decimal(usage_map.get("units") or usage_map.get("records") or payload.get("units"))
    estimated = _decimal(
        billing_map.get("estimated_cost")
        or usage_map.get("estimated_cost")
        or payload.get("estimated_cost")
    )
    actual = _decimal(
        billing_map.get("cost")
        or billing_map.get("actual_cost")
        or usage_map.get("cost")
        or payload.get("cost")
    )
    currency_value = billing_map.get("currency") or usage_map.get("currency")
    currency = str(currency_value).upper()[:3] if currency_value else None
    metadata = {
        "schema_version": OUTSCRAPER_SCHEMA_VERSION,
        "provider_usage_available": any(value is not None for value in (units, estimated, actual)),
    }
    return units, estimated, actual, currency, metadata


class OutscraperProvider:
    """Adapter for Outscraper's official asynchronous Google Maps Search API."""

    def __init__(
        self,
        *,
        api_key: str,
        transport: HTTPTransport | None = None,
        base_url: str = "https://api.outscraper.cloud",
    ) -> None:
        if not api_key.strip():
            raise AuthenticationError("OUTSCRAPER_API_KEY no está configurada.")
        normalized_base = base_url.rstrip("/")
        if not normalized_base.startswith("https://"):
            raise ValidationProviderError("La URL base de Outscraper debe usar HTTPS.")
        if urlsplit(normalized_base).hostname not in OFFICIAL_OUTSCRAPER_HOSTS:
            raise ValidationProviderError(
                "La URL base no pertenece a la API oficial de Outscraper."
            )
        self._api_key = api_key.strip()
        self._transport = transport or UrllibHTTPTransport()
        self._base_url = normalized_base

    @property
    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "X-API-KEY": self._api_key}

    def submit(self, request: SearchRequest) -> ExtractionBatch:
        response = self._transport.get(
            url=f"{self._base_url}/google-maps-search",
            headers=self._headers,
            params=(
                ("query", request.query),
                ("limit", str(request.limit)),
                ("async", "true"),
                ("enrichment", "contacts_n_leads"),
                ("language", "es-419"),
                ("region", "AR"),
                ("dropDuplicates", "false"),
            ),
            timeout_seconds=request.timeout_seconds,
        )
        return self._batch_from_response(response)

    def extract(self, request: SearchRequest) -> ExtractionBatch:
        """Compatibility entry point; async responses contain no records until polled."""
        return self.submit(request)

    def poll(self, *, request_id: str, timeout_seconds: float = 30.0) -> ExtractionBatch:
        if not request_id.strip():
            raise ValidationProviderError("Falta el identificador del trabajo de Outscraper.")
        response = self._transport.get(
            url=f"{self._base_url}/requests/{quote(request_id, safe='')}",
            headers=self._headers,
            params=(),
            timeout_seconds=timeout_seconds,
        )
        return self._batch_from_response(response, fallback_request_id=request_id)

    def _batch_from_response(
        self, response: HTTPResponse, *, fallback_request_id: str = ""
    ) -> ExtractionBatch:
        self._raise_for_status(response)
        payload = response.payload
        request_id = str(payload.get("id") or fallback_request_id)
        provider_status = str(payload.get("status", "")).casefold()
        if response.status_code == 202 or provider_status == "pending":
            status = "PENDING"
        elif response.status_code == 204 or provider_status == "failure":
            status = "FAILED"
        elif response.status_code == 200 and provider_status == "success":
            status = "SUCCEEDED"
        else:
            raise RetryableProviderError("Outscraper devolvió un estado no reconocido.")
        if not request_id and status != "FAILED":
            raise RetryableProviderError("Outscraper no devolvió un identificador de trabajo.")
        units, estimated, actual, currency, metadata = _usage(payload)
        records = self.parse_response(payload) if status == "SUCCEEDED" else ()
        return ExtractionBatch(
            status=status,
            request_id=request_id,
            raw_payload=payload,
            units=units,
            estimated_cost=estimated,
            actual_cost=actual,
            currency=currency,
            usage_metadata=metadata,
            records=records,
        )

    def _raise_for_status(self, response: HTTPResponse) -> None:
        status = response.status_code
        message = _safe_error_message(response.payload)
        if status == 401:
            raise AuthenticationError(message or "Outscraper rechazó la credencial.")
        if status == 402:
            raise CostLimitError(message or "Outscraper rechazó la cuenta o el medio de pago.")
        if status == 403:
            raise AuthenticationError(message or "Outscraper prohibió la solicitud.")
        if status == 429:
            raise RateLimitError(
                message or "Outscraper limitó temporalmente las solicitudes.",
                retry_after=_retry_after(response.headers),
            )
        if status in {408, 425} or status >= 500:
            raise RetryableProviderError(message or f"Outscraper respondió HTTP {status}.")
        if status == 422:
            raise ValidationProviderError(message or "Outscraper rechazó los parámetros.")
        if status >= 400:
            raise PermanentProviderError(message or f"Outscraper respondió HTTP {status}.")

    def parse_response(self, raw_payload: dict[str, Any]) -> tuple[ExtractedBusiness, ...]:
        if str(raw_payload.get("status", "")).casefold() != "success":
            return ()
        return tuple(_map_business(row) for row in _rows(raw_payload))


def _retry_after(headers: Mapping[str, str]) -> float | None:
    value = next((item for key, item in headers.items() if key.casefold() == "retry-after"), None)
    try:
        return max(0.0, float(value)) if value is not None else None
    except ValueError:
        return None


def _safe_error_message(payload: Mapping[str, Any]) -> str:
    value = payload.get("errorMessage") or payload.get("message") or payload.get("error")
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:500]


def _rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict):
            rows.append(item)
        elif isinstance(item, list):
            rows.extend(row for row in item if isinstance(row, dict))
    return rows


def _email_values(row: Mapping[str, Any]) -> tuple[ExtractedEmail, ...]:
    found: list[ExtractedEmail] = []
    seen: set[str] = set()

    def add(value: object, *, source: str, primary: bool = False) -> None:
        if not isinstance(value, str) or "@" not in value:
            return
        key = value.strip().casefold()
        if not key or key in seen:
            return
        seen.add(key)
        found.append(
            ExtractedEmail(value=value.strip(), source=source, is_primary=primary, order=len(found))
        )

    add(row.get("email"), source="email", primary=True)
    for key, value in row.items():
        if key.startswith("email_"):
            add(value, source=key)
    for key in ("emails", "business_emails"):
        values = row.get(key)
        if isinstance(values, list):
            for value in values:
                if isinstance(value, Mapping):
                    add(
                        value.get("value") or value.get("email"),
                        source=str(value.get("source") or key),
                        primary=bool(value.get("primary") or value.get("is_primary")),
                    )
                else:
                    add(value, source=key)
    contacts = row.get("contacts")
    if isinstance(contacts, list):
        for contact in contacts:
            if not isinstance(contact, Mapping):
                continue
            emails = contact.get("emails")
            if isinstance(emails, list):
                for value in emails:
                    if isinstance(value, Mapping):
                        add(
                            value.get("value") or value.get("email"),
                            source=str(value.get("source") or "contacts"),
                        )
                    else:
                        add(value, source="contacts")
    for key in ("contacts_n_leads", "enrichment"):
        nested = row.get(key)
        if isinstance(nested, Mapping):
            for email in _email_values(nested):
                add(email.value, source=f"{key}:{email.source}", primary=email.is_primary)
    return tuple(found)


def _coordinate(value: object) -> Decimal | None:
    coordinate = _decimal(value)
    return coordinate if coordinate is not None and coordinate.is_finite() else None


def _website(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    website = value.strip()
    return website if "://" in website else f"https://{website}"


def _map_business(row: Mapping[str, Any]) -> ExtractedBusiness:
    provider_id = row.get("place_id") or row.get("google_id") or row.get("cid") or row.get("os_id")
    if not provider_id:
        address = row.get("full_address", row.get("address", ""))
        identity_source = f"{row.get('name', '')}\x1f{address}"
        provider_id = f"unknown-{hashlib.sha256(identity_source.encode()).hexdigest()[:24]}"
    selected = {
        key: row.get(key)
        for key in ("place_id", "google_id", "cid", "os_id", "type", "subtypes", "description")
        if row.get(key) is not None
    }
    selected["schema_version"] = OUTSCRAPER_SCHEMA_VERSION
    return ExtractedBusiness(
        provider_id=str(provider_id),
        name=str(row.get("name") or "").strip(),
        address=str(row.get("full_address") or row.get("address") or "").strip(),
        email_candidates=_email_values(row),
        website=_website(row.get("site") or row.get("website")),
        phone=str(row.get("phone") or "").strip() or None,
        category=str(row.get("category") or row.get("type") or "").strip() or None,
        latitude=_coordinate(row.get("latitude")),
        longitude=_coordinate(row.get("longitude")),
        provider_data=selected,
    )
