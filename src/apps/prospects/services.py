from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

from apps.campaigns.models import SearchRun
from apps.configuration.models import normalize_name
from apps.integrations.contracts import ExtractedBusiness
from apps.prospects.email_validation import (
    MXResolver,
    ValidatedEmail,
    validate_and_select_email,
)
from apps.prospects.models import Prospect, ProspectEmail, ProspectIdentity

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
ARGENTINA_SECOND_LEVEL = frozenset({"com.ar", "net.ar", "org.ar"})


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
    selected_email: ValidatedEmail
    valid_emails: tuple[ValidatedEmail, ...]


def registrable_domain(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlsplit(url if "://" in url else f"https://{url}")
    try:
        hostname = (parsed.hostname or "").encode("idna").decode("ascii").casefold()
    except UnicodeError:
        return ""
    labels = hostname.rstrip(".").split(".")
    if len(labels) < 2:
        return hostname
    suffix_two = ".".join(labels[-2:])
    if suffix_two in ARGENTINA_SECOND_LEVEL and len(labels) >= 3:
        return ".".join(labels[-3:])
    return suffix_two


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
    except ValidationError:
        return None
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

    keys = _identity_keys(
        business,
        provider=run.provider,
        normalized_emails=tuple(email.normalized for email in valid_emails),
        business_domain=business_domain,
    )
    _lock_keys(keys)
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
                    provider_order=email.provider_order,
                    mx_status=ProspectEmail.MXStatus.VALID,
                    mx_checked_at=checked_at,
                    is_primary=False,
                )
                for email in valid_emails
                if email.normalized not in existing_email_values
            ],
            ignore_conflicts=True,
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
        pipeline_state=Prospect.PipelineState.EMAIL_FOUND,
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
            provider_order=email.provider_order,
            mx_status=ProspectEmail.MXStatus.VALID,
            mx_checked_at=checked_at,
            is_primary=email.normalized == selected.normalized,
        )
        for email in valid_emails
    )
    return IngestResult(IngestOutcome.CREATED, prospect)


def ingest_business(
    *, run: SearchRun, business: ExtractedBusiness, resolver: MXResolver
) -> IngestResult:
    prepared = prepare_business(business=business, resolver=resolver)
    if prepared is None:
        return IngestResult(IngestOutcome.NO_EMAIL)
    return ingest_prepared_business(run=run, prepared=prepared)
