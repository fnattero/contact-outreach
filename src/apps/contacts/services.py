from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from email.utils import parseaddr
from functools import partial
from typing import TYPE_CHECKING, Any

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import Workspace
from apps.accounts.permissions import Capability, require_user_capability
from apps.audit.services import record_event
from apps.compliance.models import SuppressionEntry
from apps.compliance.services import lock_email_eligibility, normalize_email, suppress_email
from apps.contacts.models import (
    CampaignEnrollment,
    CommunicationRestriction,
    Contact,
    Conversation,
    EmailAddress,
    Organization,
    OrganizationIdentity,
)
from apps.prospects.email_validation import MXResolver, MXStatus

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from apps.campaigns.models import Campaign, OutboundMessage
    from apps.mailbox.models import InboundMessage
    from apps.prospects.models import Prospect, ProspectEmail


class OrganizationResolutionConflict(ValidationError):
    """Raised when durable identity keys point at different organizations."""


@dataclass(frozen=True, slots=True)
class IdentityClaim:
    kind: str
    value: str = ""
    value_hash: str = ""
    provider: str = ""
    provenance: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OrganizationResolution:
    organization: Organization
    email_address: EmailAddress | None


@dataclass(frozen=True, slots=True)
class EnrollmentEligibility:
    eligible: bool
    code: str = ""
    message: str = ""


@dataclass(frozen=True, slots=True)
class InboundContactEffect:
    contact: Contact | None
    conversation: Conversation | None
    promoted: bool
    cancelled_messages: int


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized_identity_value(kind: str, value: str) -> str:
    candidate = value.strip()
    if kind == OrganizationIdentity.Kind.EMAIL:
        return normalize_email(candidate)
    if kind in {
        OrganizationIdentity.Kind.BUSINESS_DOMAIN,
        OrganizationIdentity.Kind.GERS_ID,
        OrganizationIdentity.Kind.PROVIDER_ID,
    }:
        return candidate.rstrip(".").casefold()
    if kind == OrganizationIdentity.Kind.NAME_ADDRESS:
        return " ".join(candidate.split()).casefold()
    return candidate


def _prepared_claims(claims: Sequence[IdentityClaim]) -> tuple[IdentityClaim, ...]:
    prepared: dict[tuple[str, str], IdentityClaim] = {}
    for claim in claims:
        if claim.kind not in OrganizationIdentity.Kind.values:
            raise ValidationError("El tipo de identidad de la organización no es válido.")
        value = _normalized_identity_value(claim.kind, claim.value) if claim.value else ""
        value_hash = claim.value_hash.casefold().strip() or (_sha256(value) if value else "")
        if len(value_hash) != 64 or any(
            character not in "0123456789abcdef" for character in value_hash
        ):
            raise ValidationError("La identidad de la organización no tiene un hash válido.")
        key = (claim.kind, value_hash)
        prepared.setdefault(
            key,
            IdentityClaim(
                kind=claim.kind,
                value=value,
                value_hash=value_hash,
                provider=claim.provider.strip()[:50],
                provenance=dict(claim.provenance),
            ),
        )
    return tuple(prepared[key] for key in sorted(prepared))


def _advisory_lock(namespace: str, value: str) -> None:
    if connection.vendor != "postgresql":
        return
    digest = hashlib.sha256(f"{namespace}\x1f{value}".encode()).digest()
    lock_id = int.from_bytes(digest[:8], byteorder="big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_id])


def _lock_resolution_keys(
    *,
    workspace_id: object,
    claims: Sequence[IdentityClaim],
    normalized_email: str,
) -> None:
    keys = [f"identity:{claim.kind}:{claim.value_hash}" for claim in claims]
    if normalized_email:
        keys.append(f"email:{normalized_email}")
    for key in sorted(keys):
        _advisory_lock(f"workspace:{workspace_id}", key)


def _workspace_for_campaign(campaign: Campaign) -> Workspace:
    workspace_id = getattr(campaign, "workspace_id", None)
    if workspace_id:
        return Workspace.objects.get(pk=workspace_id)
    try:
        return campaign.created_by.membership.workspace
    except (AttributeError, Workspace.DoesNotExist) as exc:
        raise ValidationError("La campaña no tiene un espacio de trabajo válido.") from exc


def _fill_organization_fields(
    organization: Organization,
    *,
    name: str,
    address: str,
    website: str,
    business_domain: str,
    phone: str,
) -> None:
    normalized_name = " ".join(name.split()).casefold()
    normalized_address = " ".join(address.split()).casefold()
    values = {
        "name": name.strip()[:300],
        "normalized_name": normalized_name[:300],
        "address": address.strip()[:500],
        "normalized_address": normalized_address[:500],
        "website": website.strip()[:1000],
        "business_domain": business_domain.strip().rstrip(".").casefold()[:253],
        "phone": phone.strip()[:80],
    }
    changed: list[str] = []
    for field_name, value in values.items():
        if value and not getattr(organization, field_name):
            setattr(organization, field_name, value)
            changed.append(field_name)
    if changed:
        organization.save(update_fields=(*changed, "updated_at"))


def _get_or_create_identity(
    *,
    workspace: Workspace,
    organization: Organization,
    claim: IdentityClaim,
) -> OrganizationIdentity:
    existing = (
        OrganizationIdentity.objects.select_for_update()
        .filter(
            workspace=workspace,
            kind=claim.kind,
            value_hash=claim.value_hash,
        )
        .first()
    )
    if existing is not None:
        if existing.organization_id != organization.pk:
            raise OrganizationResolutionConflict(
                "Una identidad ya pertenece a otra organización. Revisá los datos antes de seguir."
            )
        return existing
    try:
        with transaction.atomic():
            return OrganizationIdentity.objects.create(
                workspace=workspace,
                organization=organization,
                kind=claim.kind,
                value=claim.value[:1000],
                value_hash=claim.value_hash,
                provider=claim.provider,
                provenance=dict(claim.provenance),
            )
    except IntegrityError as exc:
        existing = OrganizationIdentity.objects.select_for_update().get(
            workspace=workspace,
            kind=claim.kind,
            value_hash=claim.value_hash,
        )
        if existing.organization_id != organization.pk:
            raise OrganizationResolutionConflict(
                "Una identidad ya pertenece a otra organización. Revisá los datos antes de seguir."
            ) from exc
        return existing


def _get_or_create_email_address(
    *,
    workspace: Workspace,
    organization: Organization,
    original_email: str,
    normalized_email: str,
    validity: str,
    label: str,
    provenance: str,
    source_url: str,
    source_content_hash: str,
    provider_order: int,
    validated_at: Any,
    invalid_reason: str,
    invalidated_at: Any,
    legacy_prospect_email: ProspectEmail | None,
    prefer: bool,
) -> EmailAddress:
    existing = (
        EmailAddress.objects.select_for_update()
        .filter(
            workspace=workspace,
            normalized_email=normalized_email,
        )
        .first()
    )
    if existing is not None and existing.organization_id != organization.pk:
        raise OrganizationResolutionConflict(
            "Ese email ya pertenece a otra organización. Revisá el contacto antes de seguir."
        )
    if existing is None:
        can_prefer = (
            prefer
            and not EmailAddress.objects.filter(
                organization=organization,
                is_preferred=True,
            ).exists()
        )
        try:
            with transaction.atomic():
                existing = EmailAddress.objects.create(
                    workspace=workspace,
                    organization=organization,
                    original_email=original_email.strip()[:320] or normalized_email,
                    normalized_email=normalized_email,
                    domain=normalized_email.rsplit("@", 1)[1],
                    label=label.strip()[:120],
                    is_preferred=can_prefer,
                    provenance=provenance.strip()[:120],
                    source_url=source_url.strip()[:1000],
                    source_content_hash=source_content_hash.strip()[:64],
                    provider_order=max(0, provider_order),
                    validity=validity,
                    validated_at=validated_at,
                    invalid_reason=invalid_reason.strip()[:200],
                    invalidated_at=invalidated_at,
                    legacy_prospect_email=legacy_prospect_email,
                )
        except IntegrityError as exc:
            existing = EmailAddress.objects.select_for_update().get(
                workspace=workspace,
                normalized_email=normalized_email,
            )
            if existing.organization_id != organization.pk:
                raise OrganizationResolutionConflict(
                    "Ese email ya pertenece a otra organización. "
                    "Revisá el contacto antes de seguir."
                ) from exc
    changes: list[str] = []
    if legacy_prospect_email is not None and existing.legacy_prospect_email_id is None:
        existing.legacy_prospect_email = legacy_prospect_email
        changes.append("legacy_prospect_email")
    if validity == EmailAddress.Validity.INVALID and existing.validity != validity:
        existing.validity = validity
        existing.invalid_reason = invalid_reason.strip()[:200]
        existing.invalidated_at = invalidated_at or timezone.now()
        changes.extend(("validity", "invalid_reason", "invalidated_at"))
    elif validity == EmailAddress.Validity.VALID and existing.validity in {
        EmailAddress.Validity.UNKNOWN,
        EmailAddress.Validity.TRANSIENT,
    }:
        existing.validity = validity
        existing.validated_at = validated_at or timezone.now()
        existing.invalid_reason = ""
        existing.invalidated_at = None
        changes.extend(("validity", "validated_at", "invalid_reason", "invalidated_at"))
    if (
        prefer
        and not existing.is_preferred
        and not EmailAddress.objects.filter(
            organization=organization,
            is_preferred=True,
        )
        .exclude(pk=existing.pk)
        .exists()
    ):
        existing.is_preferred = True
        changes.append("is_preferred")
    if changes:
        existing.save(update_fields=(*dict.fromkeys(changes), "updated_at"))
    return existing


@transaction.atomic
def resolve_organization(
    *,
    workspace: Workspace,
    identities: Sequence[IdentityClaim] = (),
    email: str = "",
    name: str = "",
    address: str = "",
    website: str = "",
    business_domain: str = "",
    phone: str = "",
    source: str = Organization.Source.DISCOVERY,
    provenance: Mapping[str, Any] | None = None,
    email_validity: str = EmailAddress.Validity.UNKNOWN,
    email_label: str = "",
    email_provenance: str = "",
    email_source_url: str = "",
    email_source_content_hash: str = "",
    email_provider_order: int = 0,
    email_validated_at: Any = None,
    email_invalid_reason: str = "",
    email_invalidated_at: Any = None,
    legacy_prospect_email: ProspectEmail | None = None,
    prefer_email: bool = False,
) -> OrganizationResolution:
    """Resolve global business identity without silently merging conflicting owners."""

    if source not in Organization.Source.values:
        raise ValidationError("El origen de la organización no es válido.")
    if email_validity not in EmailAddress.Validity.values:
        raise ValidationError("El estado del email no es válido.")
    normalized = normalize_email(email) if email else ""
    prepared = list(_prepared_claims(identities))
    if normalized:
        email_hash = _sha256(normalized)
        if not any(
            claim.kind == OrganizationIdentity.Kind.EMAIL and claim.value_hash == email_hash
            for claim in prepared
        ):
            prepared.append(
                IdentityClaim(
                    kind=OrganizationIdentity.Kind.EMAIL,
                    value=normalized,
                    value_hash=email_hash,
                    provenance=dict(provenance or {}),
                )
            )
            prepared.sort(key=lambda item: (item.kind, item.value_hash))
    if not prepared and not normalized:
        raise ValidationError("Se necesita al menos una identidad o un email para la organización.")

    _lock_resolution_keys(
        workspace_id=workspace.pk,
        claims=prepared,
        normalized_email=normalized,
    )
    identity_query = Q()
    for claim in prepared:
        identity_query |= Q(kind=claim.kind, value_hash=claim.value_hash)
    candidate_ids = set(
        OrganizationIdentity.objects.select_for_update()
        .filter(identity_query, workspace=workspace)
        .values_list("organization_id", flat=True)
    )
    existing_email = None
    if normalized:
        existing_email = (
            EmailAddress.objects.select_for_update()
            .filter(
                workspace=workspace,
                normalized_email=normalized,
            )
            .first()
        )
        if existing_email is not None:
            candidate_ids.add(existing_email.organization_id)
    if len(candidate_ids) > 1:
        raise OrganizationResolutionConflict(
            "Los datos coinciden con organizaciones distintas. Hace falta una revisión manual."
        )
    if candidate_ids:
        organization = Organization.objects.select_for_update().get(pk=next(iter(candidate_ids)))
    else:
        organization = Organization.objects.create(
            workspace=workspace,
            name=name.strip()[:300],
            normalized_name=" ".join(name.split()).casefold()[:300],
            address=address.strip()[:500],
            normalized_address=" ".join(address.split()).casefold()[:500],
            website=website.strip()[:1000],
            business_domain=business_domain.strip().rstrip(".").casefold()[:253],
            phone=phone.strip()[:80],
            source=source,
            provenance=dict(provenance or {}),
        )
    if organization.workspace_id != workspace.pk:
        raise OrganizationResolutionConflict("La organización pertenece a otro espacio de trabajo.")
    _fill_organization_fields(
        organization,
        name=name,
        address=address,
        website=website,
        business_domain=business_domain,
        phone=phone,
    )
    for claim in prepared:
        _get_or_create_identity(workspace=workspace, organization=organization, claim=claim)

    email_address = existing_email
    if normalized:
        email_address = _get_or_create_email_address(
            workspace=workspace,
            organization=organization,
            original_email=email,
            normalized_email=normalized,
            validity=email_validity,
            label=email_label,
            provenance=email_provenance,
            source_url=email_source_url,
            source_content_hash=email_source_content_hash,
            provider_order=email_provider_order,
            validated_at=email_validated_at,
            invalid_reason=email_invalid_reason,
            invalidated_at=email_invalidated_at,
            legacy_prospect_email=legacy_prospect_email,
            prefer=prefer_email,
        )
    return OrganizationResolution(organization=organization, email_address=email_address)


def _prospect_identity_claims(prospect: Prospect, provider: str) -> tuple[IdentityClaim, ...]:
    claims: list[IdentityClaim] = []
    for identity in prospect.identities.all():
        kind = identity.kind
        if kind == "PROVIDER_ID" and provider == "overture":
            kind = OrganizationIdentity.Kind.GERS_ID
        claims.append(
            IdentityClaim(
                kind=kind,
                value_hash=identity.value_hash,
                provider=provider,
                provenance={"legacy_prospect_id": str(prospect.pk)},
            )
        )
    if prospect.business_domain:
        claims.append(
            IdentityClaim(
                kind=OrganizationIdentity.Kind.BUSINESS_DOMAIN,
                value=prospect.business_domain,
                provenance={"legacy_prospect_id": str(prospect.pk)},
            )
        )
    if prospect.normalized_name and prospect.normalized_address:
        claims.append(
            IdentityClaim(
                kind=OrganizationIdentity.Kind.NAME_ADDRESS,
                value=f"{prospect.normalized_name}\x1f{prospect.normalized_address}",
                provenance={"legacy_prospect_id": str(prospect.pk)},
            )
        )
    return tuple(claims)


def _legacy_email_validity(prospect_email: ProspectEmail) -> str:
    if prospect_email.is_invalid or prospect_email.mx_status == prospect_email.MXStatus.INVALID:
        return EmailAddress.Validity.INVALID
    if prospect_email.mx_status == prospect_email.MXStatus.VALID:
        return EmailAddress.Validity.VALID
    if prospect_email.mx_status == prospect_email.MXStatus.TRANSIENT:
        return EmailAddress.Validity.TRANSIENT
    return EmailAddress.Validity.UNKNOWN


@transaction.atomic
def ensure_prospect_enrollment(
    prospect: Prospect,
    *,
    campaign: Campaign | None = None,
    provider: str = "",
) -> CampaignEnrollment:
    """Project a legacy Prospect into the contact-centric model during the cutover."""

    from apps.prospects.models import Prospect

    locked = (
        Prospect.objects.select_for_update()
        .select_related("campaign", "campaign__created_by", "source_run", "organization")
        .prefetch_related("identities", "emails")
        .get(pk=prospect.pk)
    )
    target_campaign = campaign or locked.campaign
    workspace = _workspace_for_campaign(target_campaign)
    provider_name = provider or (locked.source_run.provider if locked.source_run_id else "")
    legacy_emails = list(locked.emails.all())
    primary = next((item for item in legacy_emails if item.is_primary), None)
    seed = primary or (legacy_emails[0] if legacy_emails else None)
    resolution = resolve_organization(
        workspace=workspace,
        identities=_prospect_identity_claims(locked, provider_name),
        email=seed.original_email if seed is not None else "",
        name=locked.name,
        address=locked.address,
        website=locked.website,
        business_domain=locked.business_domain,
        phone=locked.phone,
        source=Organization.Source.DISCOVERY,
        provenance={"legacy_prospect_id": str(locked.pk)},
        email_validity=_legacy_email_validity(seed)
        if seed is not None
        else EmailAddress.Validity.UNKNOWN,
        email_provenance=seed.source if seed is not None else "",
        email_source_url=seed.source_url if seed is not None else "",
        email_source_content_hash=seed.source_content_hash if seed is not None else "",
        email_provider_order=seed.provider_order if seed is not None else 0,
        email_validated_at=seed.mx_checked_at if seed is not None else None,
        email_invalid_reason=seed.invalid_reason if seed is not None else "",
        email_invalidated_at=seed.invalidated_at if seed is not None else None,
        legacy_prospect_email=seed,
        prefer_email=primary is not None,
    )
    organization = resolution.organization
    selected_email = resolution.email_address if primary is not None else None
    for legacy_email in legacy_emails:
        if seed is not None and legacy_email.pk == seed.pk:
            continue
        attached = resolve_organization(
            workspace=workspace,
            identities=_prospect_identity_claims(locked, provider_name),
            email=legacy_email.original_email,
            name=locked.name,
            address=locked.address,
            website=locked.website,
            business_domain=locked.business_domain,
            phone=locked.phone,
            source=Organization.Source.DISCOVERY,
            provenance={"legacy_prospect_id": str(locked.pk)},
            email_validity=_legacy_email_validity(legacy_email),
            email_provenance=legacy_email.source,
            email_source_url=legacy_email.source_url,
            email_source_content_hash=legacy_email.source_content_hash,
            email_provider_order=legacy_email.provider_order,
            email_validated_at=legacy_email.mx_checked_at,
            email_invalid_reason=legacy_email.invalid_reason,
            email_invalidated_at=legacy_email.invalidated_at,
            legacy_prospect_email=legacy_email,
            prefer_email=legacy_email.is_primary,
        )
        if attached.organization.pk != organization.pk:
            raise OrganizationResolutionConflict(
                "Los emails del prospecto pertenecen a organizaciones distintas."
            )
        if legacy_email.is_primary:
            selected_email = attached.email_address

    if locked.organization_id not in {None, organization.pk}:
        raise OrganizationResolutionConflict(
            "El prospecto ya estaba vinculado a otra organización. Hace falta una revisión manual."
        )
    if locked.organization_id is None:
        locked.organization = organization
        locked.save(update_fields=("organization", "updated_at"))
    try:
        enrollment, _ = CampaignEnrollment.objects.select_for_update().get_or_create(
            workspace=workspace,
            campaign=target_campaign,
            organization=organization,
            defaults={
                "selected_email": selected_email,
                "state": (
                    CampaignEnrollment.State.ELIGIBLE
                    if selected_email is not None
                    else CampaignEnrollment.State.DISCOVERED
                ),
                "source": "DISCOVERY",
            },
        )
    except IntegrityError as exc:
        raise OrganizationResolutionConflict(
            "La organización ya tiene una participación incompatible en esta campaña."
        ) from exc
    if enrollment.selected_email_id is None and selected_email is not None:
        enrollment.selected_email = selected_email
        enrollment.save(update_fields=("selected_email", "updated_at"))
    if target_campaign.pk == locked.campaign_id and locked.campaign_enrollment_id != enrollment.pk:
        locked.campaign_enrollment = enrollment
        locked.save(update_fields=("campaign_enrollment", "updated_at"))
    refresh_enrollment_eligibility(enrollment)
    enrollment.refresh_from_db()
    return enrollment


def enrollment_eligibility(enrollment: CampaignEnrollment) -> EnrollmentEligibility:
    selected = enrollment.selected_email
    if selected is None:
        return EnrollmentEligibility(False, "NO_EMAIL", "Elegí un email válido antes de continuar.")
    if (
        selected.workspace_id != enrollment.workspace_id
        or selected.organization_id != enrollment.organization_id
    ):
        return EnrollmentEligibility(
            False,
            "EMAIL_OWNERSHIP",
            "El email elegido no pertenece a esta organización.",
        )
    if selected.validity != EmailAddress.Validity.VALID:
        return EnrollmentEligibility(
            False,
            "INVALID_EMAIL",
            "El email elegido no está validado para enviar.",
        )
    if Contact.objects.filter(organization_id=enrollment.organization_id).exists():
        return EnrollmentEligibility(
            False,
            "EXISTING_CONTACT",
            "Esta organización ya es un contacto y no recibirá campañas nuevas.",
        )
    active = CommunicationRestriction.objects.filter(
        workspace_id=enrollment.workspace_id,
        revoked_at__isnull=True,
    ).filter(
        Q(email_address_id=selected.pk) | Q(contact__organization_id=enrollment.organization_id)
    )
    if active.exists():
        return EnrollmentEligibility(
            False,
            "RESTRICTED",
            "Este contacto o email tiene una restricción activa.",
        )
    if SuppressionEntry.objects.filter(normalized_email=selected.normalized_email).exists():
        return EnrollmentEligibility(
            False,
            "LEGACY_RESTRICTION",
            "Este email tiene una restricción registrada.",
        )
    return EnrollmentEligibility(True)


@transaction.atomic
def refresh_enrollment_eligibility(enrollment: CampaignEnrollment) -> EnrollmentEligibility:
    locked = (
        CampaignEnrollment.objects.select_for_update()
        .select_related("organization", "selected_email")
        .get(pk=enrollment.pk)
    )
    result = enrollment_eligibility(locked)
    terminal = {
        CampaignEnrollment.State.INITIAL_SENT,
        CampaignEnrollment.State.RESPONDED,
        CampaignEnrollment.State.REMINDER_SENT,
        CampaignEnrollment.State.CANCELLED,
    }
    if locked.state not in terminal:
        state = (
            CampaignEnrollment.State.ELIGIBLE
            if result.eligible
            else CampaignEnrollment.State.INELIGIBLE
        )
        if locked.state != state or locked.exclusion_reason != result.code:
            locked.state = state
            locked.exclusion_reason = result.code
            locked.save(update_fields=("state", "exclusion_reason", "updated_at"))
    return result


@transaction.atomic
def ensure_outbound_contact_links(message: OutboundMessage) -> CampaignEnrollment:
    from apps.campaigns.models import OutboundMessage

    locked = (
        OutboundMessage.objects.select_for_update()
        .select_related(
            "campaign", "campaign__created_by", "prospect", "prospect_email", "campaign_enrollment"
        )
        .get(pk=message.pk)
    )
    enrollment = locked.campaign_enrollment
    if enrollment is None:
        if locked.prospect is None or locked.campaign is None:
            raise ValidationError(
                "El mensaje no tiene una campaña y un prospecto heredado para resolver."
            )
        enrollment = ensure_prospect_enrollment(locked.prospect, campaign=locked.campaign)
    changes: list[str] = []
    if locked.organization_id != enrollment.organization_id:
        locked.organization_id = enrollment.organization_id
        changes.append("organization")
    if locked.campaign_enrollment_id != enrollment.pk:
        locked.campaign_enrollment = enrollment
        changes.append("campaign_enrollment")
    if changes:
        locked.save(update_fields=(*changes, "updated_at"))
    return enrollment


def outbound_eligibility_error(message: OutboundMessage) -> str:
    enrollment = ensure_outbound_contact_links(message)
    enrollment = CampaignEnrollment.objects.select_related("organization", "selected_email").get(
        pk=enrollment.pk
    )
    result = enrollment_eligibility(enrollment)
    if not result.eligible:
        return result.message
    selected = enrollment.selected_email
    assert selected is not None
    try:
        recipient = normalize_email(message.recipient_normalized or message.recipient)
    except ValidationError:
        return "El destinatario del mensaje no es un email válido."
    if selected.normalized_email != recipient:
        return "El destinatario ya no coincide con el email elegido para la campaña."
    return ""


def _cancel_message_queryset(queryset: Any, *, reason: str) -> int:
    from apps.campaigns.models import OutboundMessage

    return int(
        queryset.filter(
            state__in=(
                OutboundMessage.State.PREPARED,
                OutboundMessage.State.REVIEW_READY,
                OutboundMessage.State.QUEUED,
            )
        ).update(
            state=OutboundMessage.State.CANCELLED,
            next_attempt_at=None,
            error=reason,
            updated_at=timezone.now(),
        )
    )


def cancel_pending_campaign_reminders(
    organization: Organization,
    *,
    reason: str,
) -> int:
    """Stable hook used now and by the later reminder scheduler."""

    from apps.campaigns.models import OutboundMessage

    return _cancel_message_queryset(
        OutboundMessage.objects.filter(
            organization=organization,
            kind="CAMPAIGN_REMINDER",
        ),
        reason=reason,
    )


def cancel_pending_campaign_work(
    organization: Organization,
    *,
    reason: str,
    replied_enrollment_id: uuid.UUID | str | None = None,
    replied_at: Any = None,
) -> int:
    from apps.campaigns.models import OutboundMessage

    cancelled = _cancel_message_queryset(
        OutboundMessage.objects.filter(
            organization=organization,
            kind__in=("FIRST_CONTACT", "INITIAL", "CAMPAIGN_REMINDER"),
        ),
        reason=reason,
    )
    enrollments = CampaignEnrollment.objects.select_for_update().filter(organization=organization)
    if replied_enrollment_id is not None:
        enrollments.filter(pk=replied_enrollment_id).update(
            state=CampaignEnrollment.State.RESPONDED,
            replied_at=replied_at or timezone.now(),
            exclusion_reason="",
            updated_at=timezone.now(),
        )
        enrollments = enrollments.exclude(pk=replied_enrollment_id)
    enrollments.exclude(
        state__in=(
            CampaignEnrollment.State.RESPONDED,
            CampaignEnrollment.State.INITIAL_SENT,
            CampaignEnrollment.State.REMINDER_SENT,
            CampaignEnrollment.State.CANCELLED,
        )
    ).update(
        state=CampaignEnrollment.State.INELIGIBLE,
        exclusion_reason="EXISTING_CONTACT",
        updated_at=timezone.now(),
    )
    return cancelled


def _lock_organization_channels(organization: Organization) -> None:
    normalized_addresses = (
        EmailAddress.objects.filter(organization=organization)
        .order_by("normalized_email")
        .values_list("normalized_email", flat=True)
    )
    for normalized in normalized_addresses:
        lock_email_eligibility(normalized)


def _contact_for_organization(
    *,
    organization: Organization,
    preferred_email: EmailAddress | None,
    reason: str,
    inbound: InboundMessage | None = None,
    actor: User | None = None,
    name: str = "",
    notes: str = "",
) -> tuple[Contact, bool]:
    organization = Organization.objects.select_for_update().get(pk=organization.pk)
    _lock_organization_channels(organization)
    defaults = {
        "workspace": organization.workspace,
        "preferred_email": preferred_email,
        "name": name.strip()[:200],
        "created_reason": reason,
        "source_inbound_message_id": inbound.pk if inbound is not None else None,
        "created_by": actor,
        "notes": notes,
        "last_interaction_at": inbound.external_at if inbound is not None else None,
    }
    contact, created = Contact.objects.select_for_update().get_or_create(
        organization=organization,
        defaults=defaults,
    )
    changes: list[str] = []
    if contact.preferred_email_id is None and preferred_email is not None:
        contact.preferred_email = preferred_email
        changes.append("preferred_email")
    if name.strip() and not contact.name:
        contact.name = name.strip()[:200]
        changes.append("name")
    if notes and not contact.notes:
        contact.notes = notes
        changes.append("notes")
    if inbound is not None and (
        contact.last_interaction_at is None or inbound.external_at > contact.last_interaction_at
    ):
        contact.last_interaction_at = inbound.external_at
        changes.append("last_interaction_at")
    if (
        reason == Contact.CreatedReason.UNSUBSCRIBE
        and contact.status != Contact.Status.UNSUBSCRIBED
    ):
        contact.status = Contact.Status.UNSUBSCRIBED
        changes.append("status")
    if changes:
        contact.save(update_fields=(*dict.fromkeys(changes), "updated_at"))
    return contact, created


def _conversation_for_inbound(
    *,
    inbound: InboundMessage,
    contact: Contact,
) -> Conversation | None:
    if not inbound.gmail_thread_id:
        return None
    existing = (
        Conversation.objects.select_for_update()
        .filter(
            connection=inbound.connection,
            gmail_thread_id=inbound.gmail_thread_id,
        )
        .first()
    )
    if existing is not None and existing.contact_id != contact.pk:
        record_event(
            action="contact.conversation_conflict",
            entity=inbound,
            actor=None,
            after={"contact_id": str(contact.pk)},
        )
        return None
    conversation, _ = Conversation.objects.get_or_create(
        connection=inbound.connection,
        gmail_thread_id=inbound.gmail_thread_id,
        defaults={
            "workspace": contact.workspace,
            "contact": contact,
            "subject": inbound.subject,
            "last_message_at": inbound.external_at,
        },
    )
    if conversation.last_message_at is None or inbound.external_at > conversation.last_message_at:
        conversation.last_message_at = inbound.external_at
        conversation.save(update_fields=("last_message_at", "updated_at"))
    return conversation


def _restriction_for_inbound(
    *,
    inbound: InboundMessage,
    email_address: EmailAddress,
    kind: str,
) -> CommunicationRestriction:
    restriction, _ = CommunicationRestriction.objects.get_or_create(
        workspace=email_address.workspace,
        scope=CommunicationRestriction.Scope.EMAIL,
        kind=kind,
        email_address=email_address,
        revoked_at=None,
        defaults={
            "source": "gmail_reply",
            "evidence": f"InboundMessage:{inbound.pk}",
            "source_inbound_message_id": inbound.pk,
        },
    )
    legacy_reason = (
        SuppressionEntry.Reason.UNSUBSCRIBE
        if kind == CommunicationRestriction.Kind.UNSUBSCRIBE
        else SuppressionEntry.Reason.BOUNCE
    )
    suppress_email(
        email=email_address.original_email,
        reason=legacy_reason,
        actor=None,
        source="gmail_reply"
        if kind == CommunicationRestriction.Kind.UNSUBSCRIBE
        else "gmail_bounce",
        evidence=f"InboundMessage:{inbound.pk}",
    )
    return restriction


def _apply_established_contact_inbound(
    message: InboundMessage,
) -> InboundContactEffect:
    """Attach a reply to a campaign-less relationship message.

    Scheduled Contact messages start new Gmail threads and intentionally have
    no CampaignEnrollment.  Their replies must stay on the existing Contact
    without passing through prospect promotion or campaign eligibility.
    """

    from apps.campaigns.models import OutboundMessage
    from apps.mailbox.models import InboundMessage

    related = message.related_outbound
    if related is None:
        raise ValidationError("El mensaje no tiene un envío vinculado.")
    if related.contact_id is None or related.organization_id is None:
        raise ValidationError(
            "El mensaje sin campaña no tiene un contacto y una organización vinculados."
        )
    contact = (
        Contact.objects.select_for_update()
        .select_related("organization", "preferred_email")
        .get(pk=related.contact_id, organization_id=related.organization_id)
    )
    organization = contact.organization
    selected = related.email_address or contact.preferred_email
    conversation = _conversation_for_inbound(inbound=message, contact=contact)
    InboundMessage.objects.filter(pk=message.pk).update(
        organization_id=organization.pk,
        contact_id=contact.pk,
        conversation_id=conversation.pk if conversation is not None else None,
    )
    if conversation is not None and related.conversation_id != conversation.pk:
        OutboundMessage.objects.filter(pk=related.pk).update(conversation_id=conversation.pk)

    if message.classification == InboundMessage.Classification.AUTO_REPLY:
        return InboundContactEffect(contact, conversation, False, 0)
    if message.classification == InboundMessage.Classification.BOUNCE:
        if selected is not None:
            lock_email_eligibility(selected.normalized_email)
            EmailAddress.objects.filter(pk=selected.pk).update(
                validity=EmailAddress.Validity.INVALID,
                invalid_reason="Rebote informado por Gmail",
                invalidated_at=message.external_at,
                updated_at=timezone.now(),
            )
            _restriction_for_inbound(
                inbound=message,
                email_address=selected,
                kind=CommunicationRestriction.Kind.BOUNCE,
            )
        return InboundContactEffect(contact, conversation, False, 0)

    sender_literal = parseaddr(message.sender)[1]
    sender_address: EmailAddress | None = None
    if sender_literal:
        try:
            resolution = resolve_organization(
                workspace=organization.workspace,
                identities=tuple(
                    IdentityClaim(
                        kind=identity.kind,
                        value=identity.value,
                        value_hash=identity.value_hash,
                        provider=identity.provider,
                        provenance=identity.provenance,
                    )
                    for identity in organization.identities.all()
                ),
                email=sender_literal,
                name=organization.name,
                source=Organization.Source.INBOUND,
                provenance={"source_inbound_message_id": str(message.pk)},
                email_validity=EmailAddress.Validity.VALID,
                email_label="Email desde el que respondió",
                email_provenance="INBOUND_MESSAGE",
                email_validated_at=message.external_at,
                prefer_email=selected is None,
            )
            if resolution.organization.pk == organization.pk:
                sender_address = resolution.email_address
        except (OrganizationResolutionConflict, ValidationError):
            record_event(
                action="contact.sender_email_conflict",
                entity=message,
                actor=None,
                after={"organization_id": str(organization.pk)},
            )
    reason = (
        Contact.CreatedReason.UNSUBSCRIBE
        if message.classification == InboundMessage.Classification.UNSUBSCRIBE
        else Contact.CreatedReason.HUMAN_REPLY
    )
    contact, _ = _contact_for_organization(
        organization=organization,
        preferred_email=sender_address or selected,
        reason=reason,
        inbound=message,
    )
    if message.classification == InboundMessage.Classification.UNSUBSCRIBE and selected is not None:
        _restriction_for_inbound(
            inbound=message,
            email_address=selected,
            kind=CommunicationRestriction.Kind.UNSUBSCRIBE,
        )
    cancel_pending_campaign_work(
        organization,
        reason="La organización interactuó y figura en Contactos.",
        replied_at=message.external_at,
    )
    from apps.automation.scheduled import record_genuine_contact_interaction

    record_genuine_contact_interaction(contact.pk, interacted_at=message.external_at)
    record_event(
        action="contact.interaction_recorded",
        entity=contact,
        actor=None,
        after={
            "organization_id": str(organization.pk),
            "inbound_message_id": str(message.pk),
            "classification": message.classification,
            "source": "SCHEDULED_CONTACT",
        },
    )
    return InboundContactEffect(contact, conversation, False, 0)


def _apply_direct_contact_inbound(message: InboundMessage) -> InboundContactEffect:
    """Attach a new inbound Gmail thread to an existing Contact by sender email."""

    from apps.mailbox.models import InboundMessage

    sender_literal = parseaddr(message.sender)[1]
    if not sender_literal:
        raise ValidationError("El remitente no contiene un email reconocible.")
    normalized = normalize_email(sender_literal)
    selected = (
        EmailAddress.objects.select_for_update()
        .select_related("organization", "organization__contact")
        .get(
            workspace=message.connection.workspace,
            normalized_email=normalized,
            validity=EmailAddress.Validity.VALID,
            invalid_reason="",
            organization__contact__isnull=False,
        )
    )
    organization = selected.organization
    contact = organization.contact
    conversation = _conversation_for_inbound(inbound=message, contact=contact)
    InboundMessage.objects.filter(pk=message.pk).update(
        organization_id=organization.pk,
        contact_id=contact.pk,
        conversation_id=conversation.pk if conversation is not None else None,
    )

    if message.classification == InboundMessage.Classification.AUTO_REPLY:
        return InboundContactEffect(contact, conversation, False, 0)
    if message.classification == InboundMessage.Classification.BOUNCE:
        lock_email_eligibility(selected.normalized_email)
        EmailAddress.objects.filter(pk=selected.pk).update(
            validity=EmailAddress.Validity.INVALID,
            invalid_reason="Rebote informado por Gmail",
            invalidated_at=message.external_at,
            updated_at=timezone.now(),
        )
        _restriction_for_inbound(
            inbound=message,
            email_address=selected,
            kind=CommunicationRestriction.Kind.BOUNCE,
        )
        return InboundContactEffect(contact, conversation, False, 0)

    reason = (
        Contact.CreatedReason.UNSUBSCRIBE
        if message.classification == InboundMessage.Classification.UNSUBSCRIBE
        else Contact.CreatedReason.HUMAN_REPLY
    )
    contact, _ = _contact_for_organization(
        organization=organization,
        preferred_email=selected,
        reason=reason,
        inbound=message,
    )
    if message.classification == InboundMessage.Classification.UNSUBSCRIBE:
        _restriction_for_inbound(
            inbound=message,
            email_address=selected,
            kind=CommunicationRestriction.Kind.UNSUBSCRIBE,
        )
    record_event(
        action="contact.direct_inbound_imported",
        entity=contact,
        actor=None,
        after={
            "organization_id": str(organization.pk),
            "inbound_message_id": str(message.pk),
            "classification": message.classification,
        },
    )
    from apps.automation.scheduled import record_genuine_contact_interaction

    record_genuine_contact_interaction(contact.pk, interacted_at=message.external_at)
    return InboundContactEffect(contact, conversation, False, 0)


@transaction.atomic
def apply_inbound_contact_effect(inbound: InboundMessage) -> InboundContactEffect:
    """Apply only deterministic contact/restriction effects for a persisted inbound."""

    from apps.campaigns.models import OutboundMessage
    from apps.mailbox.models import InboundMessage

    message = (
        InboundMessage.objects.select_for_update(of=("self",))
        .select_related(
            "connection",
            "related_outbound",
            "related_outbound__campaign",
            "related_outbound__prospect",
        )
        .get(pk=inbound.pk)
    )
    if message.related_outbound_id is None:
        return _apply_direct_contact_inbound(message)
    related = message.related_outbound
    if related is None:
        raise ValidationError("El mensaje no tiene un envío vinculado.")
    if related.campaign_id is None:
        return _apply_established_contact_inbound(message)
    enrollment = ensure_outbound_contact_links(related)
    enrollment = CampaignEnrollment.objects.select_related("organization", "selected_email").get(
        pk=enrollment.pk
    )
    organization = enrollment.organization
    basic_updates = {
        "organization_id": organization.pk,
        "campaign_enrollment_id": enrollment.pk,
    }
    InboundMessage.objects.filter(pk=message.pk).update(**basic_updates)
    if message.classification == InboundMessage.Classification.AUTO_REPLY:
        return InboundContactEffect(None, None, False, 0)

    selected = enrollment.selected_email
    if selected is None:
        selected = EmailAddress.objects.filter(
            organization=organization,
            normalized_email=related.recipient_normalized,
        ).first()
    if message.classification == InboundMessage.Classification.BOUNCE:
        if selected is not None:
            lock_email_eligibility(selected.normalized_email)
            EmailAddress.objects.filter(pk=selected.pk).update(
                validity=EmailAddress.Validity.INVALID,
                invalid_reason="Rebote informado por Gmail",
                invalidated_at=message.external_at,
                updated_at=timezone.now(),
            )
            _restriction_for_inbound(
                inbound=message,
                email_address=selected,
                kind=CommunicationRestriction.Kind.BOUNCE,
            )
        cancelled = cancel_pending_campaign_reminders(
            organization,
            reason="El email rebotó; se canceló el recordatorio pendiente.",
        )
        return InboundContactEffect(None, None, False, cancelled)

    sender_literal = parseaddr(message.sender)[1]
    sender_address: EmailAddress | None = None
    if sender_literal:
        try:
            sender_resolution = resolve_organization(
                workspace=organization.workspace,
                identities=tuple(
                    IdentityClaim(
                        kind=identity.kind,
                        value=identity.value,
                        value_hash=identity.value_hash,
                        provider=identity.provider,
                        provenance=identity.provenance,
                    )
                    for identity in organization.identities.all()
                ),
                email=sender_literal,
                name=organization.name,
                source=Organization.Source.INBOUND,
                provenance={"source_inbound_message_id": str(message.pk)},
                email_validity=EmailAddress.Validity.VALID,
                email_label="Email desde el que respondió",
                email_provenance="INBOUND_MESSAGE",
                email_validated_at=message.external_at,
                prefer_email=selected is None,
            )
            if sender_resolution.organization.pk == organization.pk:
                sender_address = sender_resolution.email_address
        except (OrganizationResolutionConflict, ValidationError):
            record_event(
                action="contact.sender_email_conflict",
                entity=message,
                actor=None,
                after={"organization_id": str(organization.pk)},
            )
    preferred = (
        sender_address or selected or organization.email_addresses.filter(is_preferred=True).first()
    )
    reason = (
        Contact.CreatedReason.UNSUBSCRIBE
        if message.classification == InboundMessage.Classification.UNSUBSCRIBE
        else Contact.CreatedReason.HUMAN_REPLY
    )
    contact, created = _contact_for_organization(
        organization=organization,
        preferred_email=preferred,
        reason=reason,
        inbound=message,
    )
    if message.classification == InboundMessage.Classification.UNSUBSCRIBE and selected is not None:
        _restriction_for_inbound(
            inbound=message,
            email_address=selected,
            kind=CommunicationRestriction.Kind.UNSUBSCRIBE,
        )
    conversation = _conversation_for_inbound(inbound=message, contact=contact)
    InboundMessage.objects.filter(pk=message.pk).update(
        contact_id=contact.pk,
        conversation_id=conversation.pk if conversation is not None else None,
    )
    outbound_updates = {"contact_id": contact.pk}
    if conversation is not None:
        outbound_updates["conversation_id"] = conversation.pk
    OutboundMessage.objects.filter(
        organization=organization,
        gmail_thread_id=message.gmail_thread_id,
    ).update(**outbound_updates)
    cancelled = cancel_pending_campaign_work(
        organization,
        reason="La organización ya respondió y ahora figura en Contactos.",
        replied_enrollment_id=enrollment.pk,
        replied_at=message.external_at,
    )
    record_event(
        action="contact.promoted" if created else "contact.interaction_recorded",
        entity=contact,
        actor=None,
        after={
            "organization_id": str(organization.pk),
            "inbound_message_id": str(message.pk),
            "classification": message.classification,
            "cancelled_messages": cancelled,
        },
    )
    from apps.automation.scheduled import record_genuine_contact_interaction

    record_genuine_contact_interaction(contact.pk, interacted_at=message.external_at)
    return InboundContactEffect(contact, conversation, created, cancelled)


@transaction.atomic
def create_manual_contact(
    *,
    actor: User,
    email: str,
    organization_name: str = "",
    contact_name: str = "",
    notes: str = "",
) -> Contact:
    membership = require_user_capability(actor, Capability.MANAGE_CONTACTS)
    resolution = resolve_organization(
        workspace=membership.workspace,
        email=email,
        name=organization_name,
        source=Organization.Source.MANUAL,
        provenance={"created_by_id": actor.pk},
        email_validity=EmailAddress.Validity.UNKNOWN,
        email_label="Email principal",
        email_provenance="MANUAL",
        prefer_email=True,
    )
    assert resolution.email_address is not None
    contact, created = _contact_for_organization(
        organization=resolution.organization,
        preferred_email=resolution.email_address,
        reason=Contact.CreatedReason.MANUAL_ENTRY,
        actor=actor,
        name=contact_name,
        notes=notes,
    )
    cancelled = cancel_pending_campaign_work(
        resolution.organization,
        reason="La organización se agregó manualmente a Contactos.",
    )
    record_event(
        action="contact.created_manually" if created else "contact.manual_entry_reused",
        entity=contact,
        actor=actor,
        after={
            "organization_id": str(resolution.organization.pk),
            "cancelled_messages": cancelled,
        },
    )
    if resolution.email_address.validity in {
        EmailAddress.Validity.UNKNOWN,
        EmailAddress.Validity.TRANSIENT,
    }:
        transaction.on_commit(
            partial(_dispatch_contact_email_validation, resolution.email_address.pk)
        )
    return contact


@transaction.atomic
def add_contact_email_address(
    *,
    actor: User,
    contact_id: uuid.UUID | str,
    email: str,
    label: str = "",
    make_preferred: bool = False,
) -> EmailAddress:
    """Add a manually supplied channel without ever moving an email between organizations."""

    membership = require_user_capability(actor, Capability.MANAGE_CONTACTS)
    contact = (
        Contact.objects.select_for_update()
        .select_related("organization", "preferred_email")
        .get(pk=contact_id)
    )
    if contact.workspace_id != membership.workspace_id:
        raise PermissionDenied
    organization = Organization.objects.select_for_update().get(pk=contact.organization_id)
    claims = tuple(
        IdentityClaim(
            kind=identity.kind,
            value=identity.value,
            value_hash=identity.value_hash,
            provider=identity.provider,
            provenance=identity.provenance,
        )
        for identity in organization.identities.all()
    )
    resolution = resolve_organization(
        workspace=membership.workspace,
        identities=claims,
        email=email,
        name=organization.name,
        source=Organization.Source.MANUAL,
        provenance={"added_to_contact_id": str(contact.pk), "created_by_id": actor.pk},
        email_validity=EmailAddress.Validity.UNKNOWN,
        email_label=label or "Email agregado manualmente",
        email_provenance="MANUAL",
        prefer_email=False,
    )
    if resolution.organization.pk != organization.pk or resolution.email_address is None:
        raise OrganizationResolutionConflict(
            "Ese email pertenece a otra organización. Revisalo antes de continuar."
        )
    email_address = EmailAddress.objects.select_for_update().get(pk=resolution.email_address.pk)
    changes: list[str] = []
    clean_label = label.strip()[:120]
    if clean_label and email_address.label != clean_label:
        email_address.label = clean_label
        changes.append("label")
    choose_as_preferred = make_preferred or contact.preferred_email_id is None
    if choose_as_preferred:
        if email_address.validity == EmailAddress.Validity.INVALID:
            raise ValidationError("Un email inválido no puede ser el email preferido.")
        EmailAddress.objects.filter(
            organization=organization,
            is_preferred=True,
        ).exclude(pk=email_address.pk).update(is_preferred=False, updated_at=timezone.now())
        if not email_address.is_preferred:
            email_address.is_preferred = True
            changes.append("is_preferred")
        if contact.preferred_email_id != email_address.pk:
            contact.preferred_email = email_address
            contact.save(update_fields=("preferred_email", "updated_at"))
    if changes:
        email_address.save(update_fields=(*dict.fromkeys(changes), "updated_at"))
    record_event(
        action="contact.email_added",
        entity=email_address,
        actor=actor,
        after={
            "contact_id": str(contact.pk),
            "preferred": email_address.is_preferred,
        },
    )
    if email_address.validity in {
        EmailAddress.Validity.UNKNOWN,
        EmailAddress.Validity.TRANSIENT,
    }:
        transaction.on_commit(partial(_dispatch_contact_email_validation, email_address.pk))
    return email_address


def _dispatch_contact_email_validation(email_address_id: uuid.UUID) -> None:
    from apps.contacts.tasks import validate_contact_email_task

    validate_contact_email_task.delay(str(email_address_id))


def queue_contact_email_validation(
    *,
    actor: User,
    email_address_id: uuid.UUID | str,
) -> None:
    membership = require_user_capability(actor, Capability.MANAGE_CONTACTS)
    email_address = EmailAddress.objects.get(
        pk=email_address_id,
        workspace=membership.workspace,
    )
    if email_address.validity not in {
        EmailAddress.Validity.UNKNOWN,
        EmailAddress.Validity.TRANSIENT,
    }:
        raise ValidationError("Este email ya tiene un resultado de validación.")
    transaction.on_commit(partial(_dispatch_contact_email_validation, email_address.pk))


def validate_contact_email_address(
    email_address_id: uuid.UUID | str,
    *,
    resolver: MXResolver,
) -> str:
    """Validate one manual channel without holding a database lock during DNS."""

    email_address = EmailAddress.objects.get(pk=email_address_id)
    if email_address.validity == EmailAddress.Validity.VALID:
        return email_address.validity
    if email_address.validity == EmailAddress.Validity.INVALID:
        return email_address.validity
    mx_status = resolver.resolve(email_address.domain)
    with transaction.atomic():
        locked = EmailAddress.objects.select_for_update().get(pk=email_address.pk)
        if locked.validity in {
            EmailAddress.Validity.VALID,
            EmailAddress.Validity.INVALID,
        }:
            return locked.validity
        now = timezone.now()
        if mx_status == MXStatus.VALID:
            locked.validity = EmailAddress.Validity.VALID
            locked.validated_at = now
            locked.invalid_reason = ""
            locked.invalidated_at = None
        elif mx_status == MXStatus.INVALID:
            locked.validity = EmailAddress.Validity.INVALID
            locked.validated_at = now
            locked.invalid_reason = "El dominio no recibe correo."
            locked.invalidated_at = now
        else:
            locked.validity = EmailAddress.Validity.TRANSIENT
            locked.validated_at = None
            locked.invalid_reason = ""
            locked.invalidated_at = None
        locked.save(
            update_fields=(
                "validity",
                "validated_at",
                "invalid_reason",
                "invalidated_at",
                "updated_at",
            )
        )
        record_event(
            action="contact.email_validation_completed",
            entity=locked,
            actor=None,
            after={"validity": locked.validity},
        )
        return locked.validity


@transaction.atomic
def set_contact_preferred_email(
    *,
    actor: User,
    contact_id: uuid.UUID | str,
    email_address_id: uuid.UUID | str,
) -> Contact:
    membership = require_user_capability(actor, Capability.MANAGE_CONTACTS)
    contact = Contact.objects.select_for_update().get(pk=contact_id)
    if contact.workspace_id != membership.workspace_id:
        raise PermissionDenied
    email_address = EmailAddress.objects.select_for_update().get(pk=email_address_id)
    if (
        email_address.workspace_id != membership.workspace_id
        or email_address.organization_id != contact.organization_id
    ):
        raise PermissionDenied
    if email_address.validity == EmailAddress.Validity.INVALID:
        raise ValidationError("Un email inválido no puede ser el email preferido.")
    EmailAddress.objects.filter(
        organization_id=contact.organization_id,
        is_preferred=True,
    ).exclude(pk=email_address.pk).update(is_preferred=False, updated_at=timezone.now())
    if not email_address.is_preferred:
        email_address.is_preferred = True
        email_address.save(update_fields=("is_preferred", "updated_at"))
    if contact.preferred_email_id != email_address.pk:
        contact.preferred_email = email_address
        contact.save(update_fields=("preferred_email", "updated_at"))
    record_event(
        action="contact.preferred_email_changed",
        entity=contact,
        actor=actor,
        after={"email_address_id": str(email_address.pk)},
    )
    return contact


@transaction.atomic
def update_contact_notes(
    *,
    actor: User,
    contact_id: uuid.UUID | str,
    notes: str,
) -> Contact:
    membership = require_user_capability(actor, Capability.MANAGE_CONTACTS)
    contact = Contact.objects.select_for_update().get(pk=contact_id)
    if contact.workspace_id != membership.workspace_id:
        raise PermissionDenied
    before_present = bool(contact.notes.strip())
    contact.notes = notes.strip()
    contact.save(update_fields=("notes", "updated_at"))
    record_event(
        action="contact.notes_updated",
        entity=contact,
        actor=actor,
        before={"notes_present": before_present},
        after={"notes_present": bool(contact.notes)},
    )
    return contact


@transaction.atomic
def create_manual_restriction(
    *,
    actor: User,
    scope: str,
    reason: str,
    contact_id: uuid.UUID | str | None = None,
    email_address_id: uuid.UUID | str | None = None,
) -> CommunicationRestriction:
    if not reason.strip():
        raise ValidationError("Explicá por qué no se debe contactar.")
    membership = require_user_capability(actor, Capability.MANAGE_CONTACTS)
    contact: Contact | None = None
    email_address: EmailAddress | None = None
    if scope == CommunicationRestriction.Scope.CONTACT:
        if contact_id is None or email_address_id is not None:
            raise ValidationError("Elegí un contacto completo para esta restricción.")
        contact = (
            Contact.objects.select_for_update().select_related("organization").get(pk=contact_id)
        )
        if contact.workspace_id != membership.workspace_id:
            raise PermissionDenied
        _lock_organization_channels(contact.organization)
    elif scope == CommunicationRestriction.Scope.EMAIL:
        if email_address_id is None or contact_id is not None:
            raise ValidationError("Elegí un único email para esta restricción.")
        email_address = (
            EmailAddress.objects.select_for_update()
            .select_related("organization")
            .get(pk=email_address_id)
        )
        if email_address.workspace_id != membership.workspace_id:
            raise PermissionDenied
        lock_email_eligibility(email_address.normalized_email)
        contact, _ = _contact_for_organization(
            organization=email_address.organization,
            preferred_email=email_address,
            reason=Contact.CreatedReason.MANUAL_RESTRICTION,
            actor=actor,
        )
    else:
        raise ValidationError("El alcance de la restricción no es válido.")

    target: dict[str, Any] = (
        {"contact": contact}
        if contact is not None and scope == CommunicationRestriction.Scope.CONTACT
        else {"email_address": email_address}
    )
    restriction = (
        CommunicationRestriction.objects.select_for_update()
        .filter(
            kind=CommunicationRestriction.Kind.MANUAL,
            revoked_at__isnull=True,
            **target,
        )
        .first()
    )
    if restriction is None:
        restriction = CommunicationRestriction.objects.create(
            workspace=membership.workspace,
            scope=scope,
            kind=CommunicationRestriction.Kind.MANUAL,
            source="contacts_dashboard",
            evidence=reason.strip(),
            created_by=actor,
            **target,
        )
    if scope == CommunicationRestriction.Scope.CONTACT:
        assert contact is not None
        if contact.status == Contact.Status.ACTIVE:
            contact.status = Contact.Status.DO_NOT_CONTACT
            contact.save(update_fields=("status", "updated_at"))
        bridge_addresses = contact.organization.email_addresses.all()
        organization = contact.organization
    else:
        assert email_address is not None and contact is not None
        bridge_addresses = EmailAddress.objects.filter(pk=email_address.pk)
        organization = email_address.organization
    for address in bridge_addresses.order_by("normalized_email"):
        suppress_email(
            email=address.original_email,
            reason=SuppressionEntry.Reason.MANUAL,
            actor=actor,
            source="contacts_dashboard",
            evidence=f"CommunicationRestriction:{restriction.pk}",
        )
    cancelled = cancel_pending_campaign_work(
        organization,
        reason="La organización tiene una restricción manual en Contactos.",
    )
    record_event(
        action="contact.restriction_created",
        entity=restriction,
        actor=actor,
        after={"scope": scope, "cancelled_messages": cancelled, "reason_recorded": True},
    )
    return restriction


@transaction.atomic
def revoke_manual_restriction(
    *,
    actor: User,
    restriction_id: uuid.UUID | str,
    reason: str,
) -> CommunicationRestriction:
    if not reason.strip():
        raise ValidationError("Explicá por qué se quita la restricción.")
    membership = require_user_capability(actor, Capability.MANAGE_CONTACTS)
    restriction = (
        CommunicationRestriction.objects.select_for_update()
        .select_related("contact", "email_address")
        .get(pk=restriction_id)
    )
    if restriction.workspace_id != membership.workspace_id:
        raise PermissionDenied
    if restriction.kind != CommunicationRestriction.Kind.MANUAL:
        raise ValidationError("Sólo las restricciones manuales se pueden quitar.")
    if restriction.revoked_at is not None:
        return restriction
    restriction.revoked_at = timezone.now()
    restriction.revoked_by = actor
    restriction.revocation_reason = reason.strip()
    restriction.save(update_fields=("revoked_at", "revoked_by", "revocation_reason", "updated_at"))

    # During the expand/backfill/switch rollout, new Contact restrictions are
    # mirrored into the legacy suppression table so historical delivery paths
    # remain safe.  A bridge created solely for this reversible restriction
    # must be removed when an administrator revokes it; unrelated historical
    # suppressions and irreversible unsubscribe rows are deliberately left
    # untouched.
    bridge_evidence = f"CommunicationRestriction:{restriction.pk}"
    bridge_entries = list(
        SuppressionEntry.objects.select_for_update().filter(
            reason=SuppressionEntry.Reason.MANUAL,
            source="contacts_dashboard",
            evidence=bridge_evidence,
        )
    )
    for entry in bridge_entries:
        record_event(
            action="suppression.bridge_removed",
            entity=entry,
            actor=actor,
            before={"normalized_email": entry.normalized_email, "reason": entry.reason},
            after={"restriction_id": str(restriction.pk)},
        )
        entry.delete()
    if restriction.contact_id is not None:
        contact = Contact.objects.select_for_update().get(pk=restriction.contact_id)
        other_contact_restrictions = CommunicationRestriction.objects.filter(
            contact=contact,
            revoked_at__isnull=True,
        ).exclude(pk=restriction.pk)
        if (
            contact.status == Contact.Status.DO_NOT_CONTACT
            and not other_contact_restrictions.exists()
        ):
            contact.status = Contact.Status.ACTIVE
            contact.save(update_fields=("status", "updated_at"))
    record_event(
        action="contact.restriction_revoked",
        entity=restriction,
        actor=actor,
        after={"reason_recorded": True},
    )
    return restriction


@transaction.atomic
def set_contact_no_contact(
    *,
    actor: User,
    contact_id: uuid.UUID | str,
    blocked: bool,
) -> Contact:
    membership = require_user_capability(actor, Capability.MANAGE_CONTACTS)
    contact = Contact.objects.select_for_update().get(pk=contact_id)
    if contact.workspace_id != membership.workspace_id:
        raise PermissionDenied
    active_contact_restrictions = list(
        CommunicationRestriction.objects.select_for_update().filter(
            workspace=membership.workspace,
            contact=contact,
            scope=CommunicationRestriction.Scope.CONTACT,
            revoked_at__isnull=True,
        )
    )
    if blocked:
        has_manual_restriction = any(
            item.kind == CommunicationRestriction.Kind.MANUAL
            for item in active_contact_restrictions
        )
        if has_manual_restriction:
            return contact
        create_manual_restriction(
            actor=actor,
            scope=CommunicationRestriction.Scope.CONTACT,
            contact_id=contact.pk,
            reason="Marcado desde la lista de Contactos.",
        )
        contact.refresh_from_db()
        return contact
    permanent = [
        item
        for item in active_contact_restrictions
        if item.kind != CommunicationRestriction.Kind.MANUAL
    ]
    if permanent:
        raise ValidationError("Esta restricción no se puede quitar desde Contactos.")
    for restriction in active_contact_restrictions:
        revoke_manual_restriction(
            actor=actor,
            restriction_id=restriction.pk,
            reason="Rehabilitado desde la lista de Contactos.",
        )
    contact.refresh_from_db()
    return contact


def communication_is_restricted(
    *,
    contact: Contact,
    email_address: EmailAddress | None = None,
) -> bool:
    query = Q(contact=contact)
    if email_address is not None:
        if email_address.organization_id != contact.organization_id:
            return True
        query |= Q(email_address=email_address)
    return (
        CommunicationRestriction.objects.filter(
            workspace=contact.workspace,
            revoked_at__isnull=True,
        )
        .filter(query)
        .exists()
    )
