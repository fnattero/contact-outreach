from __future__ import annotations

from datetime import time
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.campaigns.forms import CampaignForm
from apps.campaigns.models import Campaign, SearchQuery
from apps.campaigns.services import (
    InvalidCampaignTransition,
    create_campaign,
    finish_discovery,
    transition_campaign,
)
from apps.catalogs.models import Catalog
from apps.catalogs.services import create_catalog
from apps.configuration.models import SearchCategory, SearchZone
from apps.configuration.services import (
    delete_or_archive_config_item,
    save_business_profile,
)


def profile_values() -> dict[str, object]:
    return {
        "company_name": "Carbones SA",
        "salesperson_name": "Fran",
        "phone": "",
        "whatsapp": "",
        "description": "",
        "products": "Carbones",
        "differentiators": "",
        "address": "CABA",
        "website": "",
        "signature": "Fran · Carbones SA",
        "additional_instructions": "",
        "relevance_threshold": 70,
    }


def make_catalog(owner: User) -> Catalog:
    upload = SimpleUploadedFile(
        "catalogo.pdf", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF", content_type="application/pdf"
    )
    return create_catalog(name="General", upload=upload, actor=owner)


def campaign_values(catalog: Catalog, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "name": "CABA talleres",
        "delivery_mode": Campaign.DeliveryMode.DRY_RUN,
        "location_text": "Ciudad Autónoma de Buenos Aires, Argentina",
        "objective": 300,
        "max_raw_records": 3000,
        "cost_limit": Decimal("10.00"),
        "cost_currency": "USD",
        "daily_limit": 30,
        "message_interval_minutes": 5,
        "weekdays": [0, 1, 2, 3, 4],
        "window_start": time(9, 0),
        "window_end": time(17, 0),
        "timezone_name": "America/Argentina/Buenos_Aires",
        "relevance_threshold": 70,
        "extractor_provider": "fake",
        "llm_provider": "fake",
        "llm_base_url": "",
        "llm_model": "fake-deterministic",
        "catalog": catalog,
    }
    values.update(overrides)
    return values


def make_campaign(owner: User, catalog: Catalog, **overrides: object) -> Campaign:
    category = SearchCategory.objects.get(name="Bobinados de motores")
    zone = SearchZone.objects.get(name="Palermo")
    return create_campaign(
        actor=owner,
        values=campaign_values(catalog, **overrides),
        category_ids=[category.pk],
        zone_ids=[zone.pk],
    )


@pytest.mark.django_db
def test_campaign_form_defaults_and_validates_limits() -> None:
    form = CampaignForm()
    assert form.fields["objective"].initial == 300
    campaign = Campaign(
        objective=400,
        max_raw_records=300,
        window_start=time(18),
        window_end=time(9),
        weekdays=[],
        timezone_name="Invalid/Zone",
    )
    with pytest.raises(ValidationError) as error:
        campaign.clean()
    assert {"max_raw_records", "window_end", "weekdays", "timezone_name"} <= set(
        error.value.message_dict
    )


@pytest.mark.django_db
def test_create_campaign_selects_snapshots_and_custom_objective(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    catalog = make_catalog(owner)
    campaign = make_campaign(owner, catalog, objective=425)
    assert campaign.state == Campaign.State.DRAFT
    assert campaign.objective == 425
    assert campaign.category_selections.get().name_snapshot == "Bobinados de motores"
    assert campaign.zone_selections.get().name_snapshot == "Palermo"
    assert AuditEvent.objects.filter(action="campaign.created").exists()


@pytest.mark.django_db
def test_outscraper_campaign_rejects_non_usd_costs(owner: User, private_catalog_dir: Path) -> None:
    del private_catalog_dir
    catalog = make_catalog(owner)
    with pytest.raises(ValidationError) as error:
        make_campaign(
            owner,
            catalog,
            extractor_provider="outscraper",
            cost_currency="EUR",
        )
    assert "cost_currency" in error.value.message_dict


@pytest.mark.django_db
def test_campaign_requires_active_selections(owner: User, private_catalog_dir: Path) -> None:
    del private_catalog_dir
    catalog = make_catalog(owner)
    with pytest.raises(ValidationError, match="rubro y una zona"):
        create_campaign(
            actor=owner,
            values=campaign_values(catalog),
            category_ids=[],
            zone_ids=[],
        )


@pytest.mark.django_db
def test_start_freezes_profile_settings_and_deterministic_queries(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    save_business_profile(owner=owner, values=profile_values())
    catalog = make_catalog(owner)
    campaign = make_campaign(owner, catalog)
    category = campaign.category_selections.get().category
    category.name = "Nombre modificado"
    category.save()

    started = transition_campaign(
        campaign_id=campaign.pk, target_state=Campaign.State.RUNNING, actor=owner
    )
    assert started.state == Campaign.State.RUNNING
    assert started.discovery_state == Campaign.DiscoveryState.RUNNING
    assert started.settings_snapshot["objective"] == 300
    assert started.profile_snapshot["company_name"] == "Carbones SA"
    query = SearchQuery.objects.get(campaign=started)
    assert query.category_snapshot == "Bobinados de motores"
    assert "Bobinados de motores en Palermo" in query.query_text
    started.objective = 999
    with pytest.raises(ValidationError, match="inmutable"):
        started.save()


@pytest.mark.django_db
def test_campaign_pause_resume_discovery_finish_and_complete(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    save_business_profile(owner=owner, values=profile_values())
    campaign = make_campaign(owner, make_catalog(owner))
    transition_campaign(campaign_id=campaign.pk, target_state=Campaign.State.RUNNING, actor=owner)
    paused = transition_campaign(
        campaign_id=campaign.pk, target_state=Campaign.State.PAUSED, actor=owner, reason="Revisión"
    )
    assert paused.status_reason == "Revisión"
    resumed = transition_campaign(
        campaign_id=campaign.pk, target_state=Campaign.State.RUNNING, actor=owner
    )
    assert resumed.state == Campaign.State.RUNNING
    finished = finish_discovery(
        campaign_id=campaign.pk,
        target_state=Campaign.DiscoveryState.EXHAUSTED_RAW_LIMIT,
        reason="max_raw_records=3000",
        actor=None,
    )
    assert finished.discovery_stop_reason == "max_raw_records=3000"
    completed = transition_campaign(
        campaign_id=campaign.pk, target_state=Campaign.State.COMPLETED, actor=None
    )
    assert completed.finished_at is not None


@pytest.mark.django_db
def test_invalid_campaign_and_discovery_transitions_are_rejected(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = make_campaign(owner, make_catalog(owner))
    with pytest.raises(InvalidCampaignTransition, match="Transición inválida"):
        transition_campaign(
            campaign_id=campaign.pk, target_state=Campaign.State.PAUSED, actor=owner
        )
    with pytest.raises(InvalidCampaignTransition, match="descubrimiento"):
        finish_discovery(
            campaign_id=campaign.pk,
            target_state=Campaign.DiscoveryState.TARGET_REACHED,
            reason="objetivo",
            actor=owner,
        )
    cancelled = transition_campaign(
        campaign_id=campaign.pk, target_state=Campaign.State.CANCELLED, actor=owner
    )
    assert cancelled.state == Campaign.State.CANCELLED
    with pytest.raises(InvalidCampaignTransition):
        transition_campaign(
            campaign_id=campaign.pk, target_state=Campaign.State.RUNNING, actor=owner
        )


@pytest.mark.django_db
def test_start_rejects_missing_profile_and_live_safety_barriers(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = make_campaign(owner, make_catalog(owner))
    with pytest.raises(ValidationError, match="perfil comercial"):
        transition_campaign(
            campaign_id=campaign.pk, target_state=Campaign.State.RUNNING, actor=owner
        )
    save_business_profile(owner=owner, values=profile_values())
    campaign.delivery_mode = Campaign.DeliveryMode.LIVE
    campaign.save(update_fields=("delivery_mode", "updated_at"))
    with override_settings(SEND_MODE="live", SEND_KILL_SWITCH=True):
        with pytest.raises(ValidationError, match="bloqueado"):
            transition_campaign(
                campaign_id=campaign.pk, target_state=Campaign.State.RUNNING, actor=owner
            )
    with override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False):
        with pytest.raises(ValidationError, match="conexión Gmail"):
            transition_campaign(
                campaign_id=campaign.pk, target_state=Campaign.State.RUNNING, actor=owner
            )
    campaign.refresh_from_db()
    assert campaign.state == Campaign.State.DRAFT


@pytest.mark.django_db
def test_start_rejects_tampered_catalog(owner: User, private_catalog_dir: Path) -> None:
    del private_catalog_dir
    save_business_profile(owner=owner, values=profile_values())
    campaign = make_campaign(owner, make_catalog(owner))
    with open(campaign.catalog.file.path, "ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(ValidationError, match="integridad"):
        transition_campaign(
            campaign_id=campaign.pk, target_state=Campaign.State.RUNNING, actor=owner
        )


@pytest.mark.django_db
def test_referenced_category_is_archived_instead_of_deleted(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = make_campaign(owner, make_catalog(owner))
    category = campaign.category_selections.get().category
    result = delete_or_archive_config_item(model=SearchCategory, item_id=category.pk, actor=owner)
    category.refresh_from_db()
    assert result == "archived"
    assert category.archived_at is not None
    assert not category.active


@pytest.mark.django_db
@pytest.mark.e2e
def test_campaign_creation_and_actions_through_dashboard(
    client: Client, owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    save_business_profile(owner=owner, values=profile_values())
    catalog = make_catalog(owner)
    category = SearchCategory.objects.get(name="Bobinados de motores")
    zone = SearchZone.objects.get(name="Palermo")
    client.force_login(owner)
    response = client.post(
        reverse("campaign-create"),
        {
            "name": "Dashboard 450",
            "delivery_mode": Campaign.DeliveryMode.DRY_RUN,
            "location_text": "CABA",
            "objective": 450,
            "max_raw_records": 4000,
            "cost_limit": "12.50",
            "cost_currency": "USD",
            "daily_limit": 25,
            "message_interval_minutes": 7,
            "weekdays": [0, 1, 2, 3, 4],
            "window_start": "09:00",
            "window_end": "17:00",
            "timezone_name": "America/Argentina/Buenos_Aires",
            "relevance_threshold": 80,
            "extractor_provider": "fake",
            "llm_provider": "fake",
            "llm_base_url": "",
            "llm_model": "fake-deterministic",
            "catalog": catalog.pk,
            "categories": [category.pk],
            "zones": [zone.pk],
        },
    )
    assert response.status_code == 302
    campaign = Campaign.objects.get(name="Dashboard 450")
    assert campaign.objective == 450
    detail = client.get(reverse("campaign-detail", args=(campaign.pk,)))
    assert detail.status_code == 200
    assert b"Dashboard 450" in detail.content
    started = client.post(reverse("campaign-action", args=(campaign.pk, "start")))
    assert started.status_code == 302
    campaign.refresh_from_db()
    assert campaign.state == Campaign.State.RUNNING
    assert campaign.search_queries.count() == 1


@pytest.mark.django_db
def test_campaign_views_require_login_and_mutations_require_csrf(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = make_campaign(owner, make_catalog(owner))
    anonymous = Client()
    assert anonymous.get(reverse("campaigns")).status_code == 302
    logged_in = Client()
    logged_in.force_login(owner)
    assert (
        logged_in.get(reverse("campaign-action", args=(campaign.pk, "cancel"))).status_code == 405
    )
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(owner)
    assert (
        csrf_client.post(reverse("campaign-action", args=(campaign.pk, "cancel"))).status_code
        == 403
    )
