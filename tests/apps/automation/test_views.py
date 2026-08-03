from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.automation.models import (
    FollowUpTopic,
    KnowledgeFactRevision,
    WorkspaceKnowledgeContextRevision,
)
from apps.configuration.models import PromptConfiguration


@pytest.mark.django_db
def test_automation_settings_explains_knowledge_fields_for_non_technical_admin(
    client: Client, owner
) -> None:
    client.force_login(owner)

    response = client.get(reverse("automation-settings"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Información para responder consultas" in content
    assert "Instrucciones de redacción" in content
    assert "NEW_INBOUND" in content
    assert "APPROVED_GLOBAL_CONTEXT" in content
    assert "APPROVED_FACTS" in content
    assert "Contexto general de la empresa" in content
    assert "Si falta contexto, no responde" in content
    assert "Qué puede decir la respuesta automática" in content
    assert "Precios o cotizaciones" in content
    assert "Guardar dato puntual" not in content
    assert "Guardar como borrador" not in content
    assert "Opcional. Ayuda a agrupar tarjetas parecidas." not in content
    assert "Aprobar para que el agente pueda usarlo" not in content
    assert "Guardar contexto" in content
    assert 'id="global-context"' in content
    assert 'id="knowledge-create"' in content
    assert 'id="knowledge-list"' in content
    assert "Probar búsqueda" in content
    assert (
        'action="/respuesta-automatica/informacion/probar-busqueda/#knowledge-preview"' in content
    )
    assert "Temas de seguimiento" in content


@pytest.mark.django_db
def test_admin_can_save_automatic_reply_prompt_from_dashboard(client: Client, owner) -> None:
    client.force_login(owner)

    response = client.post(
        reverse("automatic-reply-prompt-save"),
        {
            "automatic_reply_prompt": (
                "Respondé primero la consulta concreta y pedí medidas sólo si faltan."
            ),
        },
    )

    assert response.status_code == 302
    assert response["Location"].endswith("#reply-prompt")
    configuration = PromptConfiguration.objects.get(owner=owner)
    assert (
        configuration.automatic_reply_prompt
        == "Respondé primero la consulta concreta y pedí medidas sólo si faltan."
    )
    page = client.get(reverse("automation-settings"))
    assert "Respondé primero la consulta concreta" in page.content.decode()


@pytest.mark.django_db
def test_admin_can_create_and_edit_follow_up_topics(client: Client, owner) -> None:
    client.force_login(owner)
    due_at = timezone.now() + timedelta(days=14)

    created = client.post(
        reverse("follow-up-topic-save"),
        {
            "name": "Reactivar conversación",
            "objective": "Retomar una relación que quedó sin respuesta.",
            "instructions": "No ofrecer descuentos.",
            "cadence_days": "30",
            "mode": FollowUpTopic.Mode.REVIEW_BEFORE_SEND,
            "next_due_at": due_at.strftime("%Y-%m-%dT%H:%M"),
            "active": "on",
        },
    )

    assert created.status_code == 302
    topic = FollowUpTopic.objects.get(name="Reactivar conversación")
    assert topic.active
    assert topic.cadence_days == 30
    page = client.get(f"{reverse('automation-settings')}?edit_topic={topic.pk}")
    assert "Reactivar conversación" in page.content.decode()

    edited = client.post(
        reverse("follow-up-topic-save"),
        {
            "topic_id": str(topic.pk),
            "name": "Reactivar conversación",
            "objective": "Retomar una relación comercial pendiente.",
            "instructions": "",
            "cadence_days": "45",
            "mode": FollowUpTopic.Mode.REVIEW_BEFORE_SEND,
        },
    )

    assert edited.status_code == 302
    topic.refresh_from_db()
    assert topic.cadence_days == 45
    assert not topic.active


@pytest.mark.django_db
def test_admin_can_save_active_global_context(client: Client, owner) -> None:
    client.force_login(owner)

    created = client.post(
        reverse("global-context-create"),
        {
            "context_text": "Vendemos carbones para motores y respondemos con prudencia.",
        },
    )

    assert created.status_code == 302
    assert created["Location"].endswith("#global-context")
    revision = WorkspaceKnowledgeContextRevision.objects.get()
    assert revision.approved_at is not None
    assert revision.approved_by == owner
    assert revision.source_notes == ""
    page = client.get(reverse("automation-settings"))
    assert "Quiénes son, qué venden, tono y límites" in page.content.decode()


@pytest.mark.django_db
def test_admin_can_save_knowledge_fact_with_title_and_information_only(
    client: Client, owner
) -> None:
    client.force_login(owner)

    response = client.post(
        reverse("knowledge-create"),
        {
            "title": "Medidas disponibles",
            "text": "Podemos orientar al cliente si comparte modelo, medida o uso.",
        },
    )

    assert response.status_code == 302
    assert response["Location"].endswith("#knowledge-create")
    revision = KnowledgeFactRevision.objects.select_related("fact").get()
    assert revision.fact.title == "Medidas disponibles"
    assert revision.fact.category == ""
    assert revision.text == "Podemos orientar al cliente si comparte modelo, medida o uso."
    assert revision.approved_at is not None
    assert revision.approved_by == owner


@pytest.mark.django_db
def test_knowledge_search_preview_explains_human_path(
    client: Client,
    owner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client.force_login(owner)
    score = SimpleNamespace(
        revision=SimpleNamespace(fact=SimpleNamespace(title="Años de experiencia")),
        score=0.81,
        selected=True,
        may_be_irrelevant=True,
    )
    monkeypatch.setattr(
        "apps.automation.views.retrieve_relevant_fact_revisions",
        lambda **kwargs: SimpleNamespace(status="AMBIGUOUS", scores=(score,)),
    )

    response = client.post(
        reverse("knowledge-search-preview"),
        {"query": "¿Hace cuántos años trabajan?"},
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "Hay datos parecidos entre sí" in content
    assert "contexto posible" in content
    assert "Años de experiencia" in content
