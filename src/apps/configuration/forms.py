from __future__ import annotations

from typing import Any

from django import forms
from django.contrib.auth.models import User
from django.views.decorators.debug import sensitive_variables

from apps.configuration.integrations import (
    RuntimeIntegrationConfiguration,
    validate_integration_base_url,
)
from apps.configuration.models import (
    BusinessProfile,
    IntegrationConfiguration,
    SearchCategory,
    SearchZone,
)
from apps.integrations.outscraper import OFFICIAL_OUTSCRAPER_HOSTS


class BusinessProfileForm(forms.ModelForm):  # type: ignore[type-arg]
    website = forms.URLField(required=False, assume_scheme="https")

    class Meta:
        model = BusinessProfile
        fields = (
            "company_name",
            "salesperson_name",
            "phone",
            "whatsapp",
            "description",
            "products",
            "differentiators",
            "address",
            "website",
            "signature",
            "additional_instructions",
            "relevance_threshold",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "products": forms.Textarea(attrs={"rows": 3}),
            "differentiators": forms.Textarea(attrs={"rows": 3}),
            "signature": forms.Textarea(attrs={"rows": 3}),
            "additional_instructions": forms.Textarea(attrs={"rows": 3}),
        }


class SearchCategoryForm(forms.ModelForm):  # type: ignore[type-arg]
    class Meta:
        model = SearchCategory
        fields = ("name", "active", "sort_order")


class SearchZoneForm(forms.ModelForm):  # type: ignore[type-arg]
    class Meta:
        model = SearchZone
        fields = ("name", "kind", "location_text", "active", "sort_order")


class IntegrationConfigurationForm(forms.Form):
    extractor_provider = forms.ChoiceField(
        choices=IntegrationConfiguration.ExtractorProvider.choices,
        label="Proveedor de extracción predeterminado",
    )
    outscraper_api_key = forms.CharField(
        required=False,
        max_length=4096,
        strip=True,
        label="Nueva API key de Outscraper",
        widget=forms.PasswordInput(
            render_value=False,
            attrs={"autocomplete": "new-password", "spellcheck": "false"},
        ),
        help_text="Dejala vacía para conservar la credencial actual. Nunca vuelve a mostrarse.",
    )
    remove_outscraper_api_key = forms.BooleanField(
        required=False,
        label="Eliminar la credencial de Outscraper y desactivar el fallback al entorno",
    )
    outscraper_base_url = forms.URLField(
        max_length=500,
        assume_scheme="https",
        label="URL base de Outscraper",
    )
    outscraper_max_cost_per_result = forms.DecimalField(
        min_value=0,
        max_digits=12,
        decimal_places=6,
        label="Reserva máxima USD por resultado",
    )
    outscraper_batch_size = forms.IntegerField(
        min_value=1,
        max_value=1000,
        label="Resultados máximos por lote",
    )
    outscraper_poll_seconds = forms.IntegerField(
        min_value=1,
        max_value=3600,
        label="Segundos entre consultas de estado",
    )
    llm_provider = forms.ChoiceField(
        choices=IntegrationConfiguration.LLMProvider.choices,
        label="Proveedor IA predeterminado",
    )
    llm_model = forms.CharField(max_length=120, label="Modelo predeterminado")
    ollama_base_url = forms.URLField(
        max_length=500,
        assume_scheme="http",
        label="URL base de Ollama",
    )
    openai_compatible_base_url = forms.URLField(
        required=False,
        max_length=500,
        assume_scheme="https",
        label="URL base OpenAI compatible",
    )
    llm_api_key = forms.CharField(
        required=False,
        max_length=4096,
        strip=True,
        label="Nueva API key del proveedor IA",
        widget=forms.PasswordInput(
            render_value=False,
            attrs={"autocomplete": "new-password", "spellcheck": "false"},
        ),
        help_text="Dejala vacía para conservar la credencial actual. Nunca vuelve a mostrarse.",
    )
    remove_llm_api_key = forms.BooleanField(
        required=False,
        label="Eliminar la credencial IA y desactivar el fallback al entorno",
    )
    gmail_provider = forms.ChoiceField(
        choices=IntegrationConfiguration.GmailProvider.choices,
        label="Proveedor Gmail",
    )
    gmail_oauth_client_id = forms.CharField(
        required=False,
        max_length=500,
        strip=True,
        label="Google OAuth client ID",
    )
    gmail_oauth_client_secret = forms.CharField(
        required=False,
        max_length=4096,
        strip=True,
        label="Nuevo Google OAuth client secret",
        widget=forms.PasswordInput(
            render_value=False,
            attrs={"autocomplete": "new-password", "spellcheck": "false"},
        ),
        help_text="Dejalo vacío para conservar el secreto actual. Nunca vuelve a mostrarse.",
    )
    remove_gmail_oauth_client_secret = forms.BooleanField(
        required=False,
        label="Eliminar el client secret y desactivar el fallback al entorno",
    )
    current_password = forms.CharField(
        strip=False,
        label="Contraseña actual",
        widget=forms.PasswordInput(
            render_value=False,
            attrs={"autocomplete": "current-password"},
        ),
        help_text="Se exige nuevamente para proteger cambios sensibles ante una sesión robada.",
    )

    def __init__(
        self,
        *args: Any,
        user: User,
        runtime: RuntimeIntegrationConfiguration,
        **kwargs: Any,
    ) -> None:
        self.user = user
        self.runtime = runtime
        super().__init__(*args, **kwargs)
        statuses = (
            (
                "outscraper_api_key",
                runtime.outscraper_credential_configured,
                runtime.outscraper_credential_source,
            ),
            ("llm_api_key", runtime.llm_credential_configured, runtime.llm_credential_source),
            (
                "gmail_oauth_client_secret",
                runtime.gmail_credential_configured,
                runtime.gmail_credential_source,
            ),
        )
        for field_name, configured, source in statuses:
            status = "configurada" if configured else "no configurada"
            self.fields[field_name].help_text = (
                f"Estado actual: {status} ({source.casefold()}). "
                "Dejá el campo vacío para conservarlo; el valor nunca vuelve a mostrarse."
            )

    @sensitive_variables("password")
    def clean_current_password(self) -> str:
        password = str(self.cleaned_data["current_password"])
        if not self.user.check_password(password):
            raise forms.ValidationError("La contraseña actual no es correcta.")
        return password

    def clean_outscraper_base_url(self) -> str:
        return validate_integration_base_url(
            self.cleaned_data["outscraper_base_url"],
            label="Outscraper",
            official_hosts=OFFICIAL_OUTSCRAPER_HOSTS,
        )

    def clean_ollama_base_url(self) -> str:
        return validate_integration_base_url(
            self.cleaned_data["ollama_base_url"],
            label="Ollama",
            allow_http_service_name=True,
        )

    def clean_openai_compatible_base_url(self) -> str:
        value = str(self.cleaned_data["openai_compatible_base_url"])
        if not value:
            return ""
        return validate_integration_base_url(value, label="OpenAI compatible")

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        secret_actions = (
            ("outscraper_api_key", "remove_outscraper_api_key"),
            ("llm_api_key", "remove_llm_api_key"),
            ("gmail_oauth_client_secret", "remove_gmail_oauth_client_secret"),
        )
        for value_field, remove_field in secret_actions:
            if cleaned.get(value_field) and cleaned.get(remove_field):
                self.add_error(remove_field, "No reemplaces y elimines la misma credencial.")
        if cleaned.get(
            "llm_provider"
        ) == IntegrationConfiguration.LLMProvider.OPENAI_COMPATIBLE and not cleaned.get(
            "openai_compatible_base_url"
        ):
            self.add_error(
                "openai_compatible_base_url",
                "El proveedor OpenAI compatible requiere una URL base.",
            )
        return cleaned
