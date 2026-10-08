from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.audit.models import BackgroundJob
from apps.audit.services import record_event
from apps.campaigns.models import Campaign, OutboundMessage
from apps.catalogs.services import create_catalog
from apps.mailbox.models import GmailConnection


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
def test_dashboard_reports_whether_gmail_really_works_and_how_many_sends_failed(
    owner: User, private_catalog_dir: object
) -> None:
    del private_catalog_dir
    client = Client()
    client.force_login(owner)
    url = reverse("api-dashboard-summary")

    missing = client.get(url).json()["data"]["admin"]
    assert (missing["gmail_connected"], missing["gmail_status"]) == (False, "NONE")
    assert missing["failed_sends"] == 0

    connection = GmailConnection.objects.create(
        workspace=owner.membership.workspace,
        owner=owner,
        email="cuenta@example.invalid",
        status=GmailConnection.Status.ERROR,
    )
    broken = client.get(url).json()["data"]["admin"]
    # A saved connection that is in error is not a working one.
    assert (broken["gmail_connected"], broken["gmail_status"]) == (False, "ERROR")

    connection.status = GmailConnection.Status.CONNECTED
    connection.save(update_fields=("status",))
    working = client.get(url).json()["data"]["admin"]
    assert (working["gmail_connected"], working["gmail_status"]) == (True, "CONNECTED")

    catalog = create_catalog(
        name="Catálogo del resumen",
        upload=SimpleUploadedFile(
            "catalogo.pdf",
            b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
            content_type="application/pdf",
        ),
        actor=owner,
    )
    campaign = Campaign.objects.create(
        workspace=owner.membership.workspace,
        name="Con envío fallido",
        catalog=catalog,
        created_by=owner,
    )
    for state in (OutboundMessage.State.SEND_FAILED, OutboundMessage.State.SENT):
        OutboundMessage.objects.create(
            campaign=campaign,
            kind=OutboundMessage.Kind.INITIAL,
            state=state,
            recipient=f"{state.lower()}@example.invalid",
            recipient_normalized=f"{state.lower()}@example.invalid",
            subject="Asunto",
            body_text="Texto",
            idempotency_key=f"dash:{state}",
        )
    assert client.get(url).json()["data"]["admin"]["failed_sends"] == 1


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


@pytest.mark.django_db
def test_audit_log_is_authenticated_and_read_only(owner: User) -> None:
    url = reverse("api-audit-events")
    assert Client().get(url).status_code == 401

    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf = _csrf(client)
    assert client.get(url).status_code == 200
    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)(
            url, data="{}", content_type="application/json", HTTP_X_CSRFTOKEN=csrf
        )
        assert response.status_code == 405, method
