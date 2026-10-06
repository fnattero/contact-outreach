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
from apps.prospects.models import Prospect, ProspectEmail, ProspectRelevanceVerdict


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


@pytest.mark.django_db
def test_overture_prospects_expose_provenance_attribution_and_licences(
    owner: User, audience: dict[str, object]
) -> None:
    taller = audience["taller"]
    assert isinstance(taller, Prospect)
    taller.provider_data = {
        "overture_id": "08f2a100-6f6a-4f31-9a6f-2e931c237f81",
        "confidence": "0.920",
        "matched_rule": {"taxonomy_code": "", "name_terms": ["bobinado"]},
        "snapshot": {
            "release_id": "2026-07-22.0",
            "attribution": "Overture Maps Foundation, overturemaps.org",
            "source_licenses": ["CDLA Permissive 2.0"],
        },
        "zone": {"attribution": "Instituto Geográfico Nacional"},
        "provenance": {"source_licenses": ["CDLA Permissive 2.0", "ODbL 1.0"]},
    }
    taller.save(update_fields=("provider_data", "updated_at"))
    client = Client()
    client.force_login(owner)

    rows = {item["name"]: item for item in client.get(reverse("api-prospects")).json()["data"]}

    provenance = rows["Taller Uno"]["provenance"]
    assert provenance["overture_id"] == "08f2a100-6f6a-4f31-9a6f-2e931c237f81"
    assert provenance["release_id"] == "2026-07-22.0"
    assert provenance["confidence"] == "0.920"
    assert provenance["matched_rule"] == {"taxonomy_code": "", "name_terms": ["bobinado"]}
    assert provenance["attribution"] == [
        "Overture Maps Foundation, overturemaps.org",
        "Instituto Geográfico Nacional",
    ]
    assert provenance["licenses"] == ["CDLA Permissive 2.0", "ODbL 1.0"]
    assert provenance["contact_source"]["email"] == "uno@taller.example"
    # Records that never came from Overture simply have no provenance to show.
    assert rows["Ferretería Dos"]["provenance"] is None


@pytest.mark.django_db
def test_malformed_provider_data_never_breaks_the_prospect_list(
    owner: User, audience: dict[str, object]
) -> None:
    taller = audience["taller"]
    assert isinstance(taller, Prospect)
    taller.provider_data = {
        "overture_id": "x",
        "snapshot": "not-a-mapping",
        "provenance": ["not", "a", "mapping"],
        "matched_rule": "nor-this",
    }
    taller.save(update_fields=("provider_data", "updated_at"))
    client = Client()
    client.force_login(owner)

    response = client.get(reverse("api-prospects"))

    assert response.status_code == 200
    row = next(item for item in response.json()["data"] if item["name"] == "Taller Uno")
    assert row["provenance"]["attribution"] == []
    assert row["provenance"]["licenses"] == []
    assert row["provenance"]["matched_rule"] is None


def _verdict(prospect: Prospect, verdict: str, reason: str, *, vetoed: bool = False) -> None:
    ProspectRelevanceVerdict.objects.create(
        prospect=prospect,
        input_hash="a" * 64,
        criteria_digest="b" * 64,
        mode="LENIENT",
        verdict=verdict,
        reason=reason,
        vetoed=vetoed,
        provider="fake",
        model="fake-deterministic",
        schema_version="prospect-screening-v1",
        status="VALID",
        evaluated_at=timezone.now(),
    )


@pytest.mark.django_db
def test_the_list_carries_the_verdict_reason_and_can_filter_by_it(
    owner: User, audience: dict[str, object]
) -> None:
    taller, ferreteria = audience["taller"], audience["ferreteria"]
    assert isinstance(taller, Prospect) and isinstance(ferreteria, Prospect)
    _verdict(taller, "FIT", "Repara motores.")
    _verdict(ferreteria, "UNFIT", "Es una tienda.")
    client = Client()
    client.force_login(owner)

    rows = {item["name"]: item for item in client.get(reverse("api-prospects")).json()["data"]}
    assert rows["Taller Uno"]["relevance_verdict"] == "FIT"
    assert rows["Taller Uno"]["relevance_reason"] == "Repara motores."
    assert rows["Ferretería Dos"]["relevance_verdict"] == "UNFIT"
    assert rows["Taller Tres"]["relevance_verdict"] is None
    assert rows["Taller Tres"]["relevance_override"] is False
    assert rows["Taller Tres"]["campaign_state"] == Campaign.State.PAUSED

    only_unfit = client.get(reverse("api-prospects"), {"verdict": "UNFIT"}).json()
    assert [item["name"] for item in only_unfit["data"]] == ["Ferretería Dos"]
    assert client.get(reverse("api-prospects"), {"verdict": "MAYBE"}).status_code == 400


@pytest.mark.django_db
def test_the_export_includes_the_verdict_and_reason(
    owner: User, audience: dict[str, object]
) -> None:
    ferreteria = audience["ferreteria"]
    assert isinstance(ferreteria, Prospect)
    _verdict(ferreteria, "UNFIT", "Es una tienda.")
    client = Client()
    client.force_login(owner)

    lines = client.get(reverse("api-prospect-export")).content.decode().splitlines()

    assert lines[0].endswith("evaluación,motivo")
    assert any("Ferretería Dos" in line and "No encaja,Es una tienda." in line for line in lines)


@pytest.mark.django_db
def test_restoring_a_removed_business_keeps_it_and_queues_the_pipeline(
    owner: User,
    audience: dict[str, object],
    django_capture_on_commit_callbacks: object,
) -> None:
    first, ferreteria = audience["first"], audience["ferreteria"]
    assert isinstance(first, Campaign) and isinstance(ferreteria, Prospect)
    Campaign.objects.filter(pk=first.pk).update(state=Campaign.State.DISCOVERING)
    Prospect.objects.filter(pk=ferreteria.pk).update(
        pipeline_state=Prospect.PipelineState.SKIPPED_IRRELEVANT
    )
    client = Client()
    client.force_login(owner)
    url = reverse("api-prospect-restore", args=(ferreteria.pk,))

    # The route must stay clear of /actions/, which has the strictest throttle.
    assert "/actions/" not in url
    with django_capture_on_commit_callbacks() as callbacks:  # type: ignore[operator]
        response = client.post(url)

    assert response.status_code == 200, response.content
    assert response.json()["data"]["relevance_override"] is True
    assert response.json()["data"]["pipeline_state"] == Prospect.PipelineState.ENRICHED
    assert len(callbacks) == 1


@pytest.mark.django_db
def test_restoring_after_sending_started_is_refused_with_a_reason(
    owner: User, audience: dict[str, object]
) -> None:
    ferreteria = audience["ferreteria"]
    assert isinstance(ferreteria, Prospect)
    Campaign.objects.filter(pk=ferreteria.campaign_id).update(state=Campaign.State.RUNNING)
    Prospect.objects.filter(pk=ferreteria.pk).update(
        pipeline_state=Prospect.PipelineState.SKIPPED_IRRELEVANT
    )
    client = Client()
    client.force_login(owner)

    response = client.post(reverse("api-prospect-restore", args=(ferreteria.pk,)))

    assert response.status_code == 400
    assert "empezó a enviar" in response.json()["detail"]


@pytest.mark.django_db
def test_restoring_an_unknown_prospect_is_not_found(
    owner: User, audience: dict[str, object]
) -> None:
    import uuid

    client = Client()
    client.force_login(owner)

    response = client.post(reverse("api-prospect-restore", args=(uuid.uuid4(),)))

    assert response.status_code == 404
