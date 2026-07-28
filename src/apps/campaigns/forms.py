from __future__ import annotations

from typing import Any, cast

from django import forms

from apps.accounts.models import Workspace
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
    delivery_mode = forms.ChoiceField(
        choices=Campaign.DeliveryMode.choices,
        label="Modo de entrega",
        widget=forms.RadioSelect,
        help_text=(
            "Simulación prepara todo sin usar Gmail. Solo revisión deja los mensajes para leer. "
            "En vivo puede enviarlos únicamente después de la aprobación elegida."
        ),
    )
    confirm_live = forms.BooleanField(
        required=False,
        label=(
            "Confirmo que revisé la lista de controles para envío real y que una campaña "
            "en vivo puede enviar "
            "correos reales sólo después de que apruebe la campaña y si los controles "
            "de despliegue también están habilitados"
        ),
        help_text=(
            "Esta confirmación no reemplaza el modo de envío del sistema, el bloqueo general, "
            "la conexión de Gmail ni las verificaciones previas."
        ),
    )
    categories = forms.ModelMultipleChoiceField(
        queryset=SearchCategory.objects.none(),
        label="Rubros",
        widget=forms.CheckboxSelectMultiple,
        help_text="Cada rubro se combina con cada distrito para generar una búsqueda.",
    )
    zones = forms.ModelMultipleChoiceField(
        queryset=SearchZone.objects.none(),
        label="Distritos",
        widget=forms.CheckboxSelectMultiple,
        help_text="Elegí los partidos, departamentos o barrios que querés recorrer.",
    )
    provinces = forms.ModelMultipleChoiceField(
        queryset=SearchZone.objects.none(),
        required=False,
        label="Provincias",
        widget=forms.CheckboxSelectMultiple,
        help_text="Primero elegí una o más provincias para ver sus distritos.",
    )
    weekdays = forms.MultipleChoiceField(
        choices=WEEKDAY_CHOICES,
        initial=(0, 1, 2, 3, 4),
        label="Días de envío",
        widget=forms.CheckboxSelectMultiple,
    )
    extractor_provider = forms.ChoiceField(
        choices=(("fake", "Simulado (sin red)"), ("overture", "Overture Maps Places")),
        label="Proveedor de extracción",
        help_text=(
            "La opción simulada usa datos de prueba. Overture consulta la copia local importada "
            "de su catálogo de lugares."
        ),
    )
    website_fetcher = forms.ChoiceField(
        choices=(("fake", "Simulado (sin red)"), ("http", "HTTP seguro")),
        label="Lectura de sitios web",
        help_text=(
            "La opción HTTP visita sitios públicos con límites de tamaño, tiempo y protección "
            "contra accesos a redes privadas."
        ),
    )
    llm_provider = forms.ChoiceField(
        choices=(
            ("fake", "Simulado (sin red)"),
            ("ollama", "Ollama"),
            ("openai-compatible", "Compatible con OpenAI"),
        ),
        label="Proveedor de inteligencia artificial",
        help_text="Servicio que calificará prospectos y redactará los correos.",
    )
    llm_base_url = forms.URLField(
        required=False,
        assume_scheme="https",
        label="Dirección base del proveedor de inteligencia artificial",
        help_text="Dirección del servicio configurado; se conserva fija al iniciar la campaña.",
    )
    catalog = forms.ModelChoiceField(
        queryset=Catalog.objects.none(),
        required=False,
        widget=forms.HiddenInput,
        label="Catálogo principal (compatibilidad)",
    )
    catalogs = forms.ModelMultipleChoiceField(
        queryset=Catalog.objects.none(),
        label="PDFs que se adjuntarán",
        widget=forms.CheckboxSelectMultiple,
        help_text=(
            "Elegí uno o más catálogos. Se adjuntarán en este orden a la propuesta inicial; "
            "el recordatorio no lleva archivos."
        ),
    )

    class Meta:
        model = Campaign
        fields = (
            "name",
            "delivery_mode",
            "approval_mode",
            "reminder_enabled",
            "reminder_delay_days",
            "location_text",
            "objective",
            "max_raw_records",
            "overture_min_confidence",
            "daily_limit",
            "message_interval_minutes",
            "weekdays",
            "window_start",
            "window_end",
            "timezone_name",
            "relevance_threshold",
            "extractor_provider",
            "website_fetcher",
            "llm_provider",
            "llm_base_url",
            "llm_model",
            "catalog",
        )
        widgets = {
            "window_start": forms.TimeInput(attrs={"type": "time"}),
            "window_end": forms.TimeInput(attrs={"type": "time"}),
            "relevance_threshold": forms.HiddenInput,
        }
        labels = {
            "name": "Nombre de la campaña",
            "approval_mode": "Cómo querés aprobar los mensajes",
            "reminder_enabled": "Enviar un recordatorio si no responden",
            "reminder_delay_days": "Días de espera antes del recordatorio",
            "location_text": "Ubicación general",
            "objective": "Objetivo de destinatarios válidos",
            "max_raw_records": "Máximo de registros iniciales",
            "overture_min_confidence": "Confianza mínima de existencia en Overture",
            "daily_limit": "Límite diario de correos",
            "message_interval_minutes": "Intervalo entre correos (minutos)",
            "window_start": "Hora de inicio",
            "window_end": "Hora de finalización",
            "timezone_name": "Zona horaria",
            "relevance_threshold": "Umbral mínimo de relevancia",
            "llm_model": "Modelo de inteligencia artificial",
        }
        help_texts = {
            "location_text": (
                "Etiqueta descriptiva para identificar y filtrar la campaña. No delimita la "
                "búsqueda: los distritos seleccionados definen el área real."
            ),
            "objective": (
                "Cantidad de empresas únicas, sin contacto previo y con un correo válido, que se "
                "busca alcanzar."
            ),
            "max_raw_records": (
                "Cantidad máxima de registros iniciales que se revisarán, incluidos duplicados o "
                "negocios sin correo."
            ),
            "overture_min_confidence": (
                "Nivel mínimo, entre 0 y 1, de confianza de Overture en que el lugar existe."
            ),
            "daily_limit": "Cantidad máxima de correos reales que puede entregar por día.",
            "message_interval_minutes": "Espera mínima entre dos entregas consecutivas.",
            "timezone_name": "Zona horaria usada para interpretar los días y horarios de entrega.",
            "approval_mode": (
                "Aprobar toda la campaña confirma una sola vez la audiencia, el mensaje, los "
                "PDFs y el calendario. La revisión individual permite editar cada mensaje."
            ),
            "reminder_enabled": (
                "Si no hay una respuesta humana, se enviará como máximo un recordatorio en el "
                "mismo hilo."
            ),
            "reminder_delay_days": (
                "Se cuentan desde el envío confirmado y se respeta el próximo horario permitido."
            ),
            "relevance_threshold": (
                "Puntaje mínimo, de 0 a 100, para considerar que un prospecto es relevante."
            ),
            "llm_model": (
                "Nombre exacto del modelo que usará el proveedor de inteligencia artificial."
            ),
        }

    def __init__(self, *args: Any, workspace: Workspace | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if workspace is None:
            return
        cast(
            "forms.ModelMultipleChoiceField[SearchCategory]",
            self.fields["categories"],
        ).queryset = SearchCategory.objects.filter(
            workspace=workspace,
            active=True,
            archived_at__isnull=True,
        ).order_by("sort_order", "name")
        cast(
            "forms.ModelMultipleChoiceField[SearchZone]",
            self.fields["zones"],
        ).queryset = (
            SearchZone.objects.filter(
                workspace=workspace,
                active=True,
                selectable=True,
                archived_at__isnull=True,
            )
            .select_related("parent")
            .order_by("province_name", "sort_order", "name")
        )
        cast(
            "forms.ModelMultipleChoiceField[SearchZone]",
            self.fields["provinces"],
        ).queryset = SearchZone.objects.filter(
            workspace=workspace,
            level=SearchZone.Level.PROVINCE,
            active=True,
            archived_at__isnull=True,
        ).order_by("name")
        cast(
            "forms.ModelChoiceField[Catalog]",
            self.fields["catalog"],
        ).queryset = Catalog.objects.filter(
            workspace=workspace,
            active=True,
            missing=False,
        ).order_by("name", "-version")
        cast(
            "forms.ModelMultipleChoiceField[Catalog]",
            self.fields["catalogs"],
        ).queryset = Catalog.objects.filter(
            workspace=workspace,
            active=True,
            missing=False,
        ).order_by("name", "-version")

    def clean_weekdays(self) -> list[int]:
        return [int(day) for day in self.cleaned_data["weekdays"]]

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        catalogs = list(cleaned.get("catalogs") or [])
        legacy_catalog = cleaned.get("catalog")
        if not catalogs and isinstance(legacy_catalog, Catalog):
            catalogs = [legacy_catalog]
            cleaned["catalogs"] = catalogs
        if not catalogs:
            self.add_error("catalogs", "Elegí al menos un PDF para enviar con la propuesta.")
        else:
            total_bytes = sum(item.byte_size for item in catalogs)
            if any(item.byte_size > 15 * 1024 * 1024 for item in catalogs):
                self.add_error("catalogs", "Cada PDF debe pesar como máximo 15 MiB.")
            if total_bytes > 17 * 1024 * 1024:
                self.add_error(
                    "catalogs",
                    "Los PDFs seleccionados superan el límite combinado de 17 MiB.",
                )
            cleaned["catalog"] = catalogs[0]
        zones = list(cleaned.get("zones") or [])
        provinces = set(cleaned.get("provinces") or [])
        if any(zone.parent_id is not None for zone in zones) and not provinces:
            self.add_error(
                "provinces",
                "Elegí la provincia de los distritos seleccionados.",
            )
        if provinces:
            missing_parent = [
                zone.name
                for zone in zones
                if zone.parent_id is not None and zone.parent not in provinces
            ]
            if missing_parent:
                self.add_error(
                    "zones",
                    "Hay distritos elegidos dentro de una provincia que no está seleccionada.",
                )
            empty_provinces = [
                province.name
                for province in provinces
                if not any(zone.parent_id == province.pk for zone in zones)
            ]
            if empty_provinces:
                self.add_error(
                    "zones",
                    "Elegí al menos un distrito en cada provincia seleccionada.",
                )
        if cleaned.get("delivery_mode") == Campaign.DeliveryMode.LIVE and not cleaned.get(
            "confirm_live"
        ):
            self.add_error(
                "confirm_live", "Confirmá explícitamente antes de habilitar el modo en vivo."
            )
        return cleaned
