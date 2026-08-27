from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.audit.models import BackgroundJob
from apps.audit.services import record_event
from apps.campaigns.models import Campaign


def _csrf(client: Client) -> str:
    return str(client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])


@pytest.mark.django_db
def test_dashboard_summary_is_authenticated_and_hides_admin_details_from_vendor(
    owner: User,
) -> None:
    anonymous = Client().get(reverse("api-dashboard-summary"))
    assert anonymous.status_code == 401

    admin_client = Client(enforce_csrf_checks=True)
    admin_client.force_login(owner)
    admin_data = admin_client.get(reverse("api-dashboard-summary")).json()["data"]
    assert "metrics" in admin_data
    assert "safety" in admin_data
    assert "admin" in admin_data
    assert admin_data["safety"]["send_kill_switch"] is True

    vendor = User.objects.create_user(
        username="dashboard-vendor",
        password="vendor-password-1234",
        email="dashboard-vendor@example.invalid",
    )
    vendor_client = Client()
    vendor_client.force_login(vendor)
    vendor_data = vendor_client.get(reverse("api-dashboard-summary")).json()["data"]
    assert "metrics" in vendor_data
    assert "admin" not in vendor_data
    assert all(item["state"] != Campaign.State.DRAFT for item in vendor_data["campaigns"])


@pytest.mark.django_db
def test_dashboard_rejects_campaigns_outside_the_callers_workspace(owner: User) -> None:
    client = Client()
    client.force_login(owner)

    response = client.get(
        reverse("api-dashboard-summary"),
        {"campaign_id": "00000000-0000-0000-0000-000000000001"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


@pytest.mark.django_db
def test_audit_and_jobs_are_admin_only_and_redact_sensitive_audit_payload(owner: User) -> None:
    record_event(
        action="api.test",
        entity=owner,
        actor=owner,
        after={"password": "must-not-appear", "safe": "value"},
    )
    job = BackgroundJob.objects.create(
        task_name="apps.example.task",
        idempotency_key="api-test-job",
        entity_type="Contact",
        entity_id="contact-id",
        queue="default",
        state=BackgroundJob.State.FAILED,
        error="Provider failure (redacted)",
    )
    client = Client()
    assert client.get(reverse("api-audit-events")).status_code == 401
    client.force_login(owner)

    audit = client.get(reverse("api-audit-events"), {"page_size": 1})
    assert audit.status_code == 200
    assert audit.json()["meta"] == {"page": 1, "page_size": 1, "total": 1}
    assert "before" not in audit.json()["data"][0]
    assert "password" not in audit.content.decode()

    jobs = client.get(reverse("api-background-jobs"), {"state": BackgroundJob.State.FAILED})
    assert jobs.status_code == 200
    assert jobs.json()["meta"]["total"] == 1
    assert jobs.json()["data"][0]["id"] == str(job.pk)

    detail = client.get(reverse("api-background-job-detail", args=(job.pk,)))
    assert detail.status_code == 200
    assert detail.json()["data"]["error"] == "Provider failure (redacted)"
