from __future__ import annotations

import dns.exception
import dns.resolver
import pytest
from django.core.exceptions import ValidationError

from apps.integrations.contracts import ExtractedEmail
from apps.prospects.email_validation import (
    DNSMXResolver,
    MXStatus,
    TransientMXError,
    validate_and_select_email,
)


class Resolver:
    def __init__(self, statuses: dict[str, MXStatus]) -> None:
        self.statuses = statuses
        self.queries: list[str] = []

    def resolve(self, domain: str) -> MXStatus:
        self.queries.append(domain)
        return self.statuses.get(domain, MXStatus.VALID)


class MXAnswer:
    def __init__(self, exchange: str) -> None:
        self.exchange = exchange


class ScriptedDNS:
    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers

    def resolve(self, domain: str, record_type: str) -> object:
        del domain
        result = self.answers[record_type]
        if isinstance(result, Exception):
            raise result
        return result


def dns_resolver(*, mx: object, a: object = (), aaaa: object = ()) -> DNSMXResolver:
    resolver = object.__new__(DNSMXResolver)
    resolver._resolver = ScriptedDNS({"MX": mx, "A": a, "AAAA": aaaa})  # type: ignore[assignment]
    return resolver


@pytest.mark.django_db
def test_email_selection_filters_syntax_roles_and_mx() -> None:
    resolver = Resolver(
        {
            "invalid-mx.example": MXStatus.INVALID,
            "empresa.example": MXStatus.VALID,
        }
    )
    candidates = (
        ExtractedEmail("not-an-email", order=0),
        ExtractedEmail("noreply@empresa.example", is_primary=True, order=1),
        ExtractedEmail("persona@invalid-mx.example", is_primary=True, order=2),
        ExtractedEmail("contacto@empresa.example", order=3),
        ExtractedEmail("ventas@otro.example", order=4),
    )

    selected, valid = validate_and_select_email(
        candidates, business_domain="empresa.example", resolver=resolver
    )

    assert selected.normalized == "contacto@empresa.example"
    assert [email.normalized for email in valid] == [
        "contacto@empresa.example",
        "ventas@otro.example",
    ]
    assert "empresa.example" in resolver.queries


@pytest.mark.django_db
def test_provider_primary_precedes_domain_and_role() -> None:
    selected, _ = validate_and_select_email(
        (
            ExtractedEmail("ventas@empresa.example", order=0),
            ExtractedEmail("persona@otro.example", is_primary=True, order=1),
        ),
        business_domain="empresa.example",
        resolver=Resolver({}),
    )
    assert selected.normalized == "persona@otro.example"


@pytest.mark.django_db
def test_transient_mx_is_recoverable_and_invalid_set_is_rejected() -> None:
    with pytest.raises(TransientMXError):
        validate_and_select_email(
            (ExtractedEmail("ventas@temporal.example"),),
            business_domain="temporal.example",
            resolver=Resolver({"temporal.example": MXStatus.TRANSIENT}),
        )
    with pytest.raises(ValidationError):
        validate_and_select_email(
            (ExtractedEmail("ventas@invalid.example"),),
            business_domain="invalid.example",
            resolver=Resolver({"invalid.example": MXStatus.INVALID}),
        )


def test_dns_mx_resolver_handles_null_mx_fallback_and_transient_errors() -> None:
    assert dns_resolver(mx=[MXAnswer("mail.example.")]).resolve("example.com") == MXStatus.VALID
    assert dns_resolver(mx=[MXAnswer(".")]).resolve("example.com") == MXStatus.INVALID
    assert (
        dns_resolver(mx=dns.resolver.NoAnswer(), a=[object()]).resolve("example.com")
        == MXStatus.VALID
    )
    assert dns_resolver(mx=dns.resolver.NXDOMAIN()).resolve("example.com") == MXStatus.INVALID
    assert dns_resolver(mx=dns.exception.Timeout()).resolve("example.com") == MXStatus.TRANSIENT
