from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.automation.models import ReplyAutomationConfiguration
from apps.configuration.models import SearchCategory


def _csrf(client: Client) -> str:
    return str(client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])


def _patch(client: Client, url: str, payload: dict[str, object], csrf: str):
    return client.patch(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )


@pytest.mark.django_db
def test_configuration_api_exposes_safe_templates_and_persists_prompt_revision(owner: User) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf = _csrf(client)

    templates = client.get(reverse("api-message-template-revisions"))
    assert templates.status_code == 200
    assert {item["kind"] for item in templates.json()["data"]} == {
        "INITIAL",
        "REMINDER",
        "REFERRED_PROPOSAL",
    }

    prompts = client.get(reverse("api-prompts"))
    assert prompts.status_code == 200
    changed = _patch(
        client,
        reverse("api-prompts"),
        {"email_drafting_prompt": "Usá un tono breve y prudente."},
        csrf,
    )
    assert changed.status_code == 200
    assert changed.json()["data"]["email_drafting_prompt"] == "Usá un tono breve y prudente."
    assert changed.json()["data"]["revision"] == prompts.json()["data"]["revision"] + 1


@pytest.mark.django_db
def test_automation_api_defaults_to_shadow_and_live_needs_reauthentication(owner: User) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf = _csrf(client)
    url = reverse("api-automation-configuration")

    current = client.get(url)
    assert current.status_code == 200
    assert current.json()["data"]["mode"] == ReplyAutomationConfiguration.Mode.SHADOW

    enabled = client.post(
        reverse("api-automation-action", args=("enable-live",)),
        data="{}",
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert enabled.status_code == 403
    assert enabled.json()["code"] == "permission_denied"
    assert "ingresar" in enabled.json()["detail"].lower()

    disabled = _patch(client, url, {"mode": "OFF"}, csrf)
    assert disabled.status_code == 200
    assert disabled.json()["data"]["mode"] == "OFF"


def _post(client: Client, url: str, payload: dict[str, object], csrf: str):
    return client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )


@pytest.mark.django_db
def test_category_creation_goes_through_the_service_and_is_audited(owner: User) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf = _csrf(client)

    created = _post(
        client, reverse("api-search-categories"), {"name": "  Rubro   de prueba  "}, csrf
    )

    assert created.status_code == 201, created.content
    category = SearchCategory.objects.get(pk=created.json()["data"]["id"])
    assert category.name == "Rubro de prueba"
    assert category.normalized_name == "rubro de prueba"
    event = AuditEvent.objects.get(action="searchcategory.saved", entity_id=str(category.pk))
    assert event.actor == owner

    duplicate = _post(client, reverse("api-search-categories"), {"name": "rubro DE prueba"}, csrf)
    assert duplicate.status_code == 400
    assert duplicate.json()["code"] == "validation_error"


@pytest.mark.django_db
def test_category_creation_is_admin_only(owner: User) -> None:
    vendor = User.objects.create_user(username="category-vendor", password="vendor-password-1234")
    client = Client(enforce_csrf_checks=True)
    client.force_login(vendor)
    csrf = _csrf(client)

    denied = _post(client, reverse("api-search-categories"), {"name": "Intento"}, csrf)

    assert denied.status_code == 403
    assert not SearchCategory.objects.filter(name="Intento").exists()


@pytest.mark.django_db
def test_category_rules_are_capped_at_twenty_and_model_messages_reach_the_client(
    owner: User,
) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf = _csrf(client)
    category = SearchCategory.objects.get(name="Bobinados de motores")
    url = reverse("api-search-category-rules", args=(category.pk,))

    too_many = _post(
        client, url, {"rules": [{"name_terms": [f"termino {n}"]} for n in range(21)]}, csrf
    )
    assert too_many.status_code == 400
    assert "rules" in too_many.json()["field_errors"]

    invalid = _post(client, url, {"rules": [{"name_terms": ["con|barra"]}]}, csrf)
    assert invalid.status_code == 400
    assert "name_terms" in invalid.json()["field_errors"]
    assert "frases literales" in str(invalid.json()["field_errors"]["name_terms"])

    valid = _post(client, url, {"rules": [{"name_terms": ["bobinado de motores"]}]}, csrf)
    assert valid.status_code == 200, valid.content


def _category(name: str = "Rubro temporal") -> SearchCategory:
    return SearchCategory.objects.create(
        workspace=SearchCategory.objects.get(name="Bobinados de motores").workspace,
        name=name,
    )


@pytest.mark.django_db
def test_toggling_a_category_hides_it_from_selection_but_not_from_management(owner: User) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf = _csrf(client)
    category = _category()
    toggle = reverse("api-search-category-toggle", args=(category.pk,))
    listing = reverse("api-search-categories")

    off = _post(client, toggle, {}, csrf)
    assert off.status_code == 200, off.content
    assert off.json()["data"]["active"] is False

    selectable = {item["id"] for item in client.get(listing).json()["data"]}
    managed = {
        item["id"]: item["active"]
        for item in client.get(listing, {"include_inactive": "true"}).json()["data"]
    }
    assert str(category.pk) not in selectable
    assert managed[str(category.pk)] is False

    on = _post(client, toggle, {}, csrf)
    assert on.json()["data"]["active"] is True
    assert str(category.pk) in {item["id"] for item in client.get(listing).json()["data"]}
    assert (
        AuditEvent.objects.filter(
            action="searchcategory.toggled", entity_id=str(category.pk)
        ).count()
        == 2
    )


@pytest.mark.django_db
def test_deleting_an_unused_category_removes_it_and_is_audited(owner: User) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf = _csrf(client)
    category = _category()

    response = client.delete(
        reverse("api-search-category-detail", args=(category.pk,)), HTTP_X_CSRFTOKEN=csrf
    )

    assert response.status_code == 200, response.content
    assert response.json()["data"] == {"outcome": "deleted"}
    assert not SearchCategory.objects.filter(pk=category.pk).exists()
    assert AuditEvent.objects.filter(action="searchcategory.deleted", actor=owner).exists()


@pytest.mark.django_db
def test_category_management_rejects_unknown_ids_vendedor_and_anonymous(owner: User) -> None:
    category = _category()
    detail = reverse("api-search-category-detail", args=(category.pk,))
    toggle = reverse("api-search-category-toggle", args=(category.pk,))
    missing = reverse("api-search-category-toggle", args=("00000000-0000-4000-8000-000000000009",))

    assert Client().post(toggle).status_code in {401, 403}
    vendor = User.objects.create_user(username="category-mgr-vendor", password="vendor-password-1")
    seller = Client(enforce_csrf_checks=True)
    seller.force_login(vendor)
    seller_csrf = _csrf(seller)
    assert _post(seller, toggle, {}, seller_csrf).status_code == 403
    assert seller.delete(detail, HTTP_X_CSRFTOKEN=seller_csrf).status_code == 403
    category.refresh_from_db()
    assert category.active is True

    admin = Client(enforce_csrf_checks=True)
    admin.force_login(owner)
    assert _post(admin, missing, {}, _csrf(admin)).status_code == 404
