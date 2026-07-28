from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
def test_automation_settings_explains_knowledge_fields_for_non_technical_admin(
    client: Client, owner
) -> None:
    client.force_login(owner)

    response = client.get(reverse("automation-settings"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Información para responder consultas" in content
    assert "Qué puede decir la respuesta automática" in content
    assert "No lee catálogos PDF automáticamente" in content
    assert "Precios o cotizaciones" in content
    assert "Guardar como borrador" in content
