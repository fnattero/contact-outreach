from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import dns.exception
import dns.resolver
from django.core.exceptions import ValidationError
from email_validator import EmailNotValidError, validate_email

from apps.compliance.services import is_email_suppressed
from apps.integrations.contracts import ExtractedEmail

EXCLUDED_LOCAL_PARTS = frozenset(
    {"noreply", "no-reply", "do-not-reply", "donotreply", "abuse", "privacy"}
)
COMMERCIAL_ROLES = ("ventas", "sales", "info", "contacto", "comercial")


class MXStatus(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    TRANSIENT = "TRANSIENT"


class MXResolver(Protocol):
    def resolve(self, domain: str) -> MXStatus: ...


class DNSMXResolver:
    def __init__(self, *, timeout_seconds: float = 5.0) -> None:
        self._resolver = dns.resolver.Resolver(configure=True)
        self._resolver.timeout = timeout_seconds
        self._resolver.lifetime = timeout_seconds

    def resolve(self, domain: str) -> MXStatus:
        try:
            answers = self._resolver.resolve(domain, "MX")
            exchanges = [str(answer.exchange).rstrip(".") for answer in answers]
            return MXStatus.INVALID if exchanges == [""] else MXStatus.VALID
        except dns.resolver.NXDOMAIN:
            return MXStatus.INVALID
        except dns.resolver.NoAnswer:
            return self._address_fallback(domain)
        except (dns.resolver.LifetimeTimeout, dns.resolver.NoNameservers, dns.exception.Timeout):
            return MXStatus.TRANSIENT

    def _address_fallback(self, domain: str) -> MXStatus:
        transient = False
        for record_type in ("A", "AAAA"):
            try:
                if self._resolver.resolve(domain, record_type):
                    return MXStatus.VALID
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
                continue
            except (
                dns.resolver.LifetimeTimeout,
                dns.resolver.NoNameservers,
                dns.exception.Timeout,
            ):
                transient = True
        return MXStatus.TRANSIENT if transient else MXStatus.INVALID


class MockMXResolver:
    """Deterministic resolver used with the mock extractor and in tests."""

    def resolve(self, domain: str) -> MXStatus:
        del domain
        return MXStatus.VALID


@dataclass(frozen=True, slots=True)
class ValidatedEmail:
    original: str
    normalized: str
    local_part: str
    domain: str
    source: str
    provider_order: int
    provider_primary: bool
    mx_status: MXStatus
    source_url: str = ""
    source_content_hash: str = ""


class TransientMXError(RuntimeError):
    pass


def _normalize(candidate: ExtractedEmail) -> ValidatedEmail | None:
    try:
        result = validate_email(candidate.value, check_deliverability=False)
    except EmailNotValidError:
        return None
    local_part = result.local_part
    domain = result.ascii_domain.casefold() if result.ascii_domain else result.domain.casefold()
    normalized = f"{local_part.casefold()}@{domain}"
    if local_part.casefold() in EXCLUDED_LOCAL_PARTS:
        return None
    if is_email_suppressed(normalized):
        return None
    return ValidatedEmail(
        original=candidate.value,
        normalized=normalized,
        local_part=local_part,
        domain=domain,
        source=candidate.source,
        provider_order=candidate.order,
        provider_primary=candidate.is_primary,
        mx_status=MXStatus.VALID,
        source_url=candidate.source_url,
        source_content_hash=candidate.source_content_hash,
    )


def validate_and_select_email(
    candidates: tuple[ExtractedEmail, ...],
    *,
    business_domain: str,
    resolver: MXResolver,
) -> tuple[ValidatedEmail, tuple[ValidatedEmail, ...]]:
    validated: list[ValidatedEmail] = []
    seen: set[str] = set()
    saw_transient = False
    for candidate in candidates:
        normalized = _normalize(candidate)
        if normalized is None:
            continue
        mx_status = resolver.resolve(normalized.domain)
        if mx_status == MXStatus.TRANSIENT:
            saw_transient = True
            continue
        if mx_status == MXStatus.INVALID:
            continue
        if normalized.normalized in seen:
            continue
        seen.add(normalized.normalized)
        validated.append(normalized)
    if not validated:
        if saw_transient:
            raise TransientMXError("La validación MX falló temporalmente.")
        raise ValidationError("El negocio no tiene un correo sintáctica y operativamente válido.")

    return select_validated_email(tuple(validated), business_domain=business_domain), tuple(
        validated
    )


def select_validated_email(
    validated: tuple[ValidatedEmail, ...], *, business_domain: str
) -> ValidatedEmail:
    if not validated:
        raise ValidationError("No hay correos validados para seleccionar.")

    def priority(email: ValidatedEmail) -> tuple[int, int, int, int]:
        role = email.local_part.casefold()
        role_rank = next(
            (index for index, value in enumerate(COMMERCIAL_ROLES) if role == value),
            len(COMMERCIAL_ROLES),
        )
        return (
            0 if email.provider_primary else 1,
            0 if business_domain and email.domain == business_domain else 1,
            role_rank,
            email.provider_order,
        )

    return min(validated, key=priority)
