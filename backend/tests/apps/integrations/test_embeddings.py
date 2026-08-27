from __future__ import annotations

from typing import Any

import pytest

from apps.integrations.contracts import EmbeddingRequest, JSONResponse, ValidationProviderError
from apps.integrations.embeddings import (
    FakeEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    cosine_similarity,
)


class RecordingTransport:
    def __init__(self, response: dict[str, Any], *, status_code: int = 200) -> None:
        self.response = response
        self.status_code = status_code
        self.calls: list[dict[str, Any]] = []

    def post_json(
        self,
        *,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> JSONResponse:
        self.calls.append(
            {
                "url": url,
                "payload": payload,
                "headers": headers,
                "timeout": timeout_seconds,
            }
        )
        return JSONResponse(status_code=self.status_code, payload=self.response)


def test_fake_embeddings_are_deterministic_and_normalized() -> None:
    provider = FakeEmbeddingProvider(dimensions=128)
    request = EmbeddingRequest(
        texts=("carbones para motores", "carbones para motores", "agenda reunión"),
        correlation_id="correlation",
        idempotency_key="embedding-key",
        model="fake-embedding",
        dimensions=128,
    )

    result = provider.embed(request)

    assert result.vectors[0] == result.vectors[1]
    assert cosine_similarity(result.vectors[0], result.vectors[1]) == pytest.approx(1.0)
    assert cosine_similarity(result.vectors[0], result.vectors[2]) < 1.0


def test_openai_compatible_embeddings_call_embeddings_endpoint() -> None:
    vector = [1.0] + [0.0] * 127
    transport = RecordingTransport(
        {
            "model": "text-embedding-3-small",
            "data": [
                {"object": "embedding", "index": 0, "embedding": vector},
                {"object": "embedding", "index": 1, "embedding": [0.0, 1.0] + [0.0] * 126},
            ],
        }
    )
    provider = OpenAICompatibleEmbeddingProvider(
        base_url="https://api.openai.example/v1",
        api_key="secret",
        model="text-embedding-3-small",
        dimensions=128,
        transport=transport,
    )

    result = provider.embed(
        EmbeddingRequest(
            texts=("consulta uno", "consulta dos"),
            correlation_id="correlation",
            idempotency_key="embedding-key",
            model="text-embedding-3-small",
            dimensions=128,
        )
    )

    assert len(result.vectors) == 2
    assert transport.calls == [
        {
            "url": "https://api.openai.example/v1/embeddings",
            "payload": {
                "model": "text-embedding-3-small",
                "input": ["consulta uno", "consulta dos"],
                "encoding_format": "float",
                "dimensions": 128,
            },
            "headers": {"Authorization": "Bearer secret"},
            "timeout": 20.0,
        }
    ]


def test_openai_compatible_embeddings_reject_unexpected_dimensions() -> None:
    transport = RecordingTransport(
        {"data": [{"object": "embedding", "index": 0, "embedding": [1.0, 0.0]}]}
    )
    provider = OpenAICompatibleEmbeddingProvider(
        base_url="https://api.openai.example/v1",
        api_key="secret",
        model="text-embedding-3-small",
        dimensions=128,
        transport=transport,
    )

    with pytest.raises(ValidationProviderError, match="dimensión"):
        provider.embed(
            EmbeddingRequest(
                texts=("consulta",),
                correlation_id="correlation",
                idempotency_key="embedding-key",
                model="text-embedding-3-small",
                dimensions=128,
            )
        )
