from __future__ import annotations

import json
from typing import Any

import pytest

from apps.integrations.contracts import (
    FactRevisionRef,
    JSONResponse,
    ReplyContextBlock,
    ScheduledContactDraftRequest,
    ValidationProviderError,
)
from apps.integrations.llm import (
    OpenAICompatibleProvider,
    parse_scheduled_contact_draft,
    scheduled_contact_draft_json_schema,
)


def _request() -> ScheduledContactDraftRequest:
    return ScheduledContactDraftRequest(
        context=(
            ReplyContextBlock(
                source_id="plan-1",
                role="WORKSPACE",
                provenance="SCHEDULED_PURPOSE",
                text="Preguntar cómo están.",
                mandatory=True,
            ),
        ),
        facts=(FactRevisionRef("fact-1", 2, "Atendemos de lunes a viernes."),),
        purpose="CHECK_IN",
        goal="Preguntar cómo están.",
        correlation_id="correlation",
        idempotency_key="scheduled-draft-1",
    )


def _output() -> dict[str, Any]:
    return {
        "status": "DRAFT",
        "subject": "¿Cómo están?",
        "body_text": "Buen día. Queríamos saber cómo están.",
        "fact_revision_ids": [],
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


def test_scheduled_schema_scopes_fact_revision_ids() -> None:
    schema = scheduled_contact_draft_json_schema(_request())
    assert schema["properties"]["fact_revision_ids"]["items"] == {"enum": ["fact-1"]}


def test_scheduled_draft_rejects_invented_fact_and_inconsistent_human_result() -> None:
    invented = _output()
    invented["fact_revision_ids"] = ["invented"]
    with pytest.raises(ValidationProviderError, match="no incluida"):
        parse_scheduled_contact_draft(invented, request=_request())

    inconsistent = _output()
    inconsistent.update({"status": "HUMAN", "human_reason": "INSUFFICIENT_CONTEXT"})
    with pytest.raises(ValidationProviderError, match="no puede autorizar"):
        parse_scheduled_contact_draft(inconsistent, request=_request())


def test_openai_scheduled_draft_uses_bounded_context_and_strict_schema() -> None:
    transport = RecordingTransport({"choices": [{"message": {"content": json.dumps(_output())}}]})
    provider = OpenAICompatibleProvider(
        base_url="https://llm.test/v1",
        model="model-test",
        api_key="external-secret",
        transport=transport,
    )

    result = provider.draft_scheduled_contact(_request())

    assert result.subject == "¿Cómo están?"
    call = transport.calls[0]
    assert call["payload"]["response_format"]["json_schema"]["strict"] is True
    serialized_messages = json.dumps(call["payload"]["messages"], ensure_ascii=False)
    assert "UNTRUSTED_DATA" in serialized_messages
    assert "Atendemos de lunes a viernes" in serialized_messages


def test_scheduled_provider_rejects_oversized_serialized_input_before_transport() -> None:
    request = _request()
    oversized = ScheduledContactDraftRequest(
        context=(
            ReplyContextBlock(
                source_id="mandatory-purpose",
                role="WORKSPACE",
                provenance="SCHEDULED_PURPOSE",
                text="x" * 24_000,
                mandatory=True,
            ),
        ),
        facts=request.facts,
        purpose=request.purpose,
        goal=request.goal,
        correlation_id=request.correlation_id,
        idempotency_key=request.idempotency_key,
    )
    transport = RecordingTransport({"choices": [{"message": {"content": json.dumps(_output())}}]})
    provider = OpenAICompatibleProvider(
        base_url="https://llm.test/v1",
        model="model-test",
        api_key="external-secret",
        transport=transport,
    )

    with pytest.raises(ValidationProviderError, match="límite seguro"):
        provider.draft_scheduled_contact(oversized)

    assert transport.calls == []
