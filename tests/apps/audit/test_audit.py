from __future__ import annotations

import uuid

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.audit.services import record_event


@pytest.mark.django_db
def test_audit_event_redacts_secrets_and_is_append_only(owner: User) -> None:
    event = record_event(
        action="test.changed",
        entity=owner,
        actor=owner,
        after={
            "api_key": "secret",
            "refresh_token": "refresh",
            "client-secret": "client",
            "field_encryption_key": "encryption",
            "nested": {
                "accessToken": "access",
                "oauth_credential": "credential",
                "safe": "visible",
            },
        },
        correlation_id=uuid.uuid4(),
    )
    assert event.after == {
        "api_key": "[REDACTED]",
        "refresh_token": "[REDACTED]",
        "client-secret": "[REDACTED]",
        "field_encryption_key": "[REDACTED]",
        "nested": {
            "accessToken": "[REDACTED]",
            "oauth_credential": "[REDACTED]",
            "safe": "visible",
        },
    }
    event.action = "tampered"
    with pytest.raises(ValidationError):
        event.save()
    with pytest.raises(ValidationError):
        event.delete()
    with pytest.raises(ValidationError):
        AuditEvent.objects.filter(pk=event.pk).update(action="tampered")
    with pytest.raises(ValidationError):
        AuditEvent.objects.all().delete()


@pytest.mark.django_db
def test_audit_log_is_authenticated_and_read_only(client: Client, owner: User) -> None:
    assert client.get(reverse("audit-log")).status_code == 302
    client.force_login(owner)
    record_event(action="test", entity=owner, actor=None)
    response = client.get(reverse("audit-log"))
    assert response.status_code == 200
    assert b"Audit log" in response.content
