from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.configuration.models import BusinessProfile, SearchCategory, SearchZone
from apps.configuration.services import save_business_profile, save_config_item


def profile_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "company_name": "Carbones del Sur",
        "salesperson_name": "Fran Pérez",
        "phone": "1234",
        "whatsapp": "5678",
        "description": "Fabricación local",
        "products": "Carbones para motores",
        "differentiators": "Stock",
        "address": "CABA",
        "website": "https://example.com",
        "signature": "Fran\nCarbones del Sur",
        "additional_instructions": "Tono directo",
        "relevance_threshold": 75,
    }
    values.update(overrides)
    return values


@pytest.mark.django_db
def test_seed_contains_documented_categories_and_caba_zones() -> None:
    assert SearchCategory.objects.filter(archived_at__isnull=True).count() == 23
    assert SearchZone.objects.filter(archived_at__isnull=True).count() == 48
    assert SearchCategory.objects.get(name="Bobinados de motores").active
    palermo = SearchZone.objects.get(name="Palermo")
    assert palermo.kind == SearchZone.Kind.NEIGHBORHOOD
    assert palermo.location_text == "Ciudad Autónoma de Buenos Aires, Argentina"


@pytest.mark.django_db
def test_profile_is_versioned_and_audited(owner: User) -> None:
    profile = save_business_profile(owner=owner, values=profile_values())
    assert profile.profile_version == 1
    profile = save_business_profile(owner=owner, values=profile_values(company_name="Nueva SA"))
    assert profile.profile_version == 2
    assert BusinessProfile.objects.get(owner=owner).company_name == "Nueva SA"
    assert AuditEvent.objects.filter(entity_type="BusinessProfile").count() == 2


@pytest.mark.django_db
def test_profile_rejects_invalid_threshold(owner: User) -> None:
    with pytest.raises(ValidationError):
        save_business_profile(owner=owner, values=profile_values(relevance_threshold=101))


@pytest.mark.django_db
def test_category_normalization_and_unique_active_name(owner: User) -> None:
    category = save_config_item(
        item=SearchCategory(name="  Reparación   Especial  ", sort_order=99), actor=owner
    )
    assert category.name == "Reparación Especial"
    assert category.normalized_name == "reparación especial"
    with pytest.raises(ValidationError):
        save_config_item(item=SearchCategory(name="REPARACIÓN ESPECIAL"), actor=owner)


@pytest.mark.django_db
def test_category_crud_views_create_toggle_and_delete(client: Client, owner: User) -> None:
    client.force_login(owner)
    created = client.post(
        reverse("categories"), {"name": "Motores navales", "active": "on", "sort_order": 80}
    )
    assert created.status_code == 302
    category = SearchCategory.objects.get(name="Motores navales")
    assert (
        client.get(reverse("config-toggle", args=("searchcategory", category.pk))).status_code
        == 405
    )
    assert (
        client.post(reverse("config-toggle", args=("searchcategory", category.pk))).status_code
        == 302
    )
    category.refresh_from_db()
    assert not category.active
    assert (
        client.post(reverse("config-delete", args=("searchcategory", category.pk))).status_code
        == 302
    )
    assert not SearchCategory.objects.filter(pk=category.pk).exists()


@pytest.mark.django_db
def test_zone_crud_view_edits_existing(client: Client, owner: User) -> None:
    client.force_login(owner)
    zone = SearchZone.objects.get(name="Palermo")
    response = client.post(
        reverse("zones"),
        {
            "item_id": zone.pk,
            "name": "Palermo Norte",
            "kind": SearchZone.Kind.CUSTOM,
            "location_text": "CABA",
            "active": "on",
            "sort_order": 2,
        },
    )
    assert response.status_code == 302
    zone.refresh_from_db()
    assert zone.name == "Palermo Norte"
    assert zone.kind == SearchZone.Kind.CUSTOM


@pytest.mark.django_db
def test_configuration_views_require_authentication(client: Client) -> None:
    for route in ("business-profile", "categories", "zones"):
        assert client.get(reverse(route)).status_code == 302
