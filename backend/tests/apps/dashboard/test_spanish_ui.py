from __future__ import annotations

import pytest
from django import forms
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.campaigns.forms import CampaignForm
from apps.configuration.forms import BusinessProfileForm
from apps.dashboard.templatetags.ui_extras import spanish_label


def test_forms_use_spanish_labels_and_explanations() -> None:
    campaign_form = CampaignForm()
    profile_form = BusinessProfileForm()

    assert campaign_form.fields["max_raw_records"].label == "Máximo de registros iniciales"
    assert "incluidos duplicados" in campaign_form.fields["max_raw_records"].help_text
    assert campaign_form.fields["llm_model"].label == "Modelo de inteligencia artificial"
    assert isinstance(campaign_form.fields["delivery_mode"].widget, forms.RadioSelect)
    assert "No delimita la búsqueda" in campaign_form.fields["location_text"].help_text
    assert profile_form.fields["company_name"].label == "Nombre de la empresa"
    assert profile_form.fields["additional_instructions"].label == "Instrucciones adicionales"


def test_internal_identifiers_have_spanish_display_labels() -> None:
    assert spanish_label("fake") == "Simulado (sin red)"
    assert spanish_label("mailbox.deliver_message") == "Entregar correo"
    assert spanish_label("OutboundMessage") == "Correo saliente"
    assert spanish_label("website_mailto") == "Enlace de correo del sitio web"


@pytest.mark.django_db
def test_campaign_form_renders_keyboard_accessible_spanish_help(
    client: Client, owner: User
) -> None:
    client.force_login(owner)

    response = client.get(reverse("campaign-create"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Máximo de registros iniciales" in content
    assert 'class="help-trigger"' in content
    assert "Ayuda sobre Máximo de registros iniciales" in content
    assert "data-tooltip=" in content
    assert 'data-choice-select-all="category-choices"' in content
    assert "data-province-toggle=" in content
    assert "data-district-select-all=" in content
    assert "Primero elegí una o más provincias" in content
    assert "data-zone-map-canvas" in content
    assert "data-zone-map-url" in content
    assert 'id="campaign-zone-map-data"' not in content
    assert 'class="choice-item"' in content
    assert 'class="radio-card"' in content
    assert 'type="radio" name="delivery_mode"' in content
    assert '<select name="delivery_mode"' not in content
    assert "Todas las empresas reciben el mismo mensaje aprobado" in content
    assert 'name="llm_provider"' not in content
