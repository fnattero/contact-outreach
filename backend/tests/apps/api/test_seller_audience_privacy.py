"""A seller never sees the audience: prospect businesses and their email addresses (PRODUCT_SPEC)."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from tests.apps.api.test_seller_matrix import seller_world  # noqa: F401  (pytest fixture)

from apps.accounts.models import Workspace
from apps.campaigns.models import Campaign
from apps.contacts.models import CampaignEnrollment, EmailAddress, Organization

SECRET_EMAIL = "gerencia@audiencia-privada.example"
SECRET_BUSINESS = "Taller Audiencia Privada"


@pytest.fixture
def enrolled(seller_world: dict[str, object]) -> dict[str, object]:  # noqa: F811
    campaign = seller_world["campaign"]
    assert isinstance(campaign, Campaign)
    workspace = Workspace.objects.get()
    organization = Organization.objects.create(
        workspace=workspace, name=SECRET_BUSINESS, normalized_name=SECRET_BUSINESS.casefold()
    )
    email = EmailAddress.objects.create(
        workspace=workspace,
        organization=organization,
        original_email=SECRET_EMAIL,
        normalized_email=SECRET_EMAIL,
        domain="audiencia-privada.example",
        validity=EmailAddress.Validity.VALID,
        is_preferred=True,
    )
    CampaignEnrollment.objects.create(
        workspace=workspace,
        campaign=campaign,
        organization=organization,
        selected_email=email,
        state=CampaignEnrollment.State.ELIGIBLE,
        exclusion_reason="Motivo interno",
    )
    return seller_world


@pytest.mark.django_db
def test_a_seller_cannot_list_the_audience_of_a_visible_campaign(
    enrolled: dict[str, object], private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    client, campaign = enrolled["client"], enrolled["campaign"]
    assert isinstance(client, Client) and isinstance(campaign, Campaign)

    response = client.get(reverse("api-campaign-enrollments", args=(campaign.pk,)))

    assert response.status_code == 403
    assert SECRET_EMAIL not in response.content.decode()
    assert SECRET_BUSINESS not in response.content.decode()


@pytest.mark.django_db
def test_an_admin_still_sees_the_audience(
    enrolled: dict[str, object], private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = enrolled["campaign"]
    assert isinstance(campaign, Campaign)
    admin = Client()
    admin.force_login(User.objects.get(username="admin"))

    rows = admin.get(reverse("api-campaign-enrollments", args=(campaign.pk,))).json()["data"]

    assert [(row["organization_name"], row["selected_email"]) for row in rows] == [
        (SECRET_BUSINESS, SECRET_EMAIL)
    ]
    assert rows[0]["exclusion_reason"] == "Motivo interno"
