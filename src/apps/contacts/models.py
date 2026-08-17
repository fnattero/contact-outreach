from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import TimestampedUUIDModel


class Organization(TimestampedUUIDModel):
    """A workspace-global business, independent from any one campaign."""

    class Source(models.TextChoices):
        DISCOVERY = "DISCOVERY", "Descubrimiento"
        INBOUND = "INBOUND", "Respuesta recibida"
        MANUAL = "MANUAL", "Carga manual"
        SUPPRESSION = "SUPPRESSION", "Restricción anterior"
        MIGRATION = "MIGRATION", "Datos existentes"

    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.PROTECT,
        related_name="organizations",
    )
    name = models.CharField(max_length=300, blank=True)
    normalized_name = models.CharField(max_length=300, blank=True)
    address = models.CharField(max_length=500, blank=True)
    normalized_address = models.CharField(max_length=500, blank=True)
    website = models.URLField(max_length=1000, blank=True)
    business_domain = models.CharField(max_length=253, blank=True)
    phone = models.CharField(max_length=80, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.DISCOVERY)
    provenance = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("normalized_name", "created_at")
        indexes = [
            models.Index(fields=("workspace", "normalized_name")),
            models.Index(fields=("workspace", "business_domain")),
        ]

    def __str__(self) -> str:
        return self.name or "Organización sin nombre"


class OrganizationIdentity(TimestampedUUIDModel):
    """A stable deduplication key owned by exactly one organization."""

    class Kind(models.TextChoices):
        GERS_ID = "GERS_ID", "GERS ID"
        PROVIDER_ID = "PROVIDER_ID", "Identificador del proveedor"
        BUSINESS_DOMAIN = "BUSINESS_DOMAIN", "Dominio empresarial"
        NAME_ADDRESS = "NAME_ADDRESS", "Nombre y dirección"
        EMAIL = "EMAIL", "Email heredado"

    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.PROTECT,
        related_name="organization_identities",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="identities",
    )
    kind = models.CharField(max_length=30, choices=Kind.choices)
    value = models.CharField(max_length=1000, blank=True)
    value_hash = models.CharField(max_length=64)
    provider = models.CharField(max_length=50, blank=True)
    provenance = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("kind", "created_at")
        constraints = [
            models.UniqueConstraint(
                fields=("workspace", "kind", "value_hash"),
                name="org_identity_workspace_unique",
            )
        ]
        indexes = [models.Index(fields=("organization", "kind"))]

    def clean(self) -> None:
        super().clean()
        if self.organization_id and self.workspace_id:
            organization_workspace_id = getattr(self.organization, "workspace_id", None)
            if organization_workspace_id != self.workspace_id:
                raise ValidationError("La identidad debe pertenecer al espacio de la organización.")


class EmailAddress(TimestampedUUIDModel):
    """A validated communication channel; organizations may own several."""

    class Validity(models.TextChoices):
        VALID = "VALID", "Válido"
        INVALID = "INVALID", "Inválido"
        TRANSIENT = "TRANSIENT", "Validación pendiente"
        UNKNOWN = "UNKNOWN", "Sin validar"

    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.PROTECT,
        related_name="email_addresses",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="email_addresses",
    )
    original_email = models.CharField(max_length=320)
    normalized_email = models.CharField(max_length=320)
    domain = models.CharField(max_length=253, blank=True)
    label = models.CharField(max_length=120, blank=True)
    is_preferred = models.BooleanField(default=False)
    provenance = models.CharField(max_length=120, blank=True)
    source_url = models.URLField(max_length=1000, blank=True)
    source_content_hash = models.CharField(max_length=64, blank=True)
    provider_order = models.PositiveIntegerField(default=0)
    validity = models.CharField(
        max_length=20,
        choices=Validity.choices,
        default=Validity.UNKNOWN,
    )
    validated_at = models.DateTimeField(blank=True, null=True)
    invalid_reason = models.CharField(max_length=200, blank=True)
    invalidated_at = models.DateTimeField(blank=True, null=True)
    legacy_prospect_email = models.OneToOneField(
        "prospects.ProspectEmail",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="contact_email_address",
    )

    class Meta:
        ordering = ("provider_order", "created_at")
        constraints = [
            models.UniqueConstraint(
                fields=("workspace", "normalized_email"),
                name="email_address_workspace_unique",
            ),
            models.UniqueConstraint(
                fields=("organization",),
                condition=Q(is_preferred=True),
                name="organization_one_preferred_email",
            ),
        ]
        indexes = [
            models.Index(fields=("organization", "validity")),
            models.Index(fields=("workspace", "normalized_email")),
        ]

    def clean(self) -> None:
        super().clean()
        if self.organization_id and self.workspace_id:
            organization_workspace_id = getattr(self.organization, "workspace_id", None)
            if organization_workspace_id != self.workspace_id:
                raise ValidationError("El email debe pertenecer al espacio de la organización.")

    def __str__(self) -> str:
        return self.original_email


class Contact(TimestampedUUIDModel):
    """An established relationship that excludes its organization from outreach."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Contacto actual"
        DO_NOT_CONTACT = "DO_NOT_CONTACT", "No contactar"
        UNSUBSCRIBED = "UNSUBSCRIBED", "Baja solicitada"

    class CreatedReason(models.TextChoices):
        HUMAN_REPLY = "HUMAN_REPLY", "Respondió una persona"
        MANUAL_ENTRY = "MANUAL_ENTRY", "Carga manual"
        MANUAL_RESTRICTION = "MANUAL_RESTRICTION", "Restricción manual"
        UNSUBSCRIBE = "UNSUBSCRIBE", "Baja solicitada"

    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.PROTECT,
        related_name="contacts",
    )
    organization = models.OneToOneField(
        Organization,
        on_delete=models.PROTECT,
        related_name="contact",
    )
    preferred_email = models.ForeignKey(
        EmailAddress,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="preferred_for_contacts",
    )
    name = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_reason = models.CharField(max_length=30, choices=CreatedReason.choices)
    source_inbound_message_id = models.UUIDField(blank=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="created_contacts",
    )
    automation_suspended = models.BooleanField(default=False)
    last_interaction_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("-last_interaction_at", "-created_at")
        indexes = [models.Index(fields=("workspace", "status"))]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.organization_id and self.workspace_id:
            organization_workspace_id = getattr(self.organization, "workspace_id", None)
            if organization_workspace_id != self.workspace_id:
                errors["organization"] = "El contacto debe pertenecer al mismo espacio."
        if self.preferred_email_id and self.organization_id:
            preferred_organization_id = getattr(self.preferred_email, "organization_id", None)
            if preferred_organization_id != self.organization_id:
                errors["preferred_email"] = "El email preferido debe pertenecer al contacto."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return self.name or str(self.organization)


class CommunicationRestriction(TimestampedUUIDModel):
    """An auditable contact-wide or address-specific communication restriction."""

    class Scope(models.TextChoices):
        CONTACT = "CONTACT", "Todo el contacto"
        EMAIL = "EMAIL", "Sólo este email"

    class Kind(models.TextChoices):
        UNSUBSCRIBE = "UNSUBSCRIBE", "Baja"
        BOUNCE = "BOUNCE", "Rebote"
        MANUAL = "MANUAL", "Manual"

    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.PROTECT,
        related_name="communication_restrictions",
    )
    scope = models.CharField(max_length=20, choices=Scope.choices)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    contact = models.ForeignKey(
        Contact,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="restrictions",
    )
    email_address = models.ForeignKey(
        EmailAddress,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="restrictions",
    )
    source = models.CharField(max_length=100)
    evidence = models.TextField(blank=True)
    source_inbound_message_id = models.UUIDField(blank=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="created_communication_restrictions",
    )
    revoked_at = models.DateTimeField(blank=True, null=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="revoked_communication_restrictions",
    )
    revocation_reason = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(scope="CONTACT", contact__isnull=False, email_address__isnull=True)
                    | Q(scope="EMAIL", contact__isnull=True, email_address__isnull=False)
                ),
                name="restriction_scope_target_consistent",
            ),
            models.CheckConstraint(
                condition=~Q(kind="BOUNCE") | Q(scope="EMAIL"),
                name="bounce_restriction_email_only",
            ),
            models.CheckConstraint(
                condition=~Q(kind="UNSUBSCRIBE", revoked_at__isnull=False),
                name="unsubscribe_restriction_permanent",
            ),
            models.CheckConstraint(
                condition=(
                    Q(revoked_at__isnull=True, revoked_by__isnull=True, revocation_reason="")
                    | Q(
                        revoked_at__isnull=False,
                        revoked_by__isnull=False,
                    )
                    & ~Q(revocation_reason="")
                ),
                name="restriction_revocation_audited",
            ),
            models.UniqueConstraint(
                fields=("contact", "kind"),
                condition=Q(contact__isnull=False, revoked_at__isnull=True),
                name="active_contact_restriction_unique",
            ),
            models.UniqueConstraint(
                fields=("email_address", "kind"),
                condition=Q(email_address__isnull=False, revoked_at__isnull=True),
                name="active_email_restriction_unique",
            ),
        ]
        indexes = [models.Index(fields=("workspace", "kind", "revoked_at"))]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.scope == self.Scope.CONTACT:
            if not self.contact_id or self.email_address_id:
                errors["scope"] = "Elegí un contacto completo para esta restricción."
            elif getattr(self.contact, "workspace_id", None) != self.workspace_id:
                errors["contact"] = "El contacto debe pertenecer al mismo espacio."
        elif self.scope == self.Scope.EMAIL:
            if not self.email_address_id or self.contact_id:
                errors["scope"] = "Elegí un único email para esta restricción."
            elif getattr(self.email_address, "workspace_id", None) != self.workspace_id:
                errors["email_address"] = "El email debe pertenecer al mismo espacio."
        if self.kind == self.Kind.BOUNCE and self.scope != self.Scope.EMAIL:
            errors["kind"] = "Un rebote sólo invalida el email que rebotó."
        if self.kind == self.Kind.UNSUBSCRIBE and self.revoked_at is not None:
            errors["revoked_at"] = "Una baja no se puede quitar."
        if self.revoked_at is None:
            if self.revoked_by_id or self.revocation_reason:
                errors["revoked_at"] = "La reversión está incompleta."
        elif not self.revoked_by_id or not self.revocation_reason.strip():
            errors["revocation_reason"] = "Indicá quién revierte la restricción y por qué."
        if errors:
            raise ValidationError(errors)

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            previous = type(self).objects.only("kind", "revoked_at").get(pk=self.pk)
            if previous.kind == self.Kind.UNSUBSCRIBE and (
                self.kind != previous.kind or self.revoked_at != previous.revoked_at
            ):
                raise ValidationError("Una baja no se puede modificar ni quitar.")
        self.full_clean()
        super().save(*args, **kwargs)


class Conversation(TimestampedUUIDModel):
    """One Gmail thread belonging to one established Contact."""

    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.PROTECT,
        related_name="conversations",
    )
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="conversations")
    connection = models.ForeignKey(
        "mailbox.GmailConnection",
        on_delete=models.PROTECT,
        related_name="conversations",
    )
    gmail_thread_id = models.CharField(max_length=255)
    subject = models.CharField(max_length=255, blank=True)
    automation_suspended = models.BooleanField(default=False)
    last_message_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("-last_message_at", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=("connection", "gmail_thread_id"),
                name="gmail_connection_thread_unique",
            )
        ]
        indexes = [models.Index(fields=("contact", "-last_message_at"))]

    def clean(self) -> None:
        super().clean()
        if self.contact_id and self.workspace_id:
            if getattr(self.contact, "workspace_id", None) != self.workspace_id:
                raise ValidationError("La conversación debe pertenecer al mismo espacio.")


class CampaignEnrollment(TimestampedUUIDModel):
    """An organization's campaign-specific audience participation."""

    class State(models.TextChoices):
        DISCOVERED = "DISCOVERED", "Descubierto"
        ELIGIBLE = "ELIGIBLE", "Puede recibir la propuesta"
        PREPARED = "PREPARED", "Mensaje preparado"
        INITIAL_SENT = "INITIAL_SENT", "Propuesta enviada"
        RESPONDED = "RESPONDED", "Respondió"
        REMINDER_DUE = "REMINDER_DUE", "Recordatorio pendiente"
        REMINDER_SENT = "REMINDER_SENT", "Recordatorio enviado"
        INELIGIBLE = "INELIGIBLE", "No se puede contactar"
        CANCELLED = "CANCELLED", "Cancelado"

    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.PROTECT,
        related_name="campaign_enrollments",
    )
    campaign = models.ForeignKey(
        "campaigns.Campaign",
        on_delete=models.PROTECT,
        related_name="enrollments",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="campaign_enrollments",
    )
    selected_email = models.ForeignKey(
        EmailAddress,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="campaign_enrollments",
    )
    state = models.CharField(max_length=30, choices=State.choices, default=State.DISCOVERED)
    source = models.CharField(max_length=50, default="LEGACY_PROSPECT")
    exclusion_reason = models.CharField(max_length=200, blank=True)
    initial_sent_at = models.DateTimeField(blank=True, null=True)
    replied_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("campaign", "created_at")
        constraints = [
            models.UniqueConstraint(
                fields=("campaign", "organization"),
                name="campaign_organization_enrollment_unique",
            ),
            models.UniqueConstraint(
                fields=("campaign", "selected_email"),
                condition=Q(selected_email__isnull=False),
                name="campaign_email_enrollment_unique",
            ),
        ]
        indexes = [
            models.Index(fields=("campaign", "state")),
            models.Index(fields=("organization", "-created_at")),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.organization_id and self.workspace_id:
            if getattr(self.organization, "workspace_id", None) != self.workspace_id:
                errors["organization"] = "La organización debe pertenecer al mismo espacio."
        if self.selected_email_id and self.organization_id:
            if getattr(self.selected_email, "organization_id", None) != self.organization_id:
                errors["selected_email"] = "El email elegido debe pertenecer a la organización."
        if errors:
            raise ValidationError(errors)
