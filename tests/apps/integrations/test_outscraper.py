from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from apps.integrations.contracts import (
    AuthenticationError,
    RateLimitError,
    SearchRequest,
    ValidationProviderError,
)
from apps.integrations.outscraper import HTTPResponse, OutscraperProvider


class StubTransport:
    def __init__(self, *responses: HTTPResponse) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def get(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        params: Sequence[tuple[str, str]],
        timeout_seconds: float,
    ) -> HTTPResponse:
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "params": tuple(params),
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.responses.pop(0)


def response(status_code: int, payload: dict[str, Any], **headers: str) -> HTTPResponse:
    return HTTPResponse(status_code=status_code, payload=payload, headers=headers)


def request() -> SearchRequest:
    return SearchRequest(
        query="talleres electromecánicos, Palermo, CABA",
        correlation_id="correlation",
        idempotency_key="stable-key",
        limit=17,
    )


def test_submit_uses_official_async_contract_and_header_authentication() -> None:
    transport = StubTransport(
        response(
            202,
            {
                "id": "request-123",
                "status": "Pending",
                "results_location": "https://api.outscraper.cloud/requests/request-123",
            },
        )
    )
    provider = OutscraperProvider(api_key="secret-token", transport=transport)

    batch = provider.submit(request())

    assert batch.status == "PENDING"
    assert batch.request_id == "request-123"
    call = transport.calls[0]
    assert call["url"] == "https://api.outscraper.cloud/google-maps-search"
    assert call["headers"] == {"Accept": "application/json", "X-API-KEY": "secret-token"}
    params = call["params"]
    assert ("async", "true") in params
    assert ("enrichment", "contacts_n_leads") in params
    assert ("limit", "17") in params
    assert not any(key == "apiKey" for key, _ in params)


def test_poll_maps_nested_results_and_provider_usage_without_leaking_unknown_fields() -> None:
    payload = {
        "id": "request-123",
        "status": "Success",
        "usage": {"records": 2, "estimated_cost": "0.006", "currency": "usd"},
        "data": [
            [
                {
                    "place_id": "place-1",
                    "name": "Motores Sur",
                    "full_address": "Av. Siempre Viva 123, CABA",
                    "site": "https://motores.example.com.ar",
                    "phone": "+54 11 5555 5555",
                    "category": "Taller electromecánico",
                    "latitude": -34.6,
                    "longitude": -58.4,
                    "email": "info@motores.example.com.ar",
                    "contacts_n_leads": {
                        "contacts": [
                            {
                                "emails": [
                                    {"value": "ventas@motores.example.com.ar", "source": "web"}
                                ]
                            }
                        ]
                    },
                    "unrecognized_future_field": {"must": "stay raw only"},
                },
                {
                    "google_id": "google-2",
                    "name": "Sin correo",
                    "address": "CABA",
                },
            ]
        ],
    }
    transport = StubTransport(response(200, payload))
    provider = OutscraperProvider(api_key="secret-token", transport=transport)

    batch = provider.poll(request_id="request-123")

    assert batch.status == "SUCCEEDED"
    assert batch.raw_payload == payload
    assert batch.units is not None and str(batch.units) == "2"
    assert batch.estimated_cost is not None and str(batch.estimated_cost) == "0.006"
    assert batch.currency == "USD"
    assert len(batch.records) == 2
    first = batch.records[0]
    assert first.provider_id == "place-1"
    assert [candidate.value for candidate in first.email_candidates] == [
        "info@motores.example.com.ar",
        "ventas@motores.example.com.ar",
    ]
    assert "unrecognized_future_field" not in (first.provider_data or {})
    assert transport.calls[0]["url"] == "https://api.outscraper.cloud/requests/request-123"


def test_provider_translates_authentication_and_rate_limit_errors() -> None:
    auth = OutscraperProvider(
        api_key="bad", transport=StubTransport(response(401, {"errorMessage": "unauthorized"}))
    )
    with pytest.raises(AuthenticationError, match="unauthorized"):
        auth.submit(request())

    limited = OutscraperProvider(
        api_key="key",
        transport=StubTransport(
            response(429, {"errorMessage": "slow down"}, **{"Retry-After": "12"})
        ),
    )
    with pytest.raises(RateLimitError) as error:
        limited.submit(request())
    assert error.value.retry_after == 12

    failed = OutscraperProvider(api_key="key", transport=StubTransport(response(204, {})))
    batch = failed.submit(request())
    assert batch.status == "FAILED"
    assert batch.request_id == ""


def test_provider_rejects_missing_key_and_insecure_base_url() -> None:
    with pytest.raises(AuthenticationError):
        OutscraperProvider(api_key="")
    with pytest.raises(ValidationProviderError, match="HTTPS"):
        OutscraperProvider(api_key="key", base_url="http://example.test")
    with pytest.raises(ValidationProviderError, match="oficial"):
        OutscraperProvider(api_key="key", base_url="https://example.test")
