from __future__ import annotations

import json
import uuid

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.campaigns.models import Campaign
from apps.catalogs.services import create_catalog
from apps.configuration.models import SearchCategory, SearchZone


def _post(client: Client, url: str, payload: dict[str, object], csrf_token: str):
    return client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )


def _csrf(client: Client) -> str:
    return str(client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])


@pytest.mark.django_db
def test_campaign_list_requires_authentication_and_hides_draft_from_vendor(owner: User) -> None:
    anonymous = Client().get(reverse("api-campaigns"))
    assert anonymous.status_code == 401

    admin = Client()
    admin.force_login(owner)
    response = admin.get(reverse("api-campaigns"))
    assert response.status_code == 200
    assert response.json()["meta"] == {"page": 1, "page_size": 25, "total": 0}

    vendor = User.objects.create_user(
        username="campaign-vendor",
        password="vendor-password-1234",
        email="campaign-vendor@example.invalid",
    )
    vendor_client = Client()
    vendor_client.force_login(vendor)
    assert vendor_client.get(reverse("api-campaigns")).status_code == 200


@pytest.mark.django_db
def test_admin_can_create_a_validated_campaign_draft_and_detail_is_explicit(
    owner: User,
    private_catalog_dir,
) -> None:
    catalog = create_catalog(
        name="General",
        upload=SimpleUploadedFile(
            "catalog.pdf",
            b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
            content_type="application/pdf",
        ),
        actor=owner,
    )
    category = SearchCategory.objects.get(name="Bobinados de motores")
    zone = SearchZone.objects.get(name="Palermo")
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf_token = _csrf(client)
    payload = {
        "name": "Campaña API",
        "delivery_mode": Campaign.DeliveryMode.DRY_RUN,
        "approval_mode": Campaign.ApprovalMode.CAMPAIGN,
        "categories": [str(category.pk)],
        "provinces": [str(zone.parent_id)],
        "zones": [str(zone.pk)],
        "catalog": str(catalog.pk),
        "catalogs": [str(catalog.pk)],
        "location_text": "Buenos Aires",
        "objective": 300,
        "max_raw_records": 3000,
        "overture_min_confidence": "0.750",
        "daily_limit": 30,
        "message_interval_minutes": 5,
        "weekdays": [0, 1, 2, 3, 4],
        "window_start": "09:00",
        "window_end": "17:00",
        "timezone_name": "America/Argentina/Buenos_Aires",
        "relevance_threshold": 70,
        "reminder_enabled": False,
        "reminder_delay_days": 3,
        "confirm_live": False,
    }

    created = _post(client, reverse("api-campaigns"), payload, csrf_token)

    assert created.status_code == 201, created.content
    data = created.json()["data"]
    assert data["state"] == Campaign.State.DRAFT
    assert data["categories"][0]["name"] == category.name
    assert data["attachments"][0]["name"] == catalog.name

    detail = client.get(reverse("api-campaign-detail", args=(data["id"],)))
    assert detail.status_code == 200
    assert detail.json()["data"]["name"] == "Campaña API"
    assert "initial_body_snapshot" not in detail.json()["data"]


@pytest.mark.django_db
def test_campaign_actions_require_uuid_idempotency_key(owner: User) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf_token = _csrf(client)
    url = reverse(
        "api-campaign-action",
        args=("00000000-0000-0000-0000-000000000001", "cancel"),
    )

    missing = _post(client, url, {}, csrf_token)
    assert missing.status_code == 400
    assert missing.json()["code"] == "validation_error"

    invalid = client.post(
        url,
        data="{}",
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
        HTTP_IDEMPOTENCY_KEY="not-a-uuid",
    )
    assert invalid.status_code == 400


@pytest.mark.django_db
def test_campaign_action_replays_the_persisted_result_for_the_same_key(
    owner: User,
    private_catalog_dir,
) -> None:
    catalog = create_catalog(
        name="General",
        upload=SimpleUploadedFile(
            "catalog.pdf",
            b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
            content_type="application/pdf",
        ),
        actor=owner,
    )
    campaign = Campaign.objects.create(
        workspace=owner.membership.workspace,
        name="Campaña repetible",
        catalog=catalog,
        created_by=owner,
    )
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf_token = _csrf(client)
    url = reverse("api-campaign-action", args=(campaign.pk, "cancel"))
    key = str(uuid.uuid4())

    first = client.post(
        url,
        data="{}",
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
        HTTP_IDEMPOTENCY_KEY=key,
    )
    second = client.post(
        url,
        data="{}",
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
        HTTP_IDEMPOTENCY_KEY=key,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    assert Campaign.objects.get(pk=campaign.pk).state == Campaign.State.CANCELLED
    assert AuditEvent.objects.filter(action="campaign.transitioned").count() == 1
