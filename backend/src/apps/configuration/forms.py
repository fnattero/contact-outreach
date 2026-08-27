from __future__ import annotations

import json
from typing import Any, cast

from django import forms
from django.contrib.auth.models import User
from django.views.decorators.debug import sensitive_variables

from apps.accounts.models import Workspace
from apps.configuration.integrations import (
    RuntimeIntegrationConfiguration,
    validate_integration_base_url,
)
from apps.configuration.models import (
    BusinessProfile,
    IntegrationConfiguration,
    SearchCategory,
    SearchCategoryRule,
    SearchZone,
    WorkspaceMessageTemplateRevision,
)
from apps.configuration.services import MAX_EMAIL_DRAFTING_PROMPT_LENGTH
from apps.overture.geometry import ValidatedGeometry, validate_geojson


class BusinessProfileForm(forms.ModelForm):  # type: ignore[type-arg]
    website = forms.URLField(
        required=False,
        assume_scheme="https",
        label="Sitio web",
    )

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
        labels = {
            "company_name": "Nombre de la empresa",
            "salesperson_name": "Nombre del vendedor",
            "phone": "Teléfono",
            "whatsapp": "WhatsApp",
            "description": "Descripción de la empresa",
            "products": "Productos",
            "differentiators": "Diferenciales",
            "address": "Domicilio comercial",
            "signature": "Firma de los correos",
            "additional_instructions": "Instrucciones adicionales",
            "relevance_threshold": "Umbral de relevancia predeterminado",
        }


class SearchCategoryForm(forms.ModelForm):  # type: ignore[type-arg]
    variants_text = forms.CharField(
        label="Variantes que se buscarán",
        help_text=(
            "Escribí una variante por línea; también podés separarlas con comas. "
            "El sistema las combina automáticamente como alternativas."
        ),
        widget=forms.Textarea(
            attrs={
                "rows": 8,
                "placeholder": (
                    "puertas de seguridad\nportones automáticos\ncerrajería de seguridad"
                ),
            }
        ),
    )

    class Meta:
        model = SearchCategory
        fields = ("name", "active", "sort_order")
        labels = {
            "name": "Título del rubro",
            "active": "Activo",
            "sort_order": "Orden de aparición",
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.parsed_rules: list[dict[str, object]] = []
        self._taxonomy_codes_by_term: dict[str, list[str]] = {}
        super().__init__(*args, **kwargs)
        self.order_fields(("name", "variants_text", "active", "sort_order"))
        if not self.instance._state.adding and not self.is_bound:
            variants: list[str] = []
            for rule in self.instance.rules.filter(active=True):
                for term in rule.name_terms:
                    if term not in variants:
                        variants.append(term)
                    if rule.taxonomy_code:
                        codes = self._taxonomy_codes_by_term.setdefault(term, [])
                        if rule.taxonomy_code not in codes:
                            codes.append(rule.taxonomy_code)
            self.initial["variants_text"] = "\n".join(variants)
        elif not self.instance._state.adding:
            for rule in self.instance.rules.filter(active=True):
                if not rule.taxonomy_code:
                    continue
                for term in rule.name_terms:
                    codes = self._taxonomy_codes_by_term.setdefault(term, [])
                    if rule.taxonomy_code not in codes:
                        codes.append(rule.taxonomy_code)

    def clean_variants_text(self) -> str:
        value = str(self.cleaned_data["variants_text"])
        if "|" in value:
            raise forms.ValidationError(
                "No hace falta usar |. Escribí cada variante en una línea separada."
            )
        candidates = [
            candidate.strip()
            for line in value.splitlines()
            for candidate in line.split(",")
            if candidate.strip()
        ]
        if not candidates:
            raise forms.ValidationError("Agregá al menos una variante de búsqueda.")

        variants: list[str] = []
        for candidate in candidates:
            rule = SearchCategoryRule(
                category=self.instance,
                taxonomy_code="",
                name_terms=[candidate],
            )
            try:
                rule.clean()
            except Exception as exc:
                raise forms.ValidationError(f"Variante inválida «{candidate}»: {exc}") from exc
            normalized = rule.name_terms[0]
            if normalized not in variants:
                variants.append(normalized)
        if len(variants) > 20:
            raise forms.ValidationError("Cada rubro admite como máximo 20 variantes.")

        plain_rules: list[dict[str, object]] = [
            {"taxonomy_code": "", "name_terms": [term]} for term in variants
        ]
        technical_rules: list[dict[str, object]] = [
            {"taxonomy_code": code, "name_terms": [term]}
            for term in variants
            for code in self._taxonomy_codes_by_term.get(term, [])
        ]
        available_technical_slots = 20 - len(plain_rules)
        self.parsed_rules = technical_rules[:available_technical_slots] + plain_rules
        return "\n".join(variants)


class SearchZoneForm(forms.ModelForm):  # type: ignore[type-arg]
    parent = forms.ModelChoiceField(
        queryset=SearchZone.objects.none(),
        required=False,
        label="Provincia",
        help_text=(
            "Elegila si querés usar esta zona junto con los datos de una provincia. "
            "Dejala vacía si el área cruza límites provinciales."
        ),
    )
    boundary_upload = forms.FileField(
        required=False,
        label="Archivo con el límite geográfico",
        help_text=(
            "Subí un archivo GeoJSON con uno o varios polígonos (Polygon o MultiPolygon), "
            "en coordenadas WGS84 y de hasta 1 MiB. Al editar, dejalo vacío para conservar "
            "el límite actual."
        ),
    )

    class Meta:
        model = SearchZone
        fields = ("name", "kind", "parent", "location_text", "active", "sort_order")
        labels = {
            "name": "Nombre de la zona",
            "kind": "Tipo de zona",
            "location_text": "Ubicación general",
            "active": "Activa",
            "sort_order": "Orden de aparición",
        }

    def __init__(
        self,
        *args: Any,
        workspace: Workspace | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        if workspace is not None and not self.instance.workspace_id:
            self.instance.workspace = workspace
        if workspace is None and self.instance.workspace_id:
            workspace = self.instance.workspace
        cast(
            "forms.ModelChoiceField[SearchZone]",
            self.fields["parent"],
        ).queryset = (
            SearchZone.objects.filter(
                workspace=workspace,
                level=SearchZone.Level.PROVINCE,
                active=True,
                archived_at__isnull=True,
            ).order_by("name")
            if workspace is not None
            else SearchZone.objects.none()
        )
        self._original_boundary_hash = self.instance.boundary_hash

    def clean_boundary_upload(self) -> object | None:
        upload = self.cleaned_data.get("boundary_upload")
        if upload is None:
            if not self.instance.boundary_geojson:
                raise forms.ValidationError("Subí el límite GeoJSON de la zona.")
            return None
        if upload.size > 1024 * 1024:
            raise forms.ValidationError("El GeoJSON supera el máximo de 1 MiB.")
        try:
            payload = json.loads(upload.read().decode("utf-8"))
            return validate_geojson(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise forms.ValidationError(f"El GeoJSON no es válido: {exc}") from exc

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        validated = cleaned.get("boundary_upload")
        if isinstance(validated, ValidatedGeometry):
            # ModelForm runs model validation after this method. Populate the
            # canonical boundary now so SearchZone.clean() validates the same data.
            self.instance.boundary_geojson = validated.geojson
            self.instance.boundary_bbox = list(validated.bbox)
            self.instance.boundary_hash = validated.sha256
        return cleaned

    def save(self, commit: bool = True) -> SearchZone:
        zone = cast(SearchZone, super().save(commit=False))
        validated = self.cleaned_data.get("boundary_upload")
        if isinstance(validated, ValidatedGeometry):
            zone.boundary_geojson = validated.geojson
            zone.boundary_bbox = list(validated.bbox)
            if self._original_boundary_hash and self._original_boundary_hash != validated.sha256:
                zone.boundary_revision += 1
            zone.boundary_hash = validated.sha256
            zone.boundary_source = "Carga GeoJSON del dashboard"
            zone.boundary_attribution = ""
        if commit:
            zone.save()
        return zone


class PromptConfigurationForm(forms.Form):
    email_drafting_prompt = forms.CharField(
        required=False,
        max_length=MAX_EMAIL_DRAFTING_PROMPT_LENGTH,
        label="Preferencias para redactar correos",
        help_text=(
            "Se aplican sólo cuando son compatibles con las reglas inmutables de seguridad, "
            "evidencia, extensión, pregunta final, firma y texto sin formato."
        ),
        widget=forms.Textarea(attrs={"rows": 10, "spellcheck": "true"}),
    )


class MessageTemplateRevisionForm(forms.Form):
    kind = forms.ChoiceField(
        choices=WorkspaceMessageTemplateRevision.Kind.choices,
        label="Mensaje a actualizar",
    )
    subject = forms.CharField(
        required=False,
        max_length=255,
        strip=True,
        label="Asunto",
        help_text=(
            "El recordatorio continúa el mismo hilo de correo y no lleva asunto propio; "
            "los demás mensajes sí lo necesitan."
        ),
    )
    body = forms.CharField(
        strip=True,
        label="Mensaje",
        widget=forms.Textarea(attrs={"rows": 10}),
        help_text=(
            "Texto fijo que reciben todos los destinatarios en esta etapa. "
            "No admite datos variables por destinatario."
        ),
    )


class IntegrationConfigurationForm(forms.Form):
    extractor_provider = forms.ChoiceField(
        choices=(
            (IntegrationConfiguration.ExtractorProvider.FAKE, "Simulado (sin red)"),
            (IntegrationConfiguration.ExtractorProvider.OVERTURE, "Overture Maps Places"),
        ),
        label="Proveedor de extracción predeterminado",
    )
    overture_min_confidence = forms.DecimalField(
        min_value=0,
        max_value=1,
        max_digits=4,
        decimal_places=3,
        label="Confianza mínima de existencia de Overture",
        help_text="Valor predeterminado 0,750. No mide la completitud del contacto.",
    )
    website_fetcher = forms.ChoiceField(
        choices=(
            (IntegrationConfiguration.WebsiteFetcher.FAKE, "Simulado (sin red)"),
            (IntegrationConfiguration.WebsiteFetcher.HTTP, "HTTP seguro"),
        ),
        label="Lectura de sitios web",
        help_text=(
            "La lectura real visita únicamente sitios públicos validados y bloquea intentos "
            "de acceder a redes internas (protección SSRF)."
        ),
    )
    llm_provider = forms.ChoiceField(
        choices=(
            (IntegrationConfiguration.LLMProvider.FAKE, "Simulado (sin red)"),
            (IntegrationConfiguration.LLMProvider.OLLAMA, "Ollama"),
            (
                IntegrationConfiguration.LLMProvider.OPENAI_COMPATIBLE,
                "Compatible con OpenAI",
            ),
        ),
        label="Proveedor IA predeterminado",
    )
    llm_model = forms.CharField(max_length=120, label="Modelo predeterminado")
    ollama_base_url = forms.URLField(
        max_length=500,
        assume_scheme="http",
        label="Dirección base de Ollama",
    )
    openai_compatible_base_url = forms.URLField(
        required=False,
        max_length=500,
        assume_scheme="https",
        label="Dirección base del proveedor compatible con OpenAI",
    )
    llm_api_key = forms.CharField(
        required=False,
        max_length=4096,
        strip=True,
        label="Nueva clave de API del proveedor de inteligencia artificial",
        widget=forms.PasswordInput(
            render_value=False,
            attrs={"autocomplete": "new-password", "spellcheck": "false"},
        ),
        help_text="Dejala vacía para conservar la credencial actual. Nunca vuelve a mostrarse.",
    )
    remove_llm_api_key = forms.BooleanField(
        required=False,
        label=(
            "Eliminar la credencial de inteligencia artificial y desactivar el respaldo del entorno"
        ),
    )
    embedding_provider = forms.ChoiceField(
        choices=(
            (IntegrationConfiguration.EmbeddingProvider.FAKE, "Simulado (sin red)"),
            (
                IntegrationConfiguration.EmbeddingProvider.OPENAI_COMPATIBLE,
                "OpenAI embeddings",
            ),
        ),
        label="Buscador de datos para respuestas",
        help_text=(
            "Elige cómo se encuentran las tarjetas aprobadas que se le muestran al agente. "
            "Esto no autoriza respuestas: sólo decide qué información entra al contexto."
        ),
    )
    embedding_model = forms.CharField(
        max_length=120,
        label="Modelo de embeddings",
        help_text=(
            "Modelo usado para comparar la pregunta del cliente con tus datos aprobados. "
            "Recomendado: text-embedding-3-small."
        ),
    )
    embedding_dimensions = forms.IntegerField(
        min_value=64,
        max_value=3072,
        label="Tamaño del vector",
        help_text=(
            "Cantidad de números que guarda cada dato para buscarlo. 1536 es el valor normal "
            "de text-embedding-3-small; bajarlo ahorra espacio pero puede perder precisión."
        ),
    )
    gmail_provider = forms.ChoiceField(
        choices=(
            (IntegrationConfiguration.GmailProvider.FAKE, "Simulado (sin red)"),
            (IntegrationConfiguration.GmailProvider.API, "API de Google Gmail"),
        ),
        label="Proveedor Gmail",
    )
    gmail_oauth_client_id = forms.CharField(
        required=False,
        max_length=500,
        strip=True,
        label="Identificador de cliente de Google OAuth",
    )
    gmail_oauth_client_secret = forms.CharField(
        required=False,
        max_length=4096,
        strip=True,
        label="Nuevo secreto de cliente de Google OAuth",
        widget=forms.PasswordInput(
            render_value=False,
            attrs={"autocomplete": "new-password", "spellcheck": "false"},
        ),
        help_text="Dejalo vacío para conservar el secreto actual. Nunca vuelve a mostrarse.",
    )
    remove_gmail_oauth_client_secret = forms.BooleanField(
        required=False,
        label="Eliminar el secreto de cliente y desactivar el respaldo del entorno",
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
            ("llm_api_key", runtime.llm_credential_configured, runtime.llm_credential_source),
            (
                "gmail_oauth_client_secret",
                runtime.gmail_credential_configured,
                runtime.gmail_credential_source,
            ),
        )
        source_labels = {
            "ENVIRONMENT": "variables del entorno",
            "ENCRYPTED": "almacenamiento cifrado",
            "NONE": "sin origen configurado",
        }
        for field_name, configured, source in statuses:
            status = "configurada" if configured else "no configurada"
            self.fields[field_name].help_text = (
                f"Estado actual: {status} "
                f"({source_labels.get(source, 'origen desconocido')}). "
                "Dejá el campo vacío para conservarlo; el valor nunca vuelve a mostrarse."
            )

    @sensitive_variables("password")
    def clean_current_password(self) -> str:
        password = str(self.cleaned_data["current_password"])
        if not self.user.check_password(password):
            raise forms.ValidationError("La contraseña actual no es correcta.")
        return password

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
        if (
            cleaned.get("embedding_provider")
            == IntegrationConfiguration.EmbeddingProvider.OPENAI_COMPATIBLE
        ):
            if not cleaned.get("openai_compatible_base_url"):
                self.add_error(
                    "openai_compatible_base_url",
                    "El buscador con embeddings de OpenAI usa esta misma URL base.",
                )
        return cleaned
