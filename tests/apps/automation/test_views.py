from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.automation.models import FollowUpTopic, WorkspaceKnowledgeContextRevision


@pytest.mark.django_db
def test_automation_settings_explains_knowledge_fields_for_non_technical_admin(
    client: Client, owner
) -> None:
    client.force_login(owner)

    response = client.get(reverse("automation-settings"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Información para responder consultas" in content
    assert "Contexto general que se agrega siempre" in content
    assert "El buscador elige hasta 3" in content
    assert "Qué puede decir la respuesta automática" in content
    assert "No lee catálogos PDF automáticamente" in content
    assert "Precios o cotizaciones" in content
    assert "Guardar dato puntual" not in content
    assert "Guardar como borrador" in content
    assert "Probar búsqueda" in content
    assert "Temas de seguimiento" in content


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
def test_admin_can_create_and_approve_global_context(client: Client, owner) -> None:
    client.force_login(owner)

    created = client.post(
        reverse("global-context-create"),
        {
            "context_text": "Vendemos carbones para motores y respondemos con prudencia.",
            "source_notes": "Revisado por dirección.",
        },
    )

    assert created.status_code == 302
    revision = WorkspaceKnowledgeContextRevision.objects.get()
    assert revision.approved_at is None

    approved = client.post(reverse("global-context-approve", args=(revision.pk,)))

    assert approved.status_code == 302
    revision.refresh_from_db()
    assert revision.approved_at is not None
    page = client.get(reverse("automation-settings"))
    assert "Contexto aprobado actual" in page.content.decode()


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
        selected=False,
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
    assert "Hay datos demasiado parecidos" in content
    assert "Años de experiencia" in content
