from __future__ import annotations

import json
from typing import Any

import pytest

from apps.integrations.contracts import (
    AnalysisFact,
    AnalysisRequest,
    JSONResponse,
    ReplyClassificationRequest,
    ValidationProviderError,
)
from apps.integrations.llm import (
    OllamaProvider,
    OpenAICompatibleProvider,
    UrllibJSONTransport,
    analysis_json_schema,
    parse_analysis_output,
)


def _raw_output() -> dict[str, Any]:
    return {
        "relevance_score": 75,
        "confidence": 0.8,
        "relevance_reason": "El hecho aportado indica reparación de motores.",
        "evidence": ["prospect.category"],
        "subject": "Consulta técnica",
        "body_text": "Texto de prueba",
    }


def _request() -> AnalysisRequest:
    return AnalysisRequest(
        facts=(AnalysisFact("prospect.category", "Reparación de motores"),),
        correlation_id="correlation",
        idempotency_key="analysis-key",
        system_prompt="system",
        user_prompt="user",
        json_schema=analysis_json_schema(),
    )


class RecordingTransport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
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
        return JSONResponse(status_code=200, payload=self.response)


def test_ollama_uses_one_structured_chat_call() -> None:
    transport = RecordingTransport(
        {"message": {"content": json.dumps(_raw_output(), ensure_ascii=False)}}
    )
    provider = OllamaProvider(
        base_url="http://ollama.test/",
        model="llama-test",
        transport=transport,
    )

    result = provider.analyze(_request())

    assert result.relevance_score == 75
    assert transport.calls[0]["url"] == "http://ollama.test/api/chat"
    assert transport.calls[0]["payload"]["format"]["type"] == "object"
    assert len(transport.calls[0]["payload"]["messages"]) == 2


def test_openai_compatible_uses_schema_and_external_api_key() -> None:
    transport = RecordingTransport(
        {"choices": [{"message": {"content": json.dumps(_raw_output())}}]}
    )
    provider = OpenAICompatibleProvider(
        base_url="https://llm.test/v1",
        model="model-test",
        api_key="external-secret",
        transport=transport,
    )

    result = provider.analyze(_request())

    assert result.evidence == ("prospect.category",)
    call = transport.calls[0]
    assert call["url"] == "https://llm.test/v1/chat/completions"
    assert call["headers"] == {"Authorization": "Bearer external-secret"}
    assert call["payload"]["response_format"]["type"] == "json_schema"


def test_provider_response_still_requires_local_schema_validation() -> None:
    transport = RecordingTransport({"message": {"content": "not-json"}})
    provider = OllamaProvider(base_url="http://ollama.test", model="model", transport=transport)

    with pytest.raises(ValidationProviderError, match="JSON válido"):
        provider.analyze(_request())
    with pytest.raises(ValidationProviderError, match="fase 10"):
        provider.classify_reply(ReplyClassificationRequest("hola", "correlation", "classification"))
    with pytest.raises(ValidationProviderError, match="esquema"):
        parse_analysis_output({"relevance_score": 101})


def test_provider_configuration_is_explicit() -> None:
    with pytest.raises(ValueError, match="modelo"):
        OllamaProvider(base_url="http://ollama.test", model="")
    with pytest.raises(ValueError, match="URL base"):
        OllamaProvider(base_url="", model="model")
    with pytest.raises(ValueError, match="URL base"):
        OpenAICompatibleProvider(base_url="", model="model", api_key="secret")


class FakeURLResponse:
    status = 200

    def __enter__(self) -> FakeURLResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        assert limit > 0
        return b'{"ok": true}'


def test_default_json_transport_parses_bounded_object(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("apps.integrations.llm.urlopen", lambda *args, **kwargs: FakeURLResponse())

    result = UrllibJSONTransport().post_json(
        url="https://llm.test/v1/chat/completions",
        payload={"model": "test"},
        headers={},
        timeout_seconds=1,
    )

    assert result == JSONResponse(status_code=200, payload={"ok": True})
