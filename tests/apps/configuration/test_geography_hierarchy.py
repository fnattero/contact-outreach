from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.configuration.models import SearchZone


@pytest.mark.django_db
def test_official_geography_keeps_duplicate_district_names_scoped_to_province() -> None:
    duplicates = SearchZone.objects.filter(
        level=SearchZone.Level.DISTRICT,
        name="General Belgrano",
    ).select_related("parent")

    assert duplicates.count() >= 2
    assert len({zone.parent_id for zone in duplicates}) == duplicates.count()
    assert len({zone.province_code for zone in duplicates}) == duplicates.count()


@pytest.mark.django_db
def test_caba_communes_are_reference_only_and_barrios_remain_selectable() -> None:
    caba = SearchZone.objects.get(level=SearchZone.Level.PROVINCE, official_code="02")

    assert caba.label_plural == "Barrios"
    assert caba.children.filter(level=SearchZone.Level.DISTRICT).count() == 15
    assert not caba.children.filter(level=SearchZone.Level.DISTRICT, selectable=True).exists()
    assert (
        caba.children.filter(
            level=SearchZone.Level.NEIGHBORHOOD,
            selectable=True,
        ).count()
        == 48
    )


@pytest.mark.django_db
def test_district_hierarchy_rejects_a_missing_province(owner: User) -> None:
    workspace = owner.membership.workspace
    district = SearchZone(
        workspace=workspace,
        official_code="fixture-district",
        name="Distrito sin provincia",
        normalized_name="distrito sin provincia",
        level=SearchZone.Level.DISTRICT,
        source=SearchZone.Source.CUSTOM,
        selectable=True,
        location_text="Argentina",
        boundary_geojson={
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
    )

    with pytest.raises(ValidationError) as error:
        district.full_clean()

    assert "parent" in error.value.message_dict


@pytest.mark.django_db
def test_campaign_form_groups_searchable_districts_by_province(
    client: Client,
    owner: User,
) -> None:
    client.force_login(owner)

    response = client.get(reverse("campaign-create"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Primero elegí una o más provincias" in content
    assert "Ciudad Autónoma de Buenos Aires" in content
    assert "Mendoza" in content
    assert 'type="search"' in content
    assert "Seleccionar todos" in content
    assert "Barrios" in content
    assert "Departamentos" in content
