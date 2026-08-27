from __future__ import annotations

import math
import re
import unicodedata
from hashlib import sha256
from typing import Any

from apps.integrations.contracts import (
    AuthenticationError,
    EmbeddingRequest,
    EmbeddingResult,
    JSONResponse,
    JSONTransport,
    RateLimitError,
    RetryableProviderError,
    ValidationProviderError,
)
from apps.integrations.llm import UrllibJSONTransport, validate_llm_base_url

_WORDS = re.compile(r"[\wáéíóúüñ]{3,}", re.IGNORECASE)


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return " ".join(_WORDS.findall(without_marks))


def normalize_vector(vector: list[float] | tuple[float, ...]) -> tuple[float, ...]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return tuple(float(value) for value in vector)
    return tuple(float(value) / norm for value in vector)


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise ValueError("Los vectores de embeddings tienen dimensiones distintas.")
    return sum(a * b for a, b in zip(left, right, strict=True))


class FakeEmbeddingProvider:
    """Local deterministic lexical embeddings for tests and dry-run development."""

    def __init__(self, *, dimensions: int = 1536) -> None:
        if not 64 <= dimensions <= 3072:
            raise ValueError("La dimensión de embeddings debe estar entre 64 y 3072.")
        self.dimensions = dimensions
        self.requests: list[EmbeddingRequest] = []

    def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        self.requests.append(request)
        dimensions = request.dimensions or self.dimensions
        vectors = tuple(self._embed_one(text, dimensions=dimensions) for text in request.texts)
        return EmbeddingResult(vectors=vectors, model=request.model, dimensions=dimensions)

    @staticmethod
    def _embed_one(text: str, *, dimensions: int) -> tuple[float, ...]:
        vector = [0.0] * dimensions
        tokens = _normalize_text(text).split()
        if not tokens:
            return tuple(vector)
        for token in tokens:
            digest = sha256(token.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        return normalize_vector(vector)


class OpenAICompatibleEmbeddingProvider:
    """Minimal OpenAI-compatible embeddings adapter behind the provider boundary."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dimensions: int,
        transport: JSONTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise AuthenticationError("Falta la clave para generar embeddings.")
        if not model.strip():
            raise ValueError("El modelo de embeddings no puede estar vacío.")
        if not 64 <= dimensions <= 3072:
            raise ValueError("La dimensión de embeddings debe estar entre 64 y 3072.")
        self.base_url = validate_llm_base_url(base_url, label="OpenAI compatible")
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.dimensions = dimensions
        self.transport = transport or UrllibJSONTransport()

    def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        if not request.texts:
            return EmbeddingResult(vectors=(), model=self.model, dimensions=self.dimensions)
        dimensions = request.dimensions or self.dimensions
        payload: dict[str, Any] = {
            "model": request.model or self.model,
            "input": list(request.texts),
            "encoding_format": "float",
            "dimensions": dimensions,
        }
        response = self.transport.post_json(
            url=f"{self.base_url}/embeddings",
            payload=payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout_seconds=request.timeout_seconds,
        )
        if response.status_code in {401, 403}:
            raise AuthenticationError("El proveedor de embeddings rechazó la autenticación.")
        if response.status_code == 429:
            raise RateLimitError("El proveedor de embeddings aplicó rate limit.")
        if response.status_code >= 500:
            raise RetryableProviderError("El proveedor de embeddings no está disponible.")
        if response.status_code >= 400:
            raise ValidationProviderError("El proveedor de embeddings rechazó la solicitud.")
        return _parse_embedding_response(
            response,
            expected_count=len(request.texts),
            dimensions=dimensions,
        )


def _parse_embedding_response(
    response: JSONResponse,
    *,
    expected_count: int,
    dimensions: int,
) -> EmbeddingResult:
    data = response.payload.get("data")
    if not isinstance(data, list) or len(data) != expected_count:
        raise ValidationProviderError("La respuesta de embeddings no tiene la cantidad esperada.")
    parsed: list[tuple[int, tuple[float, ...]]] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValidationProviderError("Un embedding no tiene formato de objeto.")
        index = item.get("index")
        raw_vector = item.get("embedding")
        if not isinstance(index, int) or not isinstance(raw_vector, list):
            raise ValidationProviderError("Un embedding no contiene índice y vector válidos.")
        if len(raw_vector) != dimensions:
            raise ValidationProviderError("Un embedding no tiene la dimensión configurada.")
        if not all(isinstance(value, (int, float)) for value in raw_vector):
            raise ValidationProviderError("Un embedding contiene valores no numéricos.")
        parsed.append((index, normalize_vector([float(value) for value in raw_vector])))
    parsed.sort(key=lambda item: item[0])
    vectors = tuple(vector for _, vector in parsed)
    model = str(response.payload.get("model") or "")
    return EmbeddingResult(vectors=vectors, model=model, dimensions=dimensions)


def assert_embedding_protocols() -> tuple[
    type[FakeEmbeddingProvider], type[OpenAICompatibleEmbeddingProvider]
]:
    return FakeEmbeddingProvider, OpenAICompatibleEmbeddingProvider
