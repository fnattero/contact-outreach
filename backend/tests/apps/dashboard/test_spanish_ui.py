from __future__ import annotations

from django import forms

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
