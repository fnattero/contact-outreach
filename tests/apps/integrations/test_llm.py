from __future__ import annotations

import json
from typing import Any

import pytest

from apps.integrations.contracts import (
    AnalysisFact,
    AnalysisRequest,
    EmailCandidateRef,
    FactRevisionRef,
    JSONResponse,
    ReplyClassificationRequest,
    ReplyContextBlock,
    ReplyDecisionRequest,
    ValidationProviderError,
)
from apps.integrations.llm import (
    NoRedirectHandler,
    OllamaProvider,
    OpenAICompatibleProvider,
    UrllibJSONTransport,
    analysis_json_schema,
    parse_analysis_output,
    parse_reply_decision,
    reply_decision_json_schema,
    validate_llm_base_url,
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
    evidence_ids = ("prospect.category",)
    return AnalysisRequest(
        facts=(AnalysisFact("prospect.category", "Reparación de motores"),),
        correlation_id="correlation",
        idempotency_key="analysis-key",
        system_prompt="system",
        user_prompt="user",
        json_schema=analysis_json_schema(evidence_ids),
    )


def _decision_request() -> ReplyDecisionRequest:
    return ReplyDecisionRequest(
        context=(
            ReplyContextBlock(
                source_id="inbound-1",
                role="CLIENT",
                provenance="NEW_INBOUND",
                text="Enviá la propuesta a compras@example.com",
                mandatory=True,
            ),
        ),
        candidates=(
            EmailCandidateRef(
                candidate_id="candidate-1",
                normalized_email="compras@example.com",
                region="NEW_CONTENT",
                validation_state="VALID",
            ),
        ),
        facts=(FactRevisionRef("fact-1", 3, "Trabajamos desde hace veinte años."),),
        correlation_id="correlation",
        idempotency_key="decision-key",
        policy_version="2026-07",
    )


def _decision_output() -> dict[str, Any]:
    return {
        "classification": "INTERESTED",
        "intent": "EXPLICIT_PROPOSAL_REDIRECTION",
        "action": "REDIRECT_PROPOSAL",
        "confidence": 0.99,
        "candidate_id": "candidate-1",
        "fact_revision_ids": [],
        "proposed_body": None,
        "human_reason": None,
    }


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
    evidence_items = call["payload"]["response_format"]["json_schema"]["schema"]["properties"][
        "evidence"
    ]["items"]
    assert evidence_items["enum"] == ["prospect.category"]


def test_reply_schema_is_scoped_to_candidate_and_fact_ids() -> None:
    schema = reply_decision_json_schema(_decision_request())

    candidate_options = schema["properties"]["candidate_id"]["anyOf"]
    fact_items = schema["properties"]["fact_revision_ids"]["items"]
    assert candidate_options == [{"enum": ["candidate-1"]}, {"type": "null"}]
    assert fact_items == {"enum": ["fact-1"]}


def test_reply_decision_rejects_unknown_ids_and_wrong_candidate_region() -> None:
    unknown = _decision_output()
    unknown["candidate_id"] = "invented"
    with pytest.raises(ValidationProviderError, match="no incluido"):
        parse_reply_decision(unknown, request=_decision_request())

    request = _decision_request()
    quoted_request = ReplyDecisionRequest(
        context=request.context,
        candidates=(EmailCandidateRef("candidate-1", "compras@example.com", "QUOTED", "VALID"),),
        facts=request.facts,
        correlation_id=request.correlation_id,
        idempotency_key=request.idempotency_key,
        policy_version=request.policy_version,
    )
    with pytest.raises(ValidationProviderError, match="texto nuevo"):
        parse_reply_decision(_decision_output(), request=quoted_request)


def test_reply_decision_requires_grounding_and_human_reason() -> None:
    reply = _decision_output()
    reply.update(
        {
            "intent": "GROUNDED_SIMPLE_CLARIFICATION",
            "action": "REPLY",
            "candidate_id": None,
            "proposed_body": "Respuesta sin fuente",
        }
    )
    with pytest.raises(ValidationProviderError, match="información aprobada"):
        parse_reply_decision(reply, request=_decision_request())

    human = _decision_output()
    human.update(
        {
            "intent": "MEETING_OR_DATE",
            "action": "HUMAN",
            "candidate_id": None,
        }
    )
    with pytest.raises(ValidationProviderError, match="incompleta"):
        parse_reply_decision(human, request=_decision_request())


def test_openai_compatible_decision_uses_bounded_context_and_strict_schema() -> None:
    transport = RecordingTransport(
        {"choices": [{"message": {"content": json.dumps(_decision_output())}}]}
    )
    provider = OpenAICompatibleProvider(
        base_url="https://llm.test/v1",
        model="model-test",
        api_key="external-secret",
        transport=transport,
    )

    result = provider.decide_reply(_decision_request())

    assert result.candidate_id == "candidate-1"
    call = transport.calls[0]
    assert call["payload"]["response_format"]["json_schema"]["name"] == "reply_decision"
    messages = call["payload"]["messages"]
    assert "UNTRUSTED_DATA" in messages[1]["content"]
    assert "candidate-1" in messages[1]["content"]
    assert "sólo puede afirmar hechos incluidos" in messages[0]["content"]
    assert "no agregues precios" in messages[0]["content"]


def test_reply_provider_rejects_oversized_serialized_input_before_transport() -> None:
    request = _decision_request()
    oversized = ReplyDecisionRequest(
        context=(
            ReplyContextBlock(
                source_id="mandatory-inbound",
                role="CLIENT",
                provenance="NEW_INBOUND",
                text="x" * 24_000,
                mandatory=True,
            ),
        ),
        candidates=request.candidates,
        facts=request.facts,
        correlation_id=request.correlation_id,
        idempotency_key=request.idempotency_key,
        policy_version=request.policy_version,
    )
    transport = RecordingTransport(
        {"choices": [{"message": {"content": json.dumps(_decision_output())}}]}
    )
    provider = OpenAICompatibleProvider(
        base_url="https://llm.test/v1",
        model="model-test",
        api_key="external-secret",
        transport=transport,
    )

    with pytest.raises(ValidationProviderError, match="límite seguro"):
        provider.decide_reply(oversized)

    assert transport.calls == []


def test_analysis_schema_documents_score_scale_and_constrains_evidence() -> None:
    schema = analysis_json_schema(("web.page.1", "prospect.category"))

    score = schema["properties"]["relevance_score"]
    evidence_items = schema["properties"]["evidence"]["items"]
    assert "escala 0 a 100, nunca 0 a 10" in score["description"]
    assert evidence_items["enum"] == ["prospect.category", "web.page.1"]


def test_analysis_output_rejects_ambiguous_ten_point_scale() -> None:
    output = _raw_output()
    output["relevance_score"] = 8

    with pytest.raises(ValidationProviderError, match="escala 0 a 10"):
        parse_analysis_output(output)


def test_provider_rejects_unknown_evidence_even_if_remote_ignores_schema() -> None:
    output = _raw_output()
    output["evidence"] = ["prospect.purchases"]
    transport = RecordingTransport({"choices": [{"message": {"content": json.dumps(output)}}]})
    provider = OpenAICompatibleProvider(
        base_url="https://llm.test/v1",
        model="model-test",
        api_key="external-secret",
        transport=transport,
    )

    with pytest.raises(ValidationProviderError, match=r"prospect\.purchases"):
        provider.analyze(_request())


def test_invalid_evidence_diagnostic_does_not_echo_unsafe_model_text() -> None:
    output = _raw_output()
    output["evidence"] = ["secret copied from untrusted input"]

    with pytest.raises(ValidationProviderError) as caught:
        parse_analysis_output(
            output,
            allowed_evidence_ids=("prospect.category",),
        )

    assert "<formato no permitido>" in str(caught.value)
    assert "secret copied" not in str(caught.value)


def test_provider_response_still_requires_local_schema_validation() -> None:
    transport = RecordingTransport({"message": {"content": "not-json"}})
    provider = OllamaProvider(base_url="http://ollama.test", model="model", transport=transport)

    with pytest.raises(ValidationProviderError, match="JSON válido"):
        provider.analyze(_request())
    with pytest.raises(ValidationProviderError, match="clasificación IA"):
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


def test_provider_urls_reject_secret_bearing_insecure_and_metadata_targets() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        validate_llm_base_url("http://provider.example/v1", label="remoto")
    with pytest.raises(ValueError, match="credenciales"):
        validate_llm_base_url("https://user:secret@provider.example/v1", label="remoto")
    with pytest.raises(ValueError, match="query"):
        validate_llm_base_url("https://provider.example/v1?api_key=secret", label="remoto")
    with pytest.raises(ValueError, match="reservado"):
        validate_llm_base_url("http://metadata.google.internal", label="remoto")
    with pytest.raises(ValueError, match="HTTPS"):
        validate_llm_base_url("http://internal-service/v1", label="OpenAI compatible")
    assert validate_llm_base_url("http://ollama:11434", label="Ollama") == ("http://ollama:11434")
    assert validate_llm_base_url("http://127.0.0.1:11434", label="local") == (
        "http://127.0.0.1:11434"
    )


def test_llm_transport_refuses_redirects() -> None:
    handler = NoRedirectHandler()
    redirected = handler.redirect_request(None, None, 302, "Found", {}, "https://attacker.example")
    assert redirected is None


class FakeURLResponse:
    status = 200

    def __enter__(self) -> FakeURLResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        assert limit > 0
        return b'{"ok": true}'


class FakeOpener:
    def open(self, request: object, timeout: float) -> FakeURLResponse:
        del request, timeout
        return FakeURLResponse()


def test_default_json_transport_parses_bounded_object(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("apps.integrations.llm.build_opener", lambda *args: FakeOpener())

    result = UrllibJSONTransport().post_json(
        url="https://llm.test/v1/chat/completions",
        payload={"model": "test"},
        headers={},
        timeout_seconds=1,
    )

    assert result == JSONResponse(status_code=200, payload={"ok": True})
