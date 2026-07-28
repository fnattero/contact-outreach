from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from apps.core.models import TimestampedUUIDModel
from apps.overture.matching import normalize_search_text, normalize_taxonomy_code

DEFAULT_EMAIL_DRAFTING_PROMPT = (
    "Priorizá un tono profesional, directo y prudente. Explicá una relación posible con los "
    "productos del perfil sin asumir que el negocio ya los compra o necesita."
)


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", " ", normalized.strip()).casefold()


class BusinessProfile(TimestampedUUIDModel):
    workspace = models.OneToOneField(
        "accounts.Workspace",
        on_delete=models.PROTECT,
        related_name="business_profile",
    )
    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="business_profile",
    )
    company_name = models.CharField(max_length=200)
    salesperson_name = models.CharField(max_length=200)
    phone = models.CharField(max_length=50, blank=True)
    whatsapp = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)
    products = models.TextField(blank=True)
    differentiators = models.TextField(blank=True)
    address = models.CharField(max_length=300)
    website = models.URLField(blank=True)
    signature = models.TextField()
    additional_instructions = models.TextField(blank=True)
    relevance_threshold = models.PositiveSmallIntegerField(
        default=70,
        validators=(MinValueValidator(0), MaxValueValidator(100)),
    )
    profile_version = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(relevance_threshold__gte=0, relevance_threshold__lte=100),
                name="business_profile_relevance_0_100",
            )
        ]

    def __str__(self) -> str:
        return self.company_name

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.workspace_id and self.owner_id:
            self.workspace_id = self.owner.membership.workspace_id
        super().save(*args, **kwargs)


class IntegrationConfiguration(TimestampedUUIDModel):
    class SecretSource(models.TextChoices):
        ENVIRONMENT = "ENVIRONMENT", "Entorno"
        ENCRYPTED = "ENCRYPTED", "Dashboard cifrado"
        NONE = "NONE", "Sin configurar"

    class ExtractorProvider(models.TextChoices):
        FAKE = "fake", "Mock (sin red)"
        OVERTURE = "overture", "Overture Maps Places"

    class LLMProvider(models.TextChoices):
        FAKE = "fake", "Mock (sin red)"
        OLLAMA = "ollama", "Ollama"
        OPENAI_COMPATIBLE = "openai-compatible", "OpenAI compatible"

    class WebsiteFetcher(models.TextChoices):
        FAKE = "fake", "Mock (sin red)"
        HTTP = "http", "HTTP real seguro"

    class GmailProvider(models.TextChoices):
        FAKE = "fake", "Fake (sin red)"
        API = "api", "Google Gmail"

    workspace = models.OneToOneField(
        "accounts.Workspace",
        on_delete=models.PROTECT,
        related_name="integration_configuration",
    )
    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="integration_configuration",
    )
    extractor_provider = models.CharField(
        max_length=30,
        choices=ExtractorProvider.choices,
        default=ExtractorProvider.FAKE,
    )
    overture_min_confidence = models.DecimalField(
        max_digits=4,
        decimal_places=3,
        default=Decimal("0.750"),
        validators=(MinValueValidator(0), MaxValueValidator(1)),
    )
    website_fetcher = models.CharField(
        max_length=20,
        choices=WebsiteFetcher.choices,
        default=WebsiteFetcher.FAKE,
    )
    llm_provider = models.CharField(
        max_length=30,
        choices=LLMProvider.choices,
        default=LLMProvider.FAKE,
    )
    llm_model = models.CharField(max_length=120, default="fake-deterministic")
    llm_api_key_encrypted = models.TextField(blank=True, editable=False)
    llm_api_key_source = models.CharField(
        max_length=20,
        choices=SecretSource.choices,
        default=SecretSource.ENVIRONMENT,
    )
    ollama_base_url = models.URLField(default="http://127.0.0.1:11434")
    openai_compatible_base_url = models.URLField(blank=True)
    gmail_provider = models.CharField(
        max_length=20,
        choices=GmailProvider.choices,
        default=GmailProvider.FAKE,
    )
    gmail_oauth_client_id = models.CharField(max_length=500, blank=True)
    gmail_oauth_client_secret_encrypted = models.TextField(blank=True, editable=False)
    gmail_oauth_client_secret_source = models.CharField(
        max_length=20,
        choices=SecretSource.choices,
        default=SecretSource.ENVIRONMENT,
    )
    revision = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(overture_min_confidence__gte=0, overture_min_confidence__lte=1),
                name="integration_overture_confidence_0_1",
            ),
            models.CheckConstraint(
                condition=~Q(llm_api_key_source="ENCRYPTED") | ~Q(llm_api_key_encrypted=""),
                name="integration_llm_cipher_required",
            ),
            models.CheckConstraint(
                condition=~Q(gmail_oauth_client_secret_source="ENCRYPTED")
                | ~Q(gmail_oauth_client_secret_encrypted=""),
                name="integration_gmail_cipher_required",
            ),
        ]

    def __str__(self) -> str:
        return f"Integraciones de {self.workspace.name}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.workspace_id and self.owner_id:
            self.workspace_id = self.owner.membership.workspace_id
        super().save(*args, **kwargs)


class PromptConfiguration(TimestampedUUIDModel):
    workspace = models.OneToOneField(
        "accounts.Workspace",
        on_delete=models.PROTECT,
        related_name="prompt_configuration",
    )
    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="prompt_configuration",
    )
    email_drafting_prompt = models.TextField(default=DEFAULT_EMAIL_DRAFTING_PROMPT)
    revision = models.PositiveIntegerField(default=1, editable=False)

    def __str__(self) -> str:
        return f"Prompts de {self.workspace.name}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.workspace_id and self.owner_id:
            self.workspace_id = self.owner.membership.workspace_id
        super().save(*args, **kwargs)


class WorkspaceMessageTemplateRevision(TimestampedUUIDModel):
    """An approved, placeholder-free workspace default for deterministic outreach."""

    objects: models.Manager[WorkspaceMessageTemplateRevision] = models.Manager()

    class Kind(models.TextChoices):
        INITIAL = "INITIAL", "Propuesta inicial"
        REMINDER = "REMINDER", "Recordatorio"
        REFERRED_PROPOSAL = "REFERRED_PROPOSAL", "Propuesta reenviada"

    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.PROTECT,
        related_name="message_template_revisions",
    )
    kind = models.CharField(max_length=30, choices=Kind.choices)
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    revision = models.PositiveIntegerField()
    content_hash = models.CharField(max_length=64)
    approved_at = models.DateTimeField()
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="approved_workspace_message_templates",
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("kind", "-revision")
        constraints = [
            models.UniqueConstraint(
                fields=("workspace", "kind", "revision"),
                name="workspace_message_template_revision_unique",
            ),
            models.UniqueConstraint(
                fields=("workspace", "kind"),
                condition=Q(active=True),
                name="workspace_message_template_one_active",
            ),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            previous = type(self).objects.get(pk=self.pk)
            immutable = (
                "workspace_id",
                "kind",
                "subject",
                "body",
                "revision",
                "content_hash",
                "approved_at",
                "approved_by_id",
            )
            if any(getattr(previous, field) != getattr(self, field) for field in immutable):
                raise ValidationError("Un mensaje aprobado no se edita; creá una nueva revisión.")
        super().save(*args, **kwargs)


class SearchCategory(TimestampedUUIDModel):
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.PROTECT,
        related_name="search_categories",
    )
    name = models.CharField(max_length=160)
    normalized_name = models.CharField(max_length=160, editable=False)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    rules_revision = models.PositiveIntegerField(default=1, editable=False)
    archived_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("sort_order", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("workspace", "normalized_name"),
                condition=Q(archived_at__isnull=True),
                name="configuration_category_workspace_active_name_unique",
            )
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.workspace_id:
            from apps.accounts.signals import get_workspace

            self.workspace = get_workspace()
        self.name = re.sub(r"\s+", " ", self.name.strip())
        self.normalized_name = normalize_name(self.name)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name

    @property
    def search_variants(self) -> tuple[str, ...]:
        variants: list[str] = []
        for rule in self.rules.all():
            if not rule.active:
                continue
            for term in rule.name_terms:
                if isinstance(term, str) and term not in variants:
                    variants.append(term)
        return tuple(variants)


class SearchCategoryRule(TimestampedUUIDModel):
    category = models.ForeignKey(
        SearchCategory,
        on_delete=models.CASCADE,
        related_name="rules",
    )
    taxonomy_code = models.CharField(max_length=160, blank=True)
    name_terms = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "created_at")
        constraints = [
            models.CheckConstraint(
                condition=~Q(taxonomy_code="") | ~Q(name_terms=[]),
                name="configuration_category_rule_has_matcher",
            )
        ]

    def clean(self) -> None:
        super().clean()
        raw_taxonomy_code = self.taxonomy_code.strip().casefold()
        self.taxonomy_code = normalize_taxonomy_code(raw_taxonomy_code)
        if raw_taxonomy_code and (len(raw_taxonomy_code) > 160 or not self.taxonomy_code):
            raise ValidationError({"taxonomy_code": "El código taxonómico no es válido."})
        if not isinstance(self.name_terms, list):
            raise ValidationError({"name_terms": "Los términos deben ser una lista."})
        if len(self.name_terms) > 30:
            raise ValidationError({"name_terms": "Cada regla admite como máximo 30 términos."})
        normalized_terms: list[str] = []
        for raw_term in self.name_terms:
            if not isinstance(raw_term, str):
                raise ValidationError({"name_terms": "Todos los términos deben ser texto."})
            term = raw_term.strip().casefold()
            if len(term) > 80 or not re.fullmatch(r"[\w\s-]+\*?", term):
                raise ValidationError(
                    {"name_terms": "Usá frases literales; sólo se permite * al final."}
                )
            prefix = term.endswith("*")
            literal = normalize_search_text(term[:-1] if prefix else term)
            if not literal or (prefix and " " in literal):
                raise ValidationError(
                    {"name_terms": "El comodín * sólo puede seguir a un único token."}
                )
            normalized = f"{literal}*" if prefix else literal
            if normalized not in normalized_terms:
                normalized_terms.append(normalized)
        self.name_terms = normalized_terms
        if not self.taxonomy_code and not self.name_terms:
            raise ValidationError("La regla necesita taxonomía, términos o ambos.")

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


class SearchZone(TimestampedUUIDModel):
    class Kind(models.TextChoices):
        NEIGHBORHOOD = "NEIGHBORHOOD", "Barrio"
        CUSTOM = "CUSTOM", "Personalizada"

    class Level(models.TextChoices):
        COUNTRY = "COUNTRY", "País"
        PROVINCE = "PROVINCE", "Provincia"
        DISTRICT = "DISTRICT", "Partido, departamento o comuna"
        NEIGHBORHOOD = "NEIGHBORHOOD", "Barrio"
        CUSTOM = "CUSTOM", "Personalizada"

    class Source(models.TextChoices):
        GEOREF = "GEOREF", "GeoRef / IGN"
        BUENOS_AIRES_DATA = "BUENOS_AIRES_DATA", "Buenos Aires Data"
        CUSTOM = "CUSTOM", "Carga manual"
        LEGACY = "LEGACY", "Configuración anterior"

    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.PROTECT,
        related_name="search_zones",
    )
    official_code = models.CharField(max_length=120)
    name = models.CharField(max_length=160)
    normalized_name = models.CharField(max_length=160, editable=False)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.CUSTOM)
    level = models.CharField(max_length=20, choices=Level.choices, default=Level.CUSTOM)
    parent = models.ForeignKey(
        "self",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    province_code = models.CharField(max_length=20, blank=True, db_index=True)
    province_name = models.CharField(max_length=160, blank=True)
    selectable = models.BooleanField(default=True)
    label_plural = models.CharField(max_length=40, blank=True)
    source = models.CharField(max_length=30, choices=Source.choices, default=Source.CUSTOM)
    location_text = models.CharField(max_length=300)
    boundary_geojson = models.JSONField(default=dict, blank=True)
    boundary_bbox = models.JSONField(default=list, blank=True)
    boundary_hash = models.CharField(max_length=64, blank=True, db_index=True)
    boundary_revision = models.PositiveIntegerField(default=1)
    boundary_source = models.CharField(max_length=300, blank=True)
    boundary_attribution = models.CharField(max_length=500, blank=True)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    archived_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("province_name", "sort_order", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("workspace", "parent", "official_code"),
                name="configuration_zone_hierarchy_code_unique",
                nulls_distinct=False,
            ),
            models.UniqueConstraint(
                fields=("workspace", "parent", "normalized_name"),
                condition=Q(archived_at__isnull=True),
                name="configuration_zone_sibling_name_unique",
                nulls_distinct=False,
            ),
            models.CheckConstraint(
                condition=(Q(level="CUSTOM") | ~Q(official_code="")),
                name="configuration_official_zone_has_code",
            ),
            models.CheckConstraint(
                condition=(
                    Q(level__in=("COUNTRY", "PROVINCE", "CUSTOM"))
                    | (~Q(province_code="") & ~Q(province_name=""))
                ),
                name="configuration_local_zone_has_province",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.level == self.Level.CUSTOM and not self.official_code:
            self.official_code = f"custom-{self.pk}"
        if self.level == self.Level.PROVINCE:
            if self.parent_id is not None:
                errors["parent"] = "Una provincia no puede depender de otra zona."
            if not self.province_code:
                self.province_code = self.official_code
            if not self.province_name:
                self.province_name = self.name
            if self.selectable:
                errors["selectable"] = "Elegí partidos, departamentos o barrios, no la provincia."
        elif self.level in {self.Level.DISTRICT, self.Level.NEIGHBORHOOD}:
            if self.parent_id is None:
                errors["parent"] = "La zona debe estar dentro de una provincia."
            else:
                parent = self.parent
                if parent is None:
                    errors["parent"] = "La zona debe estar dentro de una provincia."
                elif parent.level != self.Level.PROVINCE:
                    errors["parent"] = "La zona debe depender directamente de una provincia."
                elif self.workspace_id != parent.workspace_id:
                    errors["parent"] = "La provincia debe pertenecer al mismo espacio de trabajo."
                else:
                    if self.province_code and self.province_code != parent.official_code:
                        errors["province_code"] = "El código no coincide con la provincia elegida."
                    self.province_code = parent.official_code
                    self.province_name = parent.name
        elif self.level == self.Level.CUSTOM:
            if self.parent_id is None:
                self.province_code = ""
                self.province_name = ""
            else:
                parent = self.parent
                if parent is None or self.workspace_id != parent.workspace_id:
                    errors["parent"] = "La zona debe pertenecer al mismo espacio de trabajo."
                elif parent.level != self.Level.PROVINCE:
                    errors["parent"] = "La zona personalizada debe depender de una provincia."
                else:
                    self.province_code = parent.official_code
                    self.province_name = parent.name
        if (
            self.level
            not in {
                self.Level.DISTRICT,
                self.Level.NEIGHBORHOOD,
                self.Level.CUSTOM,
            }
            and self.selectable
        ):
            errors["selectable"] = "Este nivel se usa para ordenar y no se puede seleccionar."
        if errors:
            raise ValidationError(errors)
        if not self.boundary_geojson:
            if self.active:
                raise ValidationError(
                    {"boundary_geojson": "Una zona activa necesita un límite GeoJSON."}
                )
            self.boundary_bbox = []
            self.boundary_hash = ""
            return
        from apps.overture.geometry import validate_geojson

        validated = validate_geojson(self.boundary_geojson)
        self.boundary_geojson = validated.geojson
        self.boundary_bbox = list(validated.bbox)
        self.boundary_hash = validated.sha256

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.workspace_id:
            from apps.accounts.signals import get_workspace

            self.workspace = get_workspace()
        self.name = re.sub(r"\s+", " ", self.name.strip())
        self.normalized_name = normalize_name(self.name)
        if self.level == self.Level.CUSTOM and not self.official_code:
            self.official_code = f"custom-{self.pk}"
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name
