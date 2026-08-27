from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.compliance import services as compliance_services
from apps.compliance.models import SuppressionEntry
from apps.compliance.services import is_email_suppressed, normalize_email, suppress_email


def test_email_normalization_and_invalid_addresses() -> None:
    assert normalize_email(" Ventas@EXAMPLE.COM ") == "ventas@example.com"
    with pytest.raises(ValidationError):
        normalize_email("not-an-email")


@pytest.mark.django_db
def test_suppression_blocks_email_and_unsubscribe_is_permanent(owner: User) -> None:
    entry = suppress_email(
        email="Ventas@Example.com",
        reason=SuppressionEntry.Reason.MANUAL,
        actor=owner,
        evidence="Pedido telefónico",
    )
    assert is_email_suppressed("ventas@example.com")
    upgraded = suppress_email(
        email="ventas@example.com",
        reason=SuppressionEntry.Reason.UNSUBSCRIBE,
        actor=owner,
        source="inbound",
    )
    assert upgraded.pk == entry.pk
    assert upgraded.reason == SuppressionEntry.Reason.UNSUBSCRIBE
    unchanged = suppress_email(
        email="ventas@example.com", reason=SuppressionEntry.Reason.MANUAL, actor=owner
    )
    assert unchanged.reason == SuppressionEntry.Reason.UNSUBSCRIBE
    assert SuppressionEntry.objects.count() == 1
    assert AuditEvent.objects.filter(entity_type="SuppressionEntry").count() == 2


@pytest.mark.django_db
def test_invalid_email_is_never_eligible_and_reason_is_validated(owner: User) -> None:
    assert is_email_suppressed("invalid")
    with pytest.raises(ValidationError, match="motivo"):
        suppress_email(email="valid@example.com", reason="UNKNOWN", actor=owner)


@pytest.mark.django_db
def test_unique_collision_reloads_and_preserves_unsubscribe_precedence(
    owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = SuppressionEntry.objects.create(
        original_email="race@example.com",
        normalized_email="race@example.com",
        reason=SuppressionEntry.Reason.MANUAL,
        source="dashboard",
        created_by=owner,
    )
    original_first = QuerySet.first
    calls = 0

    def miss_existing_once(queryset: QuerySet[SuppressionEntry]) -> SuppressionEntry | None:
        nonlocal calls
        if queryset.model is SuppressionEntry and calls == 0:
            calls += 1
            return None
        return original_first(queryset)

    monkeypatch.setattr(QuerySet, "first", miss_existing_once)
    result = suppress_email(
        email="race@example.com",
        reason=SuppressionEntry.Reason.UNSUBSCRIBE,
        actor=owner,
        source="inbound",
        evidence="BAJA",
    )
    assert result.pk == existing.pk
    assert result.reason == SuppressionEntry.Reason.UNSUBSCRIBE
    assert result.source == "inbound"
    assert result.evidence == "BAJA"
    assert SuppressionEntry.objects.filter(normalized_email="race@example.com").count() == 1


def test_postgres_advisory_lock_is_stable_per_normalized_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[tuple[str, list[int]]] = []

    class Cursor:
        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def execute(self, sql: str, params: list[int]) -> None:
            executed.append((sql, params))

    class Connection:
        vendor = "postgresql"

        def cursor(self) -> Cursor:
            return Cursor()

    monkeypatch.setattr(compliance_services, "connection", Connection())
    compliance_services._lock_suppression_key("same@example.com")
    compliance_services._lock_suppression_key("same@example.com")
    compliance_services._lock_suppression_key("other@example.com")
    assert all(sql == "SELECT pg_advisory_xact_lock(%s)" for sql, _ in executed)
    assert executed[0][1] == executed[1][1]
    assert executed[0][1] != executed[2][1]


@pytest.mark.django_db
def test_suppression_dashboard_adds_entry_and_requires_login(client: Client, owner: User) -> None:
    assert client.get(reverse("suppressions")).status_code == 302
    client.force_login(owner)
    response = client.post(
        reverse("suppressions"),
        {"email": "blocked@example.com", "reason": SuppressionEntry.Reason.MANUAL},
    )
    assert response.status_code == 302
    assert is_email_suppressed("blocked@example.com")
    page = client.get(reverse("suppressions"))
    assert b"blocked@example.com" in page.content
