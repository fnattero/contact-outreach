from __future__ import annotations

import json
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.campaigns.models import Campaign
from apps.catalogs.models import Catalog
from apps.catalogs.services import create_catalog
from apps.configuration.models import IntegrationConfiguration, SearchCategory, SearchZone
from apps.configuration.services import save_business_profile
from apps.integrations.factory import get_website_fetcher
from apps.integrations.website import HttpWebsiteFetcher

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"


class _Fixture:
    def __init__(self, owner: User) -> None:
        self.owner = owner
        self.catalog = create_catalog(
            name="General",
            upload=SimpleUploadedFile("catalog.pdf", PDF, content_type="application/pdf"),
            actor=owner,
        )
        self.category = SearchCategory.objects.get(name="Bobinados de motores")
        self.zone = SearchZone.objects.get(name="Palermo")
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(owner)
        self.csrf = str(self.client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])

    def payload(self, **overrides: object) -> dict[str, object]:
        values: dict[str, object] = {
            "name": "Campaña de prueba",
            "delivery_mode": Campaign.DeliveryMode.DRY_RUN,
            "approval_mode": Campaign.ApprovalMode.CAMPAIGN,
            "categories": [str(self.category.pk)],
            "provinces": [str(self.zone.parent_id)],
            "zones": [str(self.zone.pk)],
            "catalogs": [str(self.catalog.pk)],
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
            "reminder_enabled": False,
            "reminder_delay_days": 3,
            "confirm_live": False,
        }
        values.update(overrides)
        return values

    def create(self, **overrides: object):
        return self.client.post(
            reverse("api-campaigns"),
            data=json.dumps(self.payload(**overrides)),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=self.csrf,
        )


@pytest.fixture
def campaign_api(owner: User, private_catalog_dir: Any) -> _Fixture:
    del private_catalog_dir
    return _Fixture(owner)


def _field_errors(response: Any) -> dict[str, list[str]]:
    assert response.status_code == 400, response.content
    assert response.json()["code"] == "validation_error"
    return dict(response.json()["field_errors"])


@pytest.mark.django_db
def test_valid_payload_creates_a_draft_using_the_workspace_provider_configuration(
    campaign_api: _Fixture,
) -> None:
    response = campaign_api.create(
        # Provider fields are never taken from the client, even when it sends them.
        extractor_provider="overture",
        llm_provider="openai-compatible",
        llm_model="client-chosen-model",
        llm_base_url="https://attacker.invalid/v1",
    )

    assert response.status_code == 201, response.content
    campaign = Campaign.objects.get(pk=response.json()["data"]["id"])
    assert campaign.state == Campaign.State.DRAFT
    assert campaign.extractor_provider == "fake"
    assert campaign.llm_provider == "fake"
    assert campaign.llm_model != "client-chosen-model"
    assert campaign.llm_base_url == ""
    assert campaign.catalog == campaign_api.catalog
    assert campaign.weekdays == [0, 1, 2, 3, 4]


@pytest.mark.django_db
def test_vendedor_cannot_create_and_anonymous_is_unauthenticated(owner: User) -> None:
    url = reverse("api-campaigns")
    assert Client().post(url, data="{}", content_type="application/json").status_code in {
        401,
        403,
    }
    vendor = User.objects.create_user(username="campaign-vendor", password="vendor-password-1234")
    client = Client(enforce_csrf_checks=True)
    client.force_login(vendor)
    csrf = str(client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])
    denied = client.post(url, data="{}", content_type="application/json", HTTP_X_CSRFTOKEN=csrf)
    assert denied.status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("body", ["[1, 2]", '"text"'])
def test_non_object_body_is_a_400_not_a_500(campaign_api: _Fixture, body: str) -> None:
    response = campaign_api.client.post(
        reverse("api-campaigns"),
        data=body,
        content_type="application/json",
        HTTP_X_CSRFTOKEN=campaign_api.csrf,
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_archived_or_inactive_categories_are_not_selectable(campaign_api: _Fixture) -> None:
    SearchCategory.objects.filter(pk=campaign_api.category.pk).update(archived_at=timezone.now())
    assert "categories" in _field_errors(campaign_api.create())

    SearchCategory.objects.filter(pk=campaign_api.category.pk).update(
        archived_at=None, active=False
    )
    assert "categories" in _field_errors(campaign_api.create())


@pytest.mark.django_db
def test_unknown_ids_are_rejected_without_revealing_whether_they_exist(
    campaign_api: _Fixture,
) -> None:
    errors = _field_errors(
        campaign_api.create(
            categories=["00000000-0000-4000-8000-000000000001"],
            catalogs=["00000000-0000-4000-8000-000000000002"],
        )
    )

    assert set(errors) == {"categories", "catalogs"}
    assert Campaign.objects.count() == 0


@pytest.mark.django_db
def test_only_district_and_neighborhood_zones_are_selectable(campaign_api: _Fixture) -> None:
    province = campaign_api.zone.parent
    assert province is not None
    errors = _field_errors(campaign_api.create(zones=[str(province.pk)]))
    assert "zones" in errors

    SearchZone.objects.filter(pk=campaign_api.zone.pk).update(
        active=False, level=SearchZone.Level.CUSTOM
    )
    assert "zones" in _field_errors(campaign_api.create())


@pytest.mark.django_db
def test_zones_need_their_province_and_every_province_needs_a_zone(
    campaign_api: _Fixture,
) -> None:
    missing_province = _field_errors(campaign_api.create(provinces=[]))
    assert "provinces" in missing_province

    other = SearchZone.objects.filter(level=SearchZone.Level.PROVINCE).exclude(
        pk=campaign_api.zone.parent_id
    )[0]
    extra = _field_errors(
        campaign_api.create(provinces=[str(campaign_api.zone.parent_id), str(other.pk)])
    )
    assert "zones" in extra


@pytest.mark.django_db
def test_catalog_must_be_active_present_and_within_size_limits(campaign_api: _Fixture) -> None:
    Catalog.objects.filter(pk=campaign_api.catalog.pk).update(byte_size=16 * 1024 * 1024)
    oversize = _field_errors(campaign_api.create())
    assert any("15 MiB" in message for message in oversize["catalogs"])

    second = create_catalog(
        name="Segundo",
        upload=SimpleUploadedFile(
            "second.pdf", PDF + b"\n% second", content_type="application/pdf"
        ),
        actor=campaign_api.owner,
    )
    Catalog.objects.filter(pk=campaign_api.catalog.pk).update(byte_size=9 * 1024 * 1024)
    Catalog.objects.filter(pk=second.pk).update(byte_size=9 * 1024 * 1024)
    combined = _field_errors(
        campaign_api.create(catalogs=[str(campaign_api.catalog.pk), str(second.pk)])
    )
    assert any("17 MiB" in message for message in combined["catalogs"])

    Catalog.objects.filter(pk=campaign_api.catalog.pk).update(byte_size=1024, missing=True)
    assert "catalogs" in _field_errors(campaign_api.create())


@pytest.mark.django_db
def test_live_delivery_requires_explicit_confirmation(campaign_api: _Fixture) -> None:
    errors = _field_errors(campaign_api.create(delivery_mode=Campaign.DeliveryMode.LIVE))

    assert "confirm_live" in errors
    assert Campaign.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("weekdays", []),
        ("weekdays", [7]),
        ("objective", 0),
        ("daily_limit", 0),
        ("reminder_delay_days", 0),
        ("overture_min_confidence", "1.500"),
        ("delivery_mode", "NOT_A_MODE"),
        ("name", ""),
    ],
)
def test_field_level_rules_reject_out_of_range_values(
    campaign_api: _Fixture, field: str, value: object
) -> None:
    assert field in _field_errors(campaign_api.create(**{field: value}))


@pytest.mark.django_db
def test_model_rules_enforced_by_the_service_map_back_onto_fields(campaign_api: _Fixture) -> None:
    errors = _field_errors(
        campaign_api.create(
            objective=400,
            max_raw_records=300,
            window_start="18:00",
            window_end="09:00",
            timezone_name="Invalid/Zone",
        )
    )

    assert {"max_raw_records", "window_end", "timezone_name"} <= set(errors)
    assert Campaign.objects.count() == 0


@pytest.mark.django_db
def test_legacy_custom_zones_are_not_selectable_even_when_active(campaign_api: _Fixture) -> None:
    custom = SearchZone.objects.create(
        workspace=campaign_api.owner.membership.workspace,
        name="Corredor custom",
        normalized_name="corredor custom",
        kind=SearchZone.Kind.CUSTOM,
        level=SearchZone.Level.CUSTOM,
        source=SearchZone.Source.CUSTOM,
        selectable=True,
        active=True,
        boundary_geojson={
            "type": "Polygon",
            "coordinates": [
                [[-58.6, -34.7], [-58.5, -34.7], [-58.5, -34.6], [-58.6, -34.6], [-58.6, -34.7]]
            ],
        },
        boundary_hash="custom-hash",
    )

    errors = _field_errors(campaign_api.create(zones=[str(custom.pk)]))

    assert "zones" in errors


@pytest.mark.django_db
def test_deleting_a_category_used_by_a_campaign_archives_it_instead(
    campaign_api: _Fixture,
) -> None:
    created = campaign_api.create()
    assert created.status_code == 201, created.content

    response = campaign_api.client.delete(
        reverse("api-search-category-detail", args=(campaign_api.category.pk,)),
        HTTP_X_CSRFTOKEN=campaign_api.csrf,
    )

    assert response.status_code == 200, response.content
    assert response.json()["data"] == {"outcome": "archived"}
    campaign_api.category.refresh_from_db()
    assert campaign_api.category.archived_at is not None
    assert campaign_api.category.active is False
    # The campaign keeps the snapshot it was created with.
    detail = campaign_api.client.get(
        reverse("api-campaign-detail", args=(created.json()["data"]["id"],))
    )
    assert detail.json()["data"]["categories"][0]["name"] == campaign_api.category.name


@pytest.mark.django_db
def test_campaign_action_with_a_non_object_body_is_a_400_and_changes_nothing(
    campaign_api: _Fixture,
) -> None:
    created = campaign_api.create()
    campaign_id = created.json()["data"]["id"]

    response = campaign_api.client.post(
        reverse("api-campaign-action", args=(campaign_id, "start-discovery")),
        data="[1, 2]",
        content_type="application/json",
        HTTP_X_CSRFTOKEN=campaign_api.csrf,
        HTTP_IDEMPOTENCY_KEY="3f2c1a44-8d2b-4f3a-9c55-0b9e6d1c7a10",
    )

    assert response.status_code == 400
    assert Campaign.objects.get(pk=campaign_id).state == Campaign.State.DRAFT


@pytest.mark.django_db
def test_the_configured_website_fetcher_is_snapshotted_and_discovery_can_start(
    campaign_api: _Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_business_profile(
        owner=campaign_api.owner,
        values={
            "company_name": "Componentes Delta SA",
            "salesperson_name": "Vendedor",
            "phone": "",
            "whatsapp": "",
            "description": "",
            "products": "Componentes industriales",
            "differentiators": "",
            "address": "CABA",
            "website": "",
            "signature": "Vendedor · Componentes Delta SA",
            "additional_instructions": "",
        },
    )
    IntegrationConfiguration.objects.create(
        owner=campaign_api.owner,
        workspace=campaign_api.owner.membership.workspace,
        website_fetcher="http",
    )
    monkeypatch.setattr("apps.api.campaigns.orchestrate_extraction.delay", lambda campaign_id: None)

    created = campaign_api.create(objective=450, max_raw_records=4000, name="Campaña 450")

    assert created.status_code == 201, created.content
    campaign = Campaign.objects.get(pk=created.json()["data"]["id"])
    assert campaign.objective == 450
    assert campaign.website_fetcher == "http"
    assert isinstance(get_website_fetcher(campaign.website_fetcher), HttpWebsiteFetcher)

    started = campaign_api.client.post(
        reverse("api-campaign-action", args=(campaign.pk, "start-discovery")),
        data="{}",
        content_type="application/json",
        HTTP_X_CSRFTOKEN=campaign_api.csrf,
        HTTP_IDEMPOTENCY_KEY="7b1a5e0c-2f43-4c1e-8e57-3c9d1a2f6b11",
    )
    assert started.status_code == 200, started.content
    campaign.refresh_from_db()
    assert campaign.state == Campaign.State.DISCOVERING
    assert campaign.search_queries.count() == 1
