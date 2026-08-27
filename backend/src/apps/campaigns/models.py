from __future__ import annotations

from collections.abc import Collection, Iterable
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, router, transaction
from django.db.models import Q

from apps.campaigns.content import (
    INITIAL_BODY,
    INITIAL_SUBJECT,
    REFERRED_PROPOSAL_BODY,
    REFERRED_PROPOSAL_SUBJECT,
    REMINDER_BODY,
)
from apps.catalogs.models import Catalog
from apps.configuration.models import SearchCategory, SearchZone
from apps.core.models import TimestampedUUIDModel


def default_weekdays() -> list[int]:
    return [0, 1, 2, 3, 4]


class Campaign(TimestampedUUIDModel):
    IMMUTABLE_AFTER_START = (
        "workspace_id",
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
        "overture_snapshot_id",
        "overture_release_id",
        "overture_min_confidence",
        "website_fetcher",
        "llm_provider",
        "llm_base_url",
        "llm_model",
        "catalog_id",
        "delivery_mode",
        "approval_mode",
        "reminder_enabled",
        "reminder_delay_days",
        "initial_subject_snapshot",
        "initial_body_snapshot",
        "reminder_body_snapshot",
        "referred_subject_snapshot",
        "referred_body_snapshot",
        "signature_snapshot",
        "settings_snapshot",
        "profile_snapshot",
        "prompt_snapshot",
    )

    class State(models.TextChoices):
        DRAFT = "DRAFT", "Borrador"
        DISCOVERING = "DISCOVERING", "Buscando destinatarios"
        AWAITING_APPROVAL = "AWAITING_APPROVAL", "Lista para aprobar"
        RUNNING = "RUNNING", "En curso"
        PAUSED = "PAUSED", "Pausada"
        CANCELLED = "CANCELLED", "Cancelada"
        COMPLETED = "COMPLETED", "Completada"
        STOPPED_ERROR = "STOPPED_ERROR", "Detenida por error"

    class DiscoveryState(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        RUNNING = "RUNNING", "En curso"
        TARGET_REACHED = "TARGET_REACHED", "Objetivo alcanzado"
        EXHAUSTED_QUERIES = "EXHAUSTED_QUERIES", "Consultas agotadas"
        EXHAUSTED_RAW_LIMIT = "EXHAUSTED_RAW_LIMIT", "Límite crudo agotado"
        EXHAUSTED_COST = "EXHAUSTED_COST", "Costo agotado"
        FAILED_PROVIDER = "FAILED_PROVIDER", "Proveedor falló"

    class DeliveryMode(models.TextChoices):
        DRY_RUN = "DRY_RUN", "Simulación"
        REVIEW_ONLY = "REVIEW_ONLY", "Solo revisión (sin Gmail)"
        LIVE = "LIVE", "En vivo"

    class ApprovalMode(models.TextChoices):
        CAMPAIGN = "CAMPAIGN", "Aprobar toda la campaña"
        PER_MESSAGE = "PER_MESSAGE", "Revisar cada mensaje"

    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.PROTECT,
        related_name="campaigns",
    )
    name = models.CharField(max_length=200)
    state = models.CharField(max_length=20, choices=State.choices, default=State.DRAFT)
    discovery_state = models.CharField(
        max_length=30,
        choices=DiscoveryState.choices,
        default=DiscoveryState.PENDING,
    )
    discovery_stop_reason = models.CharField(max_length=200, blank=True)
    delivery_mode = models.CharField(
        max_length=20,
        choices=DeliveryMode.choices,
        default=DeliveryMode.DRY_RUN,
    )
    approval_mode = models.CharField(
        max_length=20,
        choices=ApprovalMode.choices,
        default=ApprovalMode.CAMPAIGN,
    )
    reminder_enabled = models.BooleanField(default=False)
    reminder_delay_days = models.PositiveSmallIntegerField(
        default=3,
        validators=(MinValueValidator(1),),
    )
    status_reason = models.TextField(blank=True)
    location_text = models.CharField(
        max_length=300,
        default="Ciudad Autónoma de Buenos Aires, Argentina",
    )
    objective = models.PositiveIntegerField(default=300)
    max_raw_records = models.PositiveIntegerField(default=3000)
    cost_limit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("10.00"))
    cost_currency = models.CharField(max_length=3, default="USD")
    daily_limit = models.PositiveIntegerField(default=30)
    message_interval_minutes = models.PositiveIntegerField(default=5)
    weekdays = models.JSONField(default=default_weekdays)
    window_start = models.TimeField(default="09:00")
    window_end = models.TimeField(default="17:00")
    timezone_name = models.CharField(max_length=64, default="America/Argentina/Buenos_Aires")
    relevance_threshold = models.PositiveSmallIntegerField(default=70)
    extractor_provider = models.CharField(max_length=50, default="fake")
    overture_snapshot = models.ForeignKey(
        "overture.OvertureDatasetSnapshot",
        on_delete=models.PROTECT,
        related_name="campaigns",
        blank=True,
        null=True,
    )
    overture_release = models.ForeignKey(
        "overture.OvertureRelease",
        on_delete=models.PROTECT,
        related_name="campaigns",
        blank=True,
        null=True,
    )
    overture_min_confidence = models.DecimalField(
        max_digits=4,
        decimal_places=3,
        default=Decimal("0.750"),
        validators=(MinValueValidator(0), MaxValueValidator(1)),
    )
    website_fetcher = models.CharField(max_length=20, default="fake")
    llm_provider = models.CharField(max_length=50, default="fake")
    llm_base_url = models.URLField(blank=True)
    llm_model = models.CharField(max_length=120, default="fake-deterministic")
    catalog = models.ForeignKey(Catalog, on_delete=models.PROTECT, related_name="campaigns")
    settings_snapshot = models.JSONField(default=dict, blank=True)
    profile_snapshot = models.JSONField(default=dict, blank=True)
    prompt_snapshot = models.JSONField(default=dict, blank=True)
    initial_subject_snapshot = models.CharField(max_length=255, default=INITIAL_SUBJECT)
    initial_body_snapshot = models.TextField(default=INITIAL_BODY)
    reminder_body_snapshot = models.TextField(default=REMINDER_BODY)
    referred_subject_snapshot = models.CharField(
        max_length=255,
        default=REFERRED_PROPOSAL_SUBJECT,
    )
    referred_body_snapshot = models.TextField(default=REFERRED_PROPOSAL_BODY)
    signature_snapshot = models.TextField(blank=True)
    template_revision_snapshot = models.JSONField(default=dict, blank=True)
    audience_hash = models.CharField(max_length=64, blank=True)
    content_hash = models.CharField(max_length=64, blank=True)
    attachment_hash = models.CharField(max_length=64, blank=True)
    schedule_hash = models.CharField(max_length=64, blank=True)
    approved_at = models.DateTimeField(blank=True, null=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="approved_campaigns",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="campaigns",
    )
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)

    categories: models.ManyToManyField[SearchCategory, CampaignCategorySelection] = (
        models.ManyToManyField(SearchCategory, through="CampaignCategorySelection")
    )
    zones: models.ManyToManyField[SearchZone, CampaignZoneSelection] = models.ManyToManyField(
        SearchZone, through="CampaignZoneSelection"
    )

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("state", "-created_at")),
            models.Index(
                fields=("workspace", "state", "-created_at"),
                name="campaign_ws_state_created_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(objective__gt=0), name="campaign_objective_positive"
            ),
            models.CheckConstraint(
                condition=Q(max_raw_records__gt=0), name="campaign_raw_limit_positive"
            ),
            models.CheckConstraint(
                condition=Q(cost_limit__gte=0), name="campaign_cost_nonnegative"
            ),
            models.CheckConstraint(
                condition=Q(daily_limit__gt=0), name="campaign_daily_limit_positive"
            ),
            models.CheckConstraint(
                condition=Q(message_interval_minutes__gt=0), name="campaign_interval_positive"
            ),
            models.CheckConstraint(
                condition=Q(reminder_delay_days__gte=1),
                name="campaign_reminder_delay_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(approved_at__isnull=True, approved_by__isnull=True)
                    | Q(approved_at__isnull=False, approved_by__isnull=False)
                ),
                name="campaign_approval_fields_consistent",
            ),
            models.CheckConstraint(
                condition=Q(relevance_threshold__gte=0, relevance_threshold__lte=100),
                name="campaign_relevance_0_100",
            ),
            models.CheckConstraint(
                condition=Q(
                    overture_min_confidence__gte=0,
                    overture_min_confidence__lte=1,
                ),
                name="campaign_overture_confidence_0_1",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.max_raw_records < self.objective:
            errors["max_raw_records"] = "El límite crudo no puede ser menor que el objetivo."
        if self.window_start >= self.window_end:
            errors["window_end"] = "El fin del horario debe ser posterior al inicio."
        if (
            not isinstance(self.weekdays, list)
            or not self.weekdays
            or any(not isinstance(day, int) or day < 0 or day > 6 for day in self.weekdays)
        ):
            errors["weekdays"] = "Seleccioná al menos un día válido."
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError:
            errors["timezone_name"] = "La zona horaria no es válida."
        if self.extractor_provider not in {"fake", "overture"}:
            errors["extractor_provider"] = "El proveedor de extracción no es válido."
        if self.extractor_provider == "overture" and self.state != self.State.DRAFT:
            if self.overture_snapshot_id is None:
                errors["overture_snapshot"] = "La campaña iniciada debe fijar un snapshot Overture."
        if self.website_fetcher not in {"fake", "http"}:
            errors["website_fetcher"] = "El lector de sitios web no es válido."
        if self.llm_provider not in {"fake", "ollama", "openai-compatible"}:
            errors["llm_provider"] = "El proveedor IA no es válido."
        if self.llm_provider != "fake" and not self.llm_base_url:
            errors["llm_base_url"] = "El proveedor IA seleccionado requiere una URL base."
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.workspace_id and self.created_by_id:
            self.workspace_id = self.created_by.membership.workspace_id
        if not self._state.adding:
            previous = Campaign.objects.get(pk=self.pk)
            if previous.state != self.State.DRAFT and any(
                getattr(previous, field) != getattr(self, field)
                for field in self.IMMUTABLE_AFTER_START
            ):
                raise ValidationError("La configuración iniciada de una campaña es inmutable.")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


def _lock_draft_campaigns(campaign_ids: set[Any], *, using: str) -> None:
    if not campaign_ids:
        return
    states = dict(
        Campaign.objects.using(using)
        .select_for_update()
        .filter(pk__in=campaign_ids)
        .values_list("pk", "state")
    )
    if set(states) != campaign_ids or any(
        state != Campaign.State.DRAFT for state in states.values()
    ):
        raise ValidationError("Las selecciones de una campaña iniciada son inmutables.")


class DraftCampaignSelectionQuerySet(models.QuerySet[Any]):
    def _campaign_ids(self) -> set[Any]:
        return set(self.order_by().values_list("campaign_id", flat=True).distinct())

    def update(self, **kwargs: Any) -> int:
        if "campaign" in kwargs or "campaign_id" in kwargs:
            raise ValidationError("No se admite reasignar selecciones de campaña masivamente.")
        with transaction.atomic(using=self.db):
            campaign_ids = self._campaign_ids()
            _lock_draft_campaigns(campaign_ids, using=self.db)
            return super().update(**kwargs)

    def delete(self) -> tuple[int, dict[str, int]]:
        with transaction.atomic(using=self.db):
            _lock_draft_campaigns(self._campaign_ids(), using=self.db)
            return super().delete()

    def bulk_update(
        self,
        objs: Iterable[Any],
        fields: Iterable[str],
        batch_size: int | None = None,
    ) -> int:
        objects = list(objs)
        field_names = tuple(fields)
        if "campaign" in field_names or "campaign_id" in field_names:
            raise ValidationError("No se admite reasignar selecciones de campaña masivamente.")
        object_ids = [item.pk for item in objects if getattr(item, "pk", None) is not None]
        with transaction.atomic(using=self.db):
            campaign_ids = set(
                self.model.objects.using(self.db)
                .filter(pk__in=object_ids)
                .values_list("campaign_id", flat=True)
            )
            _lock_draft_campaigns(campaign_ids, using=self.db)
            return super().bulk_update(objects, field_names, batch_size=batch_size)

    def bulk_create(
        self,
        objs: Iterable[Any],
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: Collection[str] | None = None,
        unique_fields: Collection[str] | None = None,
    ) -> list[Any]:
        objects = list(objs)
        if update_conflicts:
            raise ValidationError("No se admite actualizar selecciones mediante conflictos.")
        if any(getattr(item, "campaign_id", None) is None for item in objects):
            raise ValidationError("Toda selección debe pertenecer a una campaña borrador.")
        campaign_ids = {item.campaign_id for item in objects}
        with transaction.atomic(using=self.db):
            _lock_draft_campaigns(campaign_ids, using=self.db)
            return super().bulk_create(
                objects,
                batch_size=batch_size,
                ignore_conflicts=ignore_conflicts,
                update_conflicts=update_conflicts,
                update_fields=update_fields,
                unique_fields=unique_fields,
            )


class DraftCampaignSelection(TimestampedUUIDModel):
    campaign_id: Any
    objects = DraftCampaignSelectionQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        database = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=database):
            campaign_id = getattr(self, "campaign_id", None)
            campaign_ids = {campaign_id} if campaign_id is not None else set()
            if not self._state.adding:
                current = type(self).objects.using(database).only("campaign_id").get(pk=self.pk)
                current_campaign_id = current.campaign_id
                if current_campaign_id != campaign_id:
                    raise ValidationError("No se admite reasignar selecciones de campaña.")
                campaign_ids.add(current_campaign_id)
            if not campaign_ids:
                raise ValidationError("Toda selección debe pertenecer a una campaña borrador.")
            _lock_draft_campaigns(campaign_ids, using=database)
            super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        database = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=database):
            current = type(self).objects.using(database).only("campaign_id").get(pk=self.pk)
            _lock_draft_campaigns({current.campaign_id}, using=database)
            return super().delete(*args, **kwargs)


class CampaignCategorySelection(DraftCampaignSelection):
    campaign = models.ForeignKey(
        Campaign, on_delete=models.CASCADE, related_name="category_selections"
    )
    category = models.ForeignKey(
        SearchCategory, on_delete=models.PROTECT, related_name="campaign_selections"
    )
    name_snapshot = models.CharField(max_length=160)
    normalized_name_snapshot = models.CharField(max_length=160)
    rules_snapshot = models.JSONField(default=list, blank=True)
    rules_revision_snapshot = models.PositiveIntegerField(default=1)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "name_snapshot")
        constraints = [
            models.UniqueConstraint(
                fields=("campaign", "category"), name="campaign_category_unique"
            )
        ]


class CampaignZoneSelection(DraftCampaignSelection):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="zone_selections")
    zone = models.ForeignKey(
        SearchZone, on_delete=models.PROTECT, related_name="campaign_selections"
    )
    name_snapshot = models.CharField(max_length=160)
    location_snapshot = models.CharField(max_length=300)
    boundary_geojson_snapshot = models.JSONField(default=dict, blank=True)
    boundary_bbox_snapshot = models.JSONField(default=list, blank=True)
    boundary_hash_snapshot = models.CharField(max_length=64, blank=True)
    boundary_revision_snapshot = models.PositiveIntegerField(default=1)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "name_snapshot")
        constraints = [
            models.UniqueConstraint(fields=("campaign", "zone"), name="campaign_zone_unique")
        ]


class CampaignAttachment(DraftCampaignSelection):
    """Ordered immutable catalog selection frozen before discovery starts."""

    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    catalog = models.ForeignKey(
        Catalog,
        on_delete=models.PROTECT,
        related_name="campaign_attachments",
    )
    position = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ("position", "created_at")
        constraints = [
            models.UniqueConstraint(
                fields=("campaign", "catalog"),
                name="campaign_attachment_catalog_unique",
            ),
            models.UniqueConstraint(
                fields=("campaign", "position"),
                name="campaign_attachment_position_unique",
            ),
        ]


class CampaignCoverageSelection(DraftCampaignSelection):
    """Pinned district-to-province-partition mapping used by one campaign."""

    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name="coverage_selections",
    )
    release = models.ForeignKey(
        "overture.OvertureRelease",
        on_delete=models.PROTECT,
        related_name="campaign_selections",
    )
    partition = models.ForeignKey(
        "overture.OvertureCoveragePartition",
        on_delete=models.PROTECT,
        related_name="campaign_selections",
    )
    province = models.ForeignKey(
        SearchZone,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="campaign_province_coverage",
    )
    district = models.ForeignKey(
        SearchZone,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="campaign_district_coverage",
    )
    province_code_snapshot = models.CharField(max_length=20)
    province_name_snapshot = models.CharField(max_length=160)
    district_code_snapshot = models.CharField(max_length=120)
    district_name_snapshot = models.CharField(max_length=160)
    district_level_snapshot = models.CharField(max_length=20)
    district_label_snapshot = models.CharField(max_length=40)
    boundary_geojson_snapshot = models.JSONField(default=dict)
    boundary_bbox_snapshot = models.JSONField(default=list)
    boundary_hash_snapshot = models.CharField(max_length=64)
    boundary_revision_snapshot = models.PositiveIntegerField(default=1)
    boundary_source_snapshot = models.CharField(max_length=300, blank=True)
    boundary_attribution_snapshot = models.CharField(max_length=500, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "district_name_snapshot")
        constraints = [
            models.UniqueConstraint(
                fields=("campaign", "district"),
                condition=Q(district__isnull=False),
                name="campaign_coverage_district_unique",
            ),
            models.UniqueConstraint(
                fields=("campaign", "sort_order"),
                name="campaign_coverage_order_unique",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.partition_id and self.release_id != self.partition.release_id:
            errors["release"] = "Todas las coberturas deben usar el release de su partición."
        if self.campaign_id and self.campaign.overture_release_id:
            if self.release_id != self.campaign.overture_release_id:
                errors["release"] = "Una campaña no puede mezclar versiones de datos."
        if self.partition_id and self.province_code_snapshot != self.partition.province_code:
            errors["partition"] = "La cobertura elegida pertenece a otra provincia."
        if self.district_id:
            district = self.district
            if district is None or self.province_code_snapshot != district.province_code:
                errors["district"] = "El distrito elegido pertenece a otra provincia."
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


class SearchQuery(TimestampedUUIDModel):
    class State(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        RUNNING = "RUNNING", "En curso"
        SUCCEEDED = "SUCCEEDED", "Completada"
        RETRY_WAIT = "RETRY_WAIT", "Esperando reintento"
        FAILED_PERMANENT = "FAILED_PERMANENT", "Falló"
        CANCELLED = "CANCELLED", "Cancelada"

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="search_queries")
    coverage_selection = models.ForeignKey(
        CampaignCoverageSelection,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="search_queries",
    )
    category_snapshot = models.CharField(max_length=160)
    zone_snapshot = models.CharField(max_length=160)
    location_snapshot = models.CharField(max_length=300)
    query_text = models.CharField(max_length=700)
    normalized_query = models.CharField(max_length=700)
    criteria_json = models.JSONField(default=dict, blank=True)
    zone_boundary_hash = models.CharField(max_length=64, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    state = models.CharField(max_length=30, choices=State.choices, default=State.PENDING)
    run_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ("sort_order",)
        constraints = [
            models.UniqueConstraint(
                fields=("campaign", "normalized_query"), name="campaign_query_unique"
            )
        ]


class SearchRun(TimestampedUUIDModel):
    class State(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        RUNNING = "RUNNING", "En curso"
        SUCCEEDED = "SUCCEEDED", "Completada"
        RETRY_WAIT = "RETRY_WAIT", "Esperando reintento"
        FAILED_PERMANENT = "FAILED_PERMANENT", "Falló permanentemente"
        CANCELLED = "CANCELLED", "Cancelada"

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="search_runs")
    query = models.ForeignKey(SearchQuery, on_delete=models.CASCADE, related_name="runs")
    provider = models.CharField(max_length=50)
    overture_snapshot = models.ForeignKey(
        "overture.OvertureDatasetSnapshot",
        on_delete=models.PROTECT,
        related_name="search_runs",
        blank=True,
        null=True,
    )
    overture_partition = models.ForeignKey(
        "overture.OvertureCoveragePartition",
        on_delete=models.PROTECT,
        related_name="search_runs",
        blank=True,
        null=True,
    )
    idempotency_key = models.CharField(max_length=200, unique=True)
    request_json = models.JSONField(default=dict)
    response_json = models.JSONField(default=dict, blank=True)
    response_hash = models.CharField(max_length=64, blank=True)
    provider_request_id = models.CharField(max_length=200, blank=True)
    state = models.CharField(max_length=30, choices=State.choices, default=State.PENDING)
    cursor = models.CharField(max_length=500, blank=True)
    requested_limit = models.PositiveIntegerField()
    attempts = models.PositiveIntegerField(default=0)
    raw_count = models.PositiveIntegerField(default=0)
    email_count = models.PositiveIntegerField(default=0)
    no_email_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    cost_reserved = models.DecimalField(max_digits=14, decimal_places=6, default=Decimal("0"))
    cost_estimated = models.DecimalField(max_digits=14, decimal_places=6, blank=True, null=True)
    cost_actual = models.DecimalField(max_digits=14, decimal_places=6, blank=True, null=True)
    currency = models.CharField(max_length=3, default="USD")
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    raw_persisted_at = models.DateTimeField(blank=True, null=True)
    processed_at = models.DateTimeField(blank=True, null=True)
    next_poll_at = models.DateTimeField(blank=True, null=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ("created_at",)
        indexes = [
            models.Index(fields=("state", "next_poll_at")),
            models.Index(fields=("campaign", "state")),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("provider", "provider_request_id"),
                condition=~Q(provider_request_id=""),
                name="provider_request_id_unique",
            ),
            models.CheckConstraint(
                condition=Q(cost_reserved__gte=0), name="search_run_reserved_cost_nonnegative"
            ),
        ]


class ProviderUsage(TimestampedUUIDModel):
    provider = models.CharField(max_length=50)
    operation = models.CharField(max_length=100)
    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT, related_name="provider_usage")
    run = models.OneToOneField(SearchRun, on_delete=models.PROTECT, related_name="usage")
    units = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    estimated_cost = models.DecimalField(max_digits=14, decimal_places=6, blank=True, null=True)
    actual_cost = models.DecimalField(max_digits=14, decimal_places=6, blank=True, null=True)
    currency = models.CharField(max_length=3)
    request_id = models.CharField(max_length=200, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("created_at",)
        indexes = [models.Index(fields=("provider", "campaign", "created_at"))]


class OutboundMessage(TimestampedUUIDModel):
    class Kind(models.TextChoices):
        FIRST_CONTACT = "FIRST_CONTACT", "Primer contacto (histórico)"
        INITIAL = "INITIAL", "Propuesta inicial"
        CAMPAIGN_REMINDER = "CAMPAIGN_REMINDER", "Recordatorio de campaña"
        MANUAL_REPLY = "MANUAL_REPLY", "Respuesta manual"
        AUTOMATIC_REPLY = "AUTOMATIC_REPLY", "Respuesta automática"
        REFERRED_PROPOSAL = "REFERRED_PROPOSAL", "Propuesta reenviada"
        REDIRECT_ACK = "REDIRECT_ACK", "Confirmación de reenvío"
        SCHEDULED_CONTACT = "SCHEDULED_CONTACT", "Contacto programado"

    class State(models.TextChoices):
        PREPARED = "PREPARED", "Preparado"
        REVIEW_READY = "REVIEW_READY", "Listo para revisar"
        QUEUED = "QUEUED", "En cola"
        SENDING = "SENDING", "Enviando"
        RECONCILING = "RECONCILING", "Reconciliando"
        SENT = "SENT", "Enviado"
        DRY_RUN_COMPLETED = "DRY_RUN_COMPLETED", "Simulado"
        INELIGIBLE = "INELIGIBLE", "Ya no se puede enviar"
        SEND_FAILED = "SEND_FAILED", "Falló"
        CANCELLED = "CANCELLED", "Cancelado"

    kind = models.CharField(max_length=30, choices=Kind.choices, default=Kind.INITIAL)
    campaign = models.ForeignKey(
        Campaign,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="messages",
    )
    organization = models.ForeignKey(
        "contacts.Organization",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="outbound_messages",
    )
    campaign_enrollment = models.ForeignKey(
        "contacts.CampaignEnrollment",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="outbound_messages",
    )
    contact = models.ForeignKey(
        "contacts.Contact",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="outbound_messages",
    )
    conversation = models.ForeignKey(
        "contacts.Conversation",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="outbound_messages",
    )
    prospect = models.ForeignKey(
        "prospects.Prospect",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="outbound_messages",
    )
    prospect_email = models.ForeignKey(
        "prospects.ProspectEmail",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="outbound_messages",
    )
    email_address = models.ForeignKey(
        "contacts.EmailAddress",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="outbound_messages",
    )
    analysis = models.OneToOneField(
        "prospects.AIAnalysis",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="outbound_message",
    )
    parent_inbound = models.ForeignKey(
        "mailbox.InboundMessage",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="outbound_replies",
    )
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="manual_replies",
    )
    recipient = models.CharField(max_length=320)
    recipient_normalized = models.CharField(max_length=320)
    subject = models.CharField(max_length=255)
    body_text = models.TextField()
    signature_snapshot = models.TextField(blank=True)
    content_hash = models.CharField(max_length=64, blank=True)
    catalog = models.ForeignKey(
        Catalog,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="messages",
    )
    catalog_version = models.PositiveIntegerField(blank=True, null=True)
    state = models.CharField(max_length=30, choices=State.choices, default=State.PREPARED)
    delivery_mode = models.CharField(max_length=20, choices=Campaign.DeliveryMode.choices)
    content_revision = models.PositiveIntegerField(default=1)
    last_edited_at = models.DateTimeField(blank=True, null=True)
    last_edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="edited_outbound_messages",
    )
    approved_at = models.DateTimeField(blank=True, null=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="approved_outbound_messages",
    )
    idempotency_key = models.CharField(max_length=200, unique=True)
    semantic_action_key = models.CharField(max_length=200, blank=True)
    reminder_for = models.OneToOneField(
        "self",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="campaign_reminder",
    )
    contact_sequence = models.PositiveIntegerField(blank=True, null=True)
    message_id = models.CharField(max_length=255, blank=True)
    in_reply_to = models.CharField(max_length=255, blank=True)
    references = models.JSONField(default=list, blank=True)
    mime_sha256 = models.CharField(max_length=64, blank=True)
    mime_size = models.PositiveBigIntegerField(blank=True, null=True)
    gmail_message_id = models.CharField(max_length=255, blank=True)
    gmail_thread_id = models.CharField(max_length=255, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(blank=True, null=True)
    scheduled_for = models.DateTimeField(blank=True, null=True)
    delivery_reserved_at = models.DateTimeField(blank=True, null=True)
    sending_started_at = models.DateTimeField(blank=True, null=True)
    last_attempt_at = models.DateTimeField(blank=True, null=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    simulated_at = models.DateTimeField(blank=True, null=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("campaign", "state")),
            models.Index(fields=("state", "next_attempt_at")),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("recipient_normalized", "contact_sequence"),
                condition=Q(
                    kind__in=("FIRST_CONTACT", "INITIAL"),
                    contact_sequence__isnull=False,
                ),
                name="first_contact_email_sequence_unique",
            ),
            models.UniqueConstraint(
                fields=("message_id",),
                condition=~Q(message_id=""),
                name="outbound_rfc_message_id_unique",
            ),
            models.UniqueConstraint(
                fields=("gmail_message_id",),
                condition=~Q(gmail_message_id=""),
                name="outbound_gmail_message_id_unique",
            ),
            models.UniqueConstraint(
                fields=("parent_inbound",),
                condition=Q(kind="MANUAL_REPLY", parent_inbound__isnull=False),
                name="manual_reply_parent_inbound_unique",
            ),
            models.UniqueConstraint(
                fields=("semantic_action_key",),
                condition=~Q(semantic_action_key=""),
                name="outbound_semantic_action_unique",
            ),
            models.CheckConstraint(
                condition=(
                    Q(approved_at__isnull=True, approved_by__isnull=True)
                    | Q(approved_at__isnull=False, approved_by__isnull=False)
                ),
                name="message_approval_fields_consistent",
            ),
        ]


class OutboundAttachment(TimestampedUUIDModel):
    """Immutable attachment snapshot used to reproduce the exact MIME effect."""

    message = models.ForeignKey(
        OutboundMessage,
        on_delete=models.PROTECT,
        related_name="attachments",
    )
    catalog = models.ForeignKey(
        Catalog,
        on_delete=models.PROTECT,
        related_name="outbound_attachments",
    )
    position = models.PositiveSmallIntegerField()
    catalog_version = models.PositiveIntegerField()
    storage_key = models.CharField(max_length=300)
    filename = models.CharField(max_length=255)
    byte_size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)

    class Meta:
        ordering = ("position", "created_at")
        constraints = [
            models.UniqueConstraint(
                fields=("message", "catalog"),
                name="outbound_attachment_catalog_unique",
            ),
            models.UniqueConstraint(
                fields=("message", "position"),
                name="outbound_attachment_position_unique",
            ),
            models.CheckConstraint(
                condition=Q(byte_size__gt=0),
                name="outbound_attachment_size_positive",
            ),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Los adjuntos de un mensaje preparado son inmutables.")
        super().save(*args, **kwargs)


class CampaignDeliveryReservation(TimestampedUUIDModel):
    class Status(models.TextChoices):
        RESERVED = "RESERVED", "Reservado"
        CONSUMED = "CONSUMED", "Enviado"
        RELEASED = "RELEASED", "Liberado antes del envío"

    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.PROTECT,
        related_name="campaign_delivery_reservations",
    )
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.PROTECT,
        related_name="delivery_reservations",
    )
    message = models.OneToOneField(
        OutboundMessage,
        on_delete=models.PROTECT,
        related_name="same_day_reservation",
    )
    email_address = models.ForeignKey(
        "contacts.EmailAddress",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="delivery_reservations",
    )
    normalized_email = models.CharField(max_length=320)
    local_date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RESERVED)
    consumed_at = models.DateTimeField(blank=True, null=True)
    released_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("workspace", "normalized_email", "local_date"),
                condition=~Q(status="RELEASED"),
                name="workspace_email_campaign_day_unique",
            )
        ]
        indexes = [models.Index(fields=("campaign", "local_date"))]
