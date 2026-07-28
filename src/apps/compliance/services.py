from __future__ import annotations

import hashlib

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.accounts.permissions import Capability, require_user_capability
from apps.audit.services import record_event
from apps.compliance.models import SuppressionEntry


def normalize_email(value: str) -> str:
    email = value.strip()
    validate_email(email)
    local_part, domain = email.rsplit("@", 1)
    try:
        ascii_domain = domain.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValidationError("El dominio del email no es válido.") from exc
    normalized = f"{local_part.casefold()}@{ascii_domain.casefold()}"
    validate_email(normalized)
    return normalized


def lock_email_eligibility(normalized_email: str) -> None:
    """Serialize final delivery authorization with suppression/invalidation writes."""
    if connection.vendor != "postgresql":
        return
    digest = hashlib.sha256(normalized_email.encode()).digest()
    lock_id = int.from_bytes(digest[:8], byteorder="big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_id])


def _lock_suppression_key(normalized_email: str) -> None:
    """Backward-compatible name for the shared eligibility lock."""
    lock_email_eligibility(normalized_email)


def _merge_existing_suppression(
    *,
    entry: SuppressionEntry,
    reason: str,
    actor: User | None,
    source: str,
    evidence: str,
) -> SuppressionEntry:
    if entry.reason == SuppressionEntry.Reason.UNSUBSCRIBE:
        return entry
    if reason == SuppressionEntry.Reason.UNSUBSCRIBE:
        before = {"reason": entry.reason}
        entry.reason = reason
        entry.source = source
        entry.evidence = evidence
        entry.save(update_fields=("reason", "source", "evidence", "updated_at"))
        record_event(
            action="suppression.upgraded",
            entity=entry,
            actor=actor,
            before=before,
            after={"reason": reason},
        )
    return entry


@transaction.atomic
def suppress_email(
    *,
    email: str,
    reason: str,
    actor: User | None,
    source: str = "dashboard",
    evidence: str = "",
) -> SuppressionEntry:
    if actor is not None:
        require_user_capability(actor, Capability.MANAGE_CONTACTS)
    normalized = normalize_email(email)
    if reason not in SuppressionEntry.Reason.values:
        raise ValidationError("El motivo de supresión no es válido.")
    lock_email_eligibility(normalized)
    if reason == SuppressionEntry.Reason.BOUNCE:
        from apps.prospects.models import ProspectEmail

        ProspectEmail.objects.filter(normalized_email=normalized).update(
            is_invalid=True,
            invalid_reason=SuppressionEntry.Reason.BOUNCE,
            invalidated_at=timezone.now(),
        )
    entry = SuppressionEntry.objects.select_for_update().filter(normalized_email=normalized).first()
    if entry is not None:
        return _merge_existing_suppression(
            entry=entry,
            reason=reason,
            actor=actor,
            source=source,
            evidence=evidence,
        )
    try:
        with transaction.atomic():
            entry = SuppressionEntry.objects.create(
                original_email=email.strip(),
                normalized_email=normalized,
                reason=reason,
                source=source,
                evidence=evidence,
                created_by=actor,
            )
    except IntegrityError:
        entry = SuppressionEntry.objects.select_for_update().get(normalized_email=normalized)
        return _merge_existing_suppression(
            entry=entry,
            reason=reason,
            actor=actor,
            source=source,
            evidence=evidence,
        )
    record_event(
        action="suppression.created",
        entity=entry,
        actor=actor,
        after={"normalized_email": normalized, "reason": reason},
    )
    return entry


def is_email_suppressed(email: str) -> bool:
    try:
        normalized = normalize_email(email)
    except ValidationError:
        return True
    return SuppressionEntry.objects.filter(normalized_email=normalized).exists()
