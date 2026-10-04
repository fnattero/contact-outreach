from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.campaigns.models import Campaign, SearchQuery, SearchRun
from apps.catalogs.models import Catalog
from apps.catalogs.services import create_catalog
from apps.prospects.models import Prospect, ProspectEmail


def _campaign(owner: User, catalog: Catalog, name: str) -> Campaign:
    return Campaign.objects.create(
        name=name,
        state=Campaign.State.PAUSED,
        discovery_state=Campaign.DiscoveryState.EXHAUSTED_QUERIES,
        delivery_mode=Campaign.DeliveryMode.DRY_RUN,
        catalog=catalog,
        created_by=owner,
    )


def _prospect(
    campaign: Campaign, name: str, *, category: str, state: str, email: str | None = None
) -> Prospect:
    query = SearchQuery.objects.create(
        campaign=campaign,
        category_snapshot=category,
        zone_snapshot="Palermo",
        location_snapshot="CABA",
        query_text=f"{category} Palermo {name}",
        normalized_query=f"{category} palermo {name}".casefold(),
    )
    run = SearchRun.objects.create(
        campaign=campaign,
        query=query,
        provider="fake",
        idempotency_key=f"run:{campaign.pk}:{name}",
        requested_limit=10,
        raw_count=1,
        email_count=1 if email else 0,
        duplicate_count=0,
        state=SearchRun.State.SUCCEEDED,
    )
    prospect = Prospect.objects.create(
        campaign=campaign,
        source_run=run,
        name=name,
        normalized_name=name.casefold(),
        category=category,
        neighborhood="Palermo",
        pipeline_state=state,
    )
    if email:
        local, domain = email.split("@")
        ProspectEmail.objects.create(
            prospect=prospect,
            original_email=email,
            normalized_email=email,
            domain=domain,
            local_part=local,
            source="fixture",
            mx_status=ProspectEmail.MXStatus.VALID,
            mx_checked_at=timezone.now(),
            is_primary=True,
        )
    return prospect


@pytest.fixture
def audience(owner: User, private_catalog_dir: object) -> dict[str, object]:
    del private_catalog_dir
    catalog = create_catalog(
        name="Audiencia",
        upload=SimpleUploadedFile(
            "catalogo.pdf",
            b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
            content_type="application/pdf",
        ),
        actor=owner,
    )
    first = _campaign(owner, catalog, "Primera campaña")
    second = _campaign(owner, catalog, "Segunda campaña")
    return {
        "first": first,
        "second": second,
        "taller": _prospect(
            first,
            "Taller Uno",
            category="Motores",
            state=Prospect.PipelineState.QUEUED,
            email="uno@taller.example",
        ),
        "ferreteria": _prospect(
            first, "Ferretería Dos", category="Ferretería", state=Prospect.PipelineState.QUEUED
        ),
        "otro": _prospect(
            second, "Taller Tres", category="Motores", state=Prospect.PipelineState.ERROR
        ),
    }


@pytest.mark.django_db
def test_prospects_are_listed_across_campaigns_with_their_selected_email(
    owner: User, audience: dict[str, object]
) -> None:
    client = Client()
    client.force_login(owner)

    response = client.get(reverse("api-prospects"))

    assert response.status_code == 200
    assert response.json()["meta"] == {"page": 1, "page_size": 25, "total": 3}
    by_name = {item["name"]: item for item in response.json()["data"]}
    assert by_name["Taller Uno"]["primary_email"] == "uno@taller.example"
    assert by_name["Ferretería Dos"]["primary_email"] is None
    assert by_name["Taller Tres"]["campaign"]["name"] == "Segunda campaña"
    assert by_name["Taller Tres"]["pipeline_state_label"]


@pytest.mark.django_db
def test_prospects_can_be_filtered_searched_and_paginated(
    owner: User, audience: dict[str, object]
) -> None:
    client = Client()
    client.force_login(owner)
    url = reverse("api-prospects")
    second = audience["second"]
    assert isinstance(second, Campaign)

    by_campaign = client.get(url, {"campaign": str(second.pk)}).json()
    assert [item["name"] for item in by_campaign["data"]] == ["Taller Tres"]

    by_state = client.get(url, {"state": Prospect.PipelineState.ERROR}).json()
    assert by_state["meta"]["total"] == 1

    searched = client.get(url, {"q": "taller"}).json()
    assert searched["meta"]["total"] == 2  # "Ferretería Dos" does not match
    by_email = client.get(url, {"q": "uno@taller.example"}).json()
    assert [item["name"] for item in by_email["data"]] == ["Taller Uno"]

    page = client.get(url, {"page_size": 1, "page": 2}).json()
    assert len(page["data"]) == 1
    assert page["meta"] == {"page": 2, "page_size": 1, "total": 3}


@pytest.mark.django_db
@pytest.mark.parametrize("params", [{"campaign": "no-es-uuid"}, {"state": "NOPE"}, {"page": 0}])
def test_invalid_prospect_filters_are_rejected(
    owner: User, audience: dict[str, object], params: dict[str, object]
) -> None:
    client = Client()
    client.force_login(owner)

    response = client.get(reverse("api-prospects"), params)

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


@pytest.mark.django_db
def test_prospect_list_is_restricted_to_campaign_managers(
    owner: User, audience: dict[str, object]
) -> None:
    vendor = User.objects.create_user(username="prospect-vendor", password="vendor-password-1")
    seller = Client()
    seller.force_login(vendor)

    assert Client().get(reverse("api-prospects")).status_code == 401
    denied = seller.get(reverse("api-prospects"))
    assert denied.status_code == 403
    assert "Taller Uno" not in denied.content.decode()
