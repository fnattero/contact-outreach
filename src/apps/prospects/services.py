from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

from apps.audit.services import record_event
from apps.campaigns.models import Campaign, SearchRun
from apps.compliance.services import is_email_suppressed
from apps.configuration.models import normalize_name
from apps.contacts.models import CampaignEnrollment
from apps.contacts.services import ensure_prospect_enrollment, refresh_enrollment_eligibility
from apps.integrations.contracts import ExtractedBusiness, ExtractedEmail
from apps.integrations.domains import registrable_domain_from_hostname
from apps.prospects.email_validation import (
    DNSMXResolver,
    MockMXResolver,
    MXResolver,
    TransientMXError,
    ValidatedEmail,
    select_validated_email,
    validate_and_select_email,
)
from apps.prospects.exceptions import ProspectPipelineInactive
from apps.prospects.models import Prospect, ProspectEmail, ProspectIdentity, WebsiteSnapshot

FREE_OR_SHARED_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "yahoo.com",
        "yahoo.com.ar",
        "facebook.com",
        "instagram.com",
        "wixsite.com",
    }
)
MAX_WEBSITE_EMAIL_CANDIDATES = 40
WEBSITE_EMAIL_SOURCES = frozenset({"mailto", "visible_text"})
CONTACT_CENTRIC_EXCLUSION_CODES = frozenset(
    {"EXISTING_CONTACT", "RESTRICTED", "LEGACY_RESTRICTION", "EMAIL_OWNERSHIP"}
)


class IngestOutcome(StrEnum):
    CREATED = "CREATED"
    DUPLICATE = "DUPLICATE"
    NO_EMAIL = "NO_EMAIL"


@dataclass(frozen=True, slots=True)
class IngestResult:
    outcome: IngestOutcome
    prospect: Prospect | None = None


class DeduplicationConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedBusiness:
    business: ExtractedBusiness
    business_domain: str
    selected_email: ValidatedEmail | None
    valid_emails: tuple[ValidatedEmail, ...]


@transaction.atomic
def mark_fixed_campaign_prospect_ready(prospect_id: uuid.UUID | str) -> Prospect:
    """Finish a new-campaign prospect without relevance scoring or LLM copy."""

    prospect = (
        Prospect.objects.select_for_update()
        .select_related("campaign", "campaign_enrollment")
        .get(pk=prospect_id)
    )
    if prospect.campaign.state != Campaign.State.DISCOVERING:
        return prospect
    enrollment = ensure_prospect_enrollment(prospect, campaign=prospect.campaign)
    eligibility = refresh_enrollment_eligibility(enrollment)
    before = {"pipeline_state": prospect.pipeline_state}
    if eligibility.eligible:
        prospect.pipeline_state = Prospect.PipelineState.QUEUED
        prospect.error_stage = ""
        prospect.last_error = ""
    else:
        prospect.pipeline_state = Prospect.PipelineState.SKIPPED_DUPLICATE
        prospect.error_stage = "ELIGIBILITY"
        prospect.last_error = eligibility.message
    prospect.save(update_fields=("pipeline_state", "error_stage", "last_error", "updated_at"))
    record_event(
        action="prospect.fixed_campaign_ready",
        entity=prospect,
        actor=None,
        before=before,
        after={
            "pipeline_state": prospect.pipeline_state,
            "enrollment_id": str(enrollment.pk),
            "llm_calls": 0,
        },
    )
    return prospect


def registrable_domain(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlsplit(url if "://" in url else f"https://{url}")
    try:
        hostname = (parsed.hostname or "").encode("idna").decode("ascii").casefold()
    except UnicodeError:
        return ""
    return registrable_domain_from_hostname(hostname)


def _hash(kind: str, value: str) -> tuple[str, str]:
    return kind, hashlib.sha256(value.encode("utf-8")).hexdigest()


def _identity_keys(
    business: ExtractedBusiness,
    *,
    provider: str,
    normalized_emails: tuple[str, ...],
    business_domain: str,
) -> list[tuple[str, str]]:
    keys = [
        _hash(ProspectIdentity.Kind.EMAIL, normalized_email)
        for normalized_email in normalized_emails
    ]
    keys.append(_hash(ProspectIdentity.Kind.PROVIDER_ID, f"{provider}:{business.provider_id}"))
    if business_domain and business_domain not in FREE_OR_SHARED_DOMAINS:
        keys.append(_hash(ProspectIdentity.Kind.BUSINESS_DOMAIN, business_domain))
    normalized_name = normalize_name(business.name)
    normalized_address = normalize_name(business.address)
    if normalized_name and normalized_address:
        keys.append(
            _hash(
                ProspectIdentity.Kind.NAME_ADDRESS,
                f"{normalized_name}\x1f{normalized_address}",
            )
        )
    return keys


def _lock_keys(keys: list[tuple[str, str]]) -> None:
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        for _, value_hash in sorted(keys):
            lock_id = int.from_bytes(bytes.fromhex(value_hash[:16]), byteorder="big", signed=True)
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_id])


def prepare_business(
    *, business: ExtractedBusiness, resolver: MXResolver
) -> PreparedBusiness | None:
    business_domain = registrable_domain(business.website)
    try:
        selected, valid_emails = validate_and_select_email(
            business.email_candidates,
            business_domain=business_domain,
            resolver=resolver,
        )
    except (ValidationError, TransientMXError):
        selected = None
        valid_emails = ()
    return PreparedBusiness(
        business=business,
        business_domain=business_domain,
        selected_email=selected,
        valid_emails=valid_emails,
    )


@transaction.atomic
def ingest_prepared_business(*, run: SearchRun, prepared: PreparedBusiness) -> IngestResult:
    business = prepared.business
    business_domain = prepared.business_domain
    selected = prepared.selected_email
    valid_emails = prepared.valid_emails

    eligibility_keys = _identity_keys(
        business,
        provider=run.provider,
        normalized_emails=tuple(email.normalized for email in valid_emails),
        business_domain=business_domain,
    )
    _lock_keys(eligibility_keys)
    # Email validation occurs before the write transaction so MX lookups never
    # hold database locks. Re-check suppression after taking the same advisory
    # eligibility locks used by suppression writes to close that race.
    valid_emails = tuple(
        email for email in valid_emails if not is_email_suppressed(email.normalized)
    )
    selected = (
        select_validated_email(valid_emails, business_domain=business_domain)
        if valid_emails
        else None
    )
    keys = _identity_keys(
        business,
        provider=run.provider,
        normalized_emails=tuple(email.normalized for email in valid_emails),
        business_domain=business_domain,
    )
    identity_matches = list(
        ProspectIdentity.objects.select_related("prospect").filter(
            kind__in=[kind for kind, _ in keys], value_hash__in=[value for _, value in keys]
        )
    )
    existing_email = (
        ProspectEmail.objects.select_related("prospect")
        .filter(normalized_email__in=[email.normalized for email in valid_emails])
        .first()
    )
    identity_prospect_ids = {identity.prospect_id for identity in identity_matches}
    if len(identity_prospect_ids) > 1:
        raise DeduplicationConflict("Las identidades del negocio apuntan a prospectos distintos.")
    existing = identity_matches[0].prospect if identity_matches else None
    if (
        existing is not None
        and existing_email is not None
        and existing.pk != existing_email.prospect_id
    ):
        raise DeduplicationConflict("El email y las identidades apuntan a prospectos distintos.")
    if existing is None and existing_email is not None:
        existing = existing_email.prospect
    if existing is not None:
        existing = Prospect.objects.select_for_update().get(pk=existing.pk)
        existing_keys = set(existing.identities.values_list("kind", "value_hash"))
        ProspectIdentity.objects.bulk_create(
            [
                ProspectIdentity(prospect=existing, kind=kind, value_hash=value_hash)
                for kind, value_hash in keys
                if (kind, value_hash) not in existing_keys
            ],
            ignore_conflicts=True,
        )
        existing_email_values = set(existing.emails.values_list("normalized_email", flat=True))
        has_primary_email = existing.emails.filter(is_primary=True).exists()
        selected_normalized = selected.normalized if selected is not None else ""
        promote_direct_email = (
            bool(selected_normalized)
            and not has_primary_email
            and existing.pipeline_state == Prospect.PipelineState.DISCOVERED
        )
        checked_at = timezone.now()
        ProspectEmail.objects.bulk_create(
            [
                ProspectEmail(
                    prospect=existing,
                    original_email=email.original,
                    normalized_email=email.normalized,
                    domain=email.domain,
                    local_part=email.local_part,
                    source=email.source,
                    source_url=email.source_url,
                    source_content_hash=email.source_content_hash,
                    provider_order=email.provider_order,
                    mx_status=ProspectEmail.MXStatus.VALID,
                    mx_checked_at=checked_at,
                    is_primary=(promote_direct_email and email.normalized == selected_normalized),
                )
                for email in valid_emails
                if email.normalized not in existing_email_values
            ],
            ignore_conflicts=True,
        )
        if promote_direct_email:
            ProspectEmail.objects.filter(
                prospect=existing,
                normalized_email=selected_normalized,
            ).update(is_primary=True)
            before = {"pipeline_state": existing.pipeline_state}
            existing.pipeline_state = Prospect.PipelineState.EMAIL_FOUND
            existing.error_stage = ""
            existing.last_error = ""
            existing.save(
                update_fields=("pipeline_state", "error_stage", "last_error", "updated_at")
            )
            record_event(
                action="prospect.email_found",
                entity=existing,
                actor=None,
                before=before,
                after={"pipeline_state": existing.pipeline_state, "source": "provider"},
            )
        enrollment = ensure_prospect_enrollment(
            existing,
            campaign=run.campaign,
            provider=run.provider,
        )
        if (
            enrollment.state == CampaignEnrollment.State.INELIGIBLE
            and enrollment.exclusion_reason in CONTACT_CENTRIC_EXCLUSION_CODES
            and existing.campaign_id == run.campaign_id
            and existing.pipeline_state
            not in {
                Prospect.PipelineState.SKIPPED_DUPLICATE,
                Prospect.PipelineState.SKIPPED_NO_EMAIL,
            }
        ):
            existing.pipeline_state = Prospect.PipelineState.SKIPPED_DUPLICATE
            existing.error_stage = "ELIGIBILITY"
            existing.last_error = enrollment.exclusion_reason
            existing.save(
                update_fields=("pipeline_state", "error_stage", "last_error", "updated_at")
            )
        return IngestResult(IngestOutcome.DUPLICATE, existing)

    prospect = Prospect.objects.create(
        campaign=run.campaign,
        source_run=run,
        name=business.name,
        normalized_name=normalize_name(business.name),
        address=business.address,
        normalized_address=normalize_name(business.address),
        neighborhood=run.query.zone_snapshot,
        category=business.category or run.query.category_snapshot,
        website=business.website or "",
        business_domain=business_domain if business_domain not in FREE_OR_SHARED_DOMAINS else "",
        phone=business.phone or "",
        latitude=business.latitude,
        longitude=business.longitude,
        provider_data=business.provider_data or {},
        pipeline_state=(
            Prospect.PipelineState.EMAIL_FOUND
            if selected is not None
            else Prospect.PipelineState.DISCOVERED
        ),
    )
    ProspectIdentity.objects.bulk_create(
        ProspectIdentity(prospect=prospect, kind=kind, value_hash=value_hash)
        for kind, value_hash in keys
    )
    checked_at = timezone.now()
    ProspectEmail.objects.bulk_create(
        ProspectEmail(
            prospect=prospect,
            original_email=email.original,
            normalized_email=email.normalized,
            domain=email.domain,
            local_part=email.local_part,
            source=email.source,
            source_url=email.source_url,
            source_content_hash=email.source_content_hash,
            provider_order=email.provider_order,
            mx_status=ProspectEmail.MXStatus.VALID,
            mx_checked_at=checked_at,
            is_primary=selected is not None and email.normalized == selected.normalized,
        )
        for email in valid_emails
    )
    enrollment = ensure_prospect_enrollment(
        prospect,
        campaign=run.campaign,
        provider=run.provider,
    )
    if (
        enrollment.state == CampaignEnrollment.State.INELIGIBLE
        and enrollment.exclusion_reason in CONTACT_CENTRIC_EXCLUSION_CODES
    ):
        prospect.pipeline_state = Prospect.PipelineState.SKIPPED_DUPLICATE
        prospect.error_stage = "ELIGIBILITY"
        prospect.last_error = enrollment.exclusion_reason
        prospect.save(update_fields=("pipeline_state", "error_stage", "last_error", "updated_at"))
        return IngestResult(IngestOutcome.DUPLICATE, prospect)
    return IngestResult(
        IngestOutcome.CREATED if selected is not None else IngestOutcome.NO_EMAIL,
        prospect,
    )


def ingest_business(
    *, run: SearchRun, business: ExtractedBusiness, resolver: MXResolver
) -> IngestResult:
    prepared = prepare_business(business=business, resolver=resolver)
    if prepared is None:
        return IngestResult(IngestOutcome.NO_EMAIL)
    return ingest_prepared_business(run=run, prepared=prepared)


def _website_candidates(snapshot: WebsiteSnapshot) -> tuple[ExtractedEmail, ...]:
    pages = snapshot.pages if isinstance(snapshot.pages, list) else []
    allowed_sources = {
        (str(page.get("final_url", "")), str(page.get("content_hash", "")))
        for page in pages
        if isinstance(page, Mapping)
    }
    rows = snapshot.email_candidates if isinstance(snapshot.email_candidates, list) else []
    candidates: list[ExtractedEmail] = []
    for row in rows[:MAX_WEBSITE_EMAIL_CANDIDATES]:
        if not isinstance(row, Mapping):
            continue
        value = str(row.get("value", "")).strip()
        source = str(row.get("source", ""))
        source_url = str(row.get("page_url", ""))
        source_hash = str(row.get("page_content_hash", ""))
        if (
            not value
            or len(value) > 320
            or source not in WEBSITE_EMAIL_SOURCES
            or (source_url, source_hash) not in allowed_sources
            or len(source_url) > 1000
            or len(source_hash) != 64
        ):
            continue
        candidates.append(
            ExtractedEmail(
                value=value,
                source=f"website_{source}",
                order=len(candidates),
                source_url=source_url,
                source_content_hash=source_hash,
            )
        )
    return tuple(candidates)


def _set_contact_terminal_locked(
    *,
    prospect: Prospect,
    state: str,
    snapshot: WebsiteSnapshot,
    candidate_count: int,
    reason: str = "",
) -> Prospect:
    before = {"pipeline_state": prospect.pipeline_state}
    prospect.pipeline_state = state
    prospect.error_stage = ""
    prospect.last_error = ""
    prospect.save(update_fields=("pipeline_state", "error_stage", "last_error", "updated_at"))
    after = {
        "pipeline_state": state,
        "source": "website",
        "snapshot_id": str(snapshot.pk),
        "candidate_count": candidate_count,
    }
    if reason:
        after["reason"] = reason
    record_event(
        action=(
            "prospect.email_found"
            if state == Prospect.PipelineState.EMAIL_FOUND
            else "prospect.skipped_no_email"
            if state == Prospect.PipelineState.SKIPPED_NO_EMAIL
            else "prospect.skipped_duplicate"
        ),
        entity=prospect,
        actor=None,
        before=before,
        after=after,
    )
    ensure_prospect_enrollment(
        prospect,
        campaign=prospect.campaign,
        provider=prospect.source_run.provider,
    )
    return prospect


@transaction.atomic
def _skip_website_contact(
    *,
    prospect_id: uuid.UUID | str,
    snapshot_id: uuid.UUID | str,
    candidate_count: int,
    reason: str = "",
) -> Prospect:
    prospect = Prospect.objects.select_for_update().select_related("campaign").get(pk=prospect_id)
    snapshot = WebsiteSnapshot.objects.get(pk=snapshot_id, prospect=prospect)
    if prospect.campaign.state not in {Campaign.State.DISCOVERING, Campaign.State.RUNNING}:
        raise ProspectPipelineInactive("La campaña ya no está activa.")
    if prospect.pipeline_state != Prospect.PipelineState.DISCOVERED:
        return prospect
    return _set_contact_terminal_locked(
        prospect=prospect,
        state=Prospect.PipelineState.SKIPPED_NO_EMAIL,
        snapshot=snapshot,
        candidate_count=candidate_count,
        reason=reason,
    )


def skip_website_email_after_mx_retries(
    prospect_id: uuid.UUID | str,
    *,
    snapshot: WebsiteSnapshot,
) -> Prospect:
    """Finish contact discovery after the bounded transient-MX retry budget."""

    return _skip_website_contact(
        prospect_id=prospect_id,
        snapshot_id=snapshot.pk,
        candidate_count=len(_website_candidates(snapshot)),
        reason="mx_retry_exhausted",
    )


@transaction.atomic
def _persist_website_emails(
    *,
    prospect_id: uuid.UUID | str,
    snapshot_id: uuid.UUID | str,
    selected: ValidatedEmail,
    valid_emails: tuple[ValidatedEmail, ...],
) -> Prospect:
    prospect = Prospect.objects.select_for_update().select_related("campaign").get(pk=prospect_id)
    snapshot = WebsiteSnapshot.objects.get(pk=snapshot_id, prospect=prospect)
    if prospect.campaign.state not in {Campaign.State.DISCOVERING, Campaign.State.RUNNING}:
        raise ProspectPipelineInactive("La campaña ya no está activa.")
    if prospect.pipeline_state != Prospect.PipelineState.DISCOVERED:
        return prospect

    email_keys = [_hash(ProspectIdentity.Kind.EMAIL, email.normalized) for email in valid_emails]
    _lock_keys(email_keys)
    eligible = tuple(email for email in valid_emails if not is_email_suppressed(email.normalized))
    if not eligible:
        return _set_contact_terminal_locked(
            prospect=prospect,
            state=Prospect.PipelineState.SKIPPED_NO_EMAIL,
            snapshot=snapshot,
            candidate_count=len(valid_emails),
        )
    selected_email = (
        selected
        if selected in eligible
        else select_validated_email(
            eligible,
            business_domain=prospect.business_domain or registrable_domain(prospect.website),
        )
    )
    eligible_keys = [_hash(ProspectIdentity.Kind.EMAIL, email.normalized) for email in eligible]

    normalized_values = [email.normalized for email in eligible]
    conflicting_email = (
        ProspectEmail.objects.select_for_update()
        .filter(normalized_email__in=normalized_values)
        .exclude(prospect=prospect)
        .first()
    )
    conflicting_identity = (
        ProspectIdentity.objects.select_for_update()
        .filter(
            kind=ProspectIdentity.Kind.EMAIL,
            value_hash__in=[item[1] for item in eligible_keys],
        )
        .exclude(prospect=prospect)
        .first()
    )
    if conflicting_email is not None or conflicting_identity is not None:
        return _set_contact_terminal_locked(
            prospect=prospect,
            state=Prospect.PipelineState.SKIPPED_DUPLICATE,
            snapshot=snapshot,
            candidate_count=len(eligible),
        )

    existing_values = set(prospect.emails.values_list("normalized_email", flat=True))
    checked_at = timezone.now()
    ProspectEmail.objects.bulk_create(
        ProspectEmail(
            prospect=prospect,
            original_email=email.original,
            normalized_email=email.normalized,
            domain=email.domain,
            local_part=email.local_part,
            source=email.source,
            source_url=email.source_url,
            source_content_hash=email.source_content_hash,
            provider_order=email.provider_order,
            mx_status=ProspectEmail.MXStatus.VALID,
            mx_checked_at=checked_at,
            is_primary=email.normalized == selected_email.normalized,
        )
        for email in eligible
        if email.normalized not in existing_values
    )
    existing_identity_keys = set(prospect.identities.values_list("kind", "value_hash"))
    ProspectIdentity.objects.bulk_create(
        ProspectIdentity(prospect=prospect, kind=kind, value_hash=value_hash)
        for kind, value_hash in eligible_keys
        if (kind, value_hash) not in existing_identity_keys
    )
    return _set_contact_terminal_locked(
        prospect=prospect,
        state=Prospect.PipelineState.EMAIL_FOUND,
        snapshot=snapshot,
        candidate_count=len(eligible),
    )


def discover_website_email(
    prospect_id: uuid.UUID | str,
    *,
    snapshot: WebsiteSnapshot,
    resolver: MXResolver | None = None,
) -> Prospect:
    prospect = Prospect.objects.select_related("campaign", "source_run").get(pk=prospect_id)
    if prospect.campaign.state not in {Campaign.State.DISCOVERING, Campaign.State.RUNNING}:
        raise ProspectPipelineInactive("La campaña ya no está activa.")
    if prospect.pipeline_state != Prospect.PipelineState.DISCOVERED:
        return prospect
    candidates = _website_candidates(snapshot)
    if not candidates:
        return _skip_website_contact(
            prospect_id=prospect.pk,
            snapshot_id=snapshot.pk,
            candidate_count=0,
        )
    active_resolver = resolver or (
        MockMXResolver() if prospect.source_run.provider == "fake" else DNSMXResolver()
    )
    try:
        selected, valid_emails = validate_and_select_email(
            candidates,
            business_domain=prospect.business_domain or registrable_domain(prospect.website),
            resolver=active_resolver,
        )
    except ValidationError:
        return _skip_website_contact(
            prospect_id=prospect.pk,
            snapshot_id=snapshot.pk,
            candidate_count=len(candidates),
        )
    return _persist_website_emails(
        prospect_id=prospect.pk,
        snapshot_id=snapshot.pk,
        selected=selected,
        valid_emails=valid_emails,
    )
