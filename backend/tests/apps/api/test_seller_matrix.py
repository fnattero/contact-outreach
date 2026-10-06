from __future__ import annotations

import uuid
from pathlib import Path
from uuid import UUID

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from tests.apps.accounts.test_workspace_authorization import _campaign, _catalog, _thread

from apps.campaigns.models import Campaign, OutboundMessage

ABSENT = UUID(int=0)


@pytest.fixture
def seller_world(private_catalog_dir: Path) -> dict[str, object]:
    del private_catalog_dir
    admin = User.objects.create_user(username="admin", password="password-for-tests-1")
    seller = User.objects.create_user(username="seller", password="password-for-tests-2")
    catalog = _catalog(admin)
    draft = _campaign(admin, catalog, name="Borrador secreto", state=Campaign.State.DRAFT)
    campaign = _campaign(admin, catalog, name="Campaña visible", state=Campaign.State.PAUSED)
    sent, inbound = _thread(admin, campaign, catalog)
    unsent, _ = _thread(
        admin,
        campaign,
        catalog,
        message_state=OutboundMessage.State.SEND_FAILED,
        suffix="unsent",
    )
    assert inbound is not None
    client = Client(enforce_csrf_checks=True)
    client.force_login(seller)
    csrf = str(client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])
    return {
        "client": client,
        "csrf": csrf,
        "catalog": catalog,
        "draft": draft,
        "campaign": campaign,
        "sent": sent,
        "unsent": unsent,
        "inbound": inbound,
    }


def _send(world: dict[str, object], method: str, url: str):
    client = world["client"]
    assert isinstance(client, Client)
    return getattr(client, method)(
        url,
        data="{}",
        content_type="application/json",
        HTTP_X_CSRFTOKEN=str(world["csrf"]),
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )


@pytest.mark.django_db
def test_seller_can_read_operations_but_only_what_is_meant_for_sellers(
    seller_world: dict[str, object],
) -> None:
    client = seller_world["client"]
    assert isinstance(client, Client)
    campaign, draft = seller_world["campaign"], seller_world["draft"]
    sent, unsent, inbound = seller_world["sent"], seller_world["unsent"], seller_world["inbound"]
    assert isinstance(campaign, Campaign) and isinstance(draft, Campaign)
    assert isinstance(sent, OutboundMessage) and isinstance(unsent, OutboundMessage)

    readable = (
        reverse("api-dashboard-summary"),
        reverse("api-campaigns"),
        reverse("api-campaign-detail", args=(campaign.pk,)),
        reverse("api-outbound-messages"),
        reverse("api-outbound-message-detail", args=(sent.pk,)),
        reverse("api-inbound-messages"),
        reverse("api-inbound-message-thread", args=(inbound.pk,)),  # type: ignore[attr-defined]
    )
    for url in readable:
        assert client.get(url).status_code == 200, url

    campaigns = client.get(reverse("api-campaigns")).json()["data"]
    assert [item["name"] for item in campaigns] == ["Campaña visible"]
    # The seller's view of a campaign carries no audience, frozen settings or admin metrics.
    detail = client.get(reverse("api-campaign-detail", args=(campaign.pk,))).json()["data"]
    assert "metrics" not in detail and "audience_hash" not in detail
    outbound = {row["id"] for row in client.get(reverse("api-outbound-messages")).json()["data"]}
    assert outbound == {str(sent.pk)}
    sent_detail = client.get(reverse("api-outbound-message-detail", args=(sent.pk,))).json()["data"]
    assert "Contenido sent" in sent_detail["body_text"]
    assert "attachments" not in sent_detail and "message_id" not in sent_detail

    # Drafts and unsent messages do not exist as far as a seller can tell.
    assert client.get(reverse("api-campaign-detail", args=(draft.pk,))).status_code != 200
    assert client.get(reverse("api-outbound-message-detail", args=(unsent.pk,))).status_code != 200
    # Everything readable is read-only.
    for url in readable:
        assert _send(seller_world, "post", url).status_code in {405, 403}, url


@pytest.mark.django_db
def test_seller_is_refused_every_administrative_read(seller_world: dict[str, object]) -> None:
    client = seller_world["client"]
    assert isinstance(client, Client)
    catalog = seller_world["catalog"]
    forbidden_gets = (
        reverse("api-users"),
        reverse("api-prospects"),
        reverse("api-prospect-export"),
        reverse("api-outbound-message-export"),
        reverse("api-inbound-message-export"),
        reverse("api-workspace-profile"),
        reverse("api-relevance-filter"),
        reverse("api-integrations-status"),
        reverse("api-overture-status"),
        reverse("api-search-categories"),
        reverse("api-catalogs"),
        reverse("api-catalog-download", args=(catalog.pk,)),  # type: ignore[attr-defined]
        reverse("api-suppressions"),
        reverse("api-audit-events"),
        reverse("api-background-jobs"),
        reverse("api-gmail-connection"),
        reverse("api-health-degraded"),
        reverse("api-automation-writing-instructions"),
    )
    for url in forbidden_gets:
        assert client.get(url).status_code == 403, url


@pytest.mark.django_db
def test_seller_is_refused_every_effect_bearing_action(seller_world: dict[str, object]) -> None:
    campaign, unsent, inbound = (
        seller_world["campaign"],
        seller_world["unsent"],
        seller_world["inbound"],
    )
    assert isinstance(campaign, Campaign) and isinstance(unsent, OutboundMessage)
    forbidden = (
        ("post", reverse("api-campaigns")),
        ("post", reverse("api-campaign-action", args=(campaign.pk, "cancel"))),
        ("patch", reverse("api-outbound-message-draft", args=(unsent.pk,))),
        ("post", reverse("api-outbound-message-authorize", args=(unsent.pk,))),
        ("post", reverse("api-inbound-message-manual-reply", args=(inbound.pk,))),  # type: ignore[attr-defined]
        ("post", reverse("api-gmail-oauth-start")),
        ("post", reverse("api-gmail-test")),
        ("post", reverse("api-gmail-disconnect")),
        ("post", reverse("api-overture-sync")),
        ("post", reverse("api-search-category-toggle", args=(ABSENT,))),
        ("delete", reverse("api-search-category-detail", args=(ABSENT,))),
        ("post", reverse("api-background-job-retry", args=(ABSENT,))),
        ("post", reverse("api-users-unlock-login")),
        ("post", reverse("api-suppressions")),
        ("post", reverse("api-prospect-restore", args=(ABSENT,))),
    )
    for method, url in forbidden:
        assert _send(seller_world, method, url).status_code == 403, f"{method.upper()} {url}"
    campaign.refresh_from_db()
    assert campaign.state == Campaign.State.PAUSED
