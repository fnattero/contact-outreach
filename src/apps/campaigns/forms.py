from __future__ import annotations

from django import forms

from apps.campaigns.models import Campaign
from apps.catalogs.models import Catalog
from apps.configuration.models import SearchCategory, SearchZone

WEEKDAY_CHOICES = (
    (0, "Lunes"),
    (1, "Martes"),
    (2, "Miércoles"),
    (3, "Jueves"),
    (4, "Viernes"),
    (5, "Sábado"),
    (6, "Domingo"),
)


class CampaignForm(forms.ModelForm):  # type: ignore[type-arg]
    categories = forms.ModelMultipleChoiceField(
        queryset=SearchCategory.objects.filter(active=True, archived_at__isnull=True),
        label="Rubros",
        widget=forms.CheckboxSelectMultiple,
    )
    zones = forms.ModelMultipleChoiceField(
        queryset=SearchZone.objects.filter(active=True, archived_at__isnull=True),
        label="Zonas",
        widget=forms.CheckboxSelectMultiple,
    )
    weekdays = forms.MultipleChoiceField(
        choices=WEEKDAY_CHOICES,
        initial=(0, 1, 2, 3, 4),
        label="Días de envío",
        widget=forms.CheckboxSelectMultiple,
    )
    extractor_provider = forms.ChoiceField(
        choices=(("fake", "Mock (sin red)"), ("outscraper", "Outscraper"))
    )
    llm_provider = forms.ChoiceField(
        choices=(
            ("fake", "Mock (sin red)"),
            ("ollama", "Ollama"),
            ("openai-compatible", "OpenAI compatible"),
        )
    )
    llm_base_url = forms.URLField(required=False, assume_scheme="https")
    catalog = forms.ModelChoiceField(
        queryset=Catalog.objects.filter(active=True, missing=False), label="Catálogo"
    )

    class Meta:
        model = Campaign
        fields = (
            "name",
            "delivery_mode",
            "location_text",
            "objective",
            "max_raw_records",
            "cost_limit",
            "cost_currency",
            "daily_limit",
            "message_interval_minutes",
            "weekdays",
            "window_start",
            "window_end",
            "timezone_name",
            "relevance_threshold",
            "extractor_provider",
            "llm_provider",
            "llm_base_url",
            "llm_model",
            "catalog",
        )
        widgets = {
            "window_start": forms.TimeInput(attrs={"type": "time"}),
            "window_end": forms.TimeInput(attrs={"type": "time"}),
        }

    def clean_weekdays(self) -> list[int]:
        return [int(day) for day in self.cleaned_data["weekdays"]]
