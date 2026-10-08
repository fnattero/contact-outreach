from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, override_settings
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.configuration.models import SendModeSetting
from apps.configuration.send_mode import (
    disable_live_sending,
    enable_live_sending,
    live_sending_allowed,
    send_mode_state,
)

SERVER_LIVE = {"SEND_MODE": "live", "SEND_KILL_SWITCH": False, "SEND_REQUIRES_APP_ENABLE": True}


@pytest.mark.django_db
def test_real_sending_stays_off_while_the_server_is_in_simulation(owner: User) -> None:
    assert live_sending_allowed() is False
    assert send_mode_state().server_allows_live is False
    with pytest.raises(ValidationError, match="servidor no permite"):
        enable_live_sending(actor=owner, reauthenticated=True)
    assert not SendModeSetting.objects.filter(live_enabled=True).exists()


@pytest.mark.django_db
@override_settings(**SERVER_LIVE)
def test_the_server_alone_is_not_enough_the_admin_must_turn_it_on(owner: User) -> None:
    assert live_sending_allowed() is False
    assert send_mode_state().server_allows_live is True

    enable_live_sending(actor=owner, reauthenticated=True)

    state = send_mode_state()
    assert (state.effective_live, state.app_enabled, state.enabled_by) == (
        True,
        True,
        owner.username,
    )
    assert live_sending_allowed() is True

    disable_live_sending(actor=owner)
    assert live_sending_allowed() is False
    assert send_mode_state().enabled_by is None
    actions = list(
        AuditEvent.objects.filter(action__startswith="send_mode.").values_list("action", flat=True)
    )
    assert sorted(actions) == ["send_mode.live_enabled", "send_mode.simulation_enabled"]


@pytest.mark.django_db
@override_settings(**SERVER_LIVE)
def test_the_admins_key_alone_is_not_enough_either(owner: User) -> None:
    enable_live_sending(actor=owner, reauthenticated=True)

    with override_settings(SEND_MODE="dry-run"):
        assert live_sending_allowed() is False
    with override_settings(SEND_KILL_SWITCH=True):
        assert live_sending_allowed() is False


@pytest.mark.django_db
@override_settings(**{**SERVER_LIVE, "SEND_REQUIRES_APP_ENABLE": False})
def test_a_deployment_can_let_the_server_alone_decide() -> None:
    assert live_sending_allowed() is True


@pytest.mark.django_db
@override_settings(**SERVER_LIVE)
def test_enabling_needs_a_recent_password_and_the_right_permission(owner: User) -> None:
    with pytest.raises(PermissionDenied):
        enable_live_sending(actor=owner, reauthenticated=False)
    seller = User.objects.create_user(username="send-vendor", password="vendor-password-1234")
    with pytest.raises(PermissionDenied):
        enable_live_sending(actor=seller, reauthenticated=True)
    with pytest.raises(PermissionDenied):
        disable_live_sending(actor=seller)
    assert live_sending_allowed() is False


def _csrf(client: Client) -> str:
    return str(client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])


def _post(client: Client, url: str, payload: dict[str, object], csrf: str):
    return client.post(
        url, data=json.dumps(payload), content_type="application/json", HTTP_X_CSRFTOKEN=csrf
    )


@pytest.mark.django_db
@override_settings(**SERVER_LIVE)
def test_the_api_turns_real_sending_on_only_with_the_password_and_the_typed_word(
    owner: User,
) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf = _csrf(client)
    url = reverse("api-send-mode-action", args=("enable-live",))

    no_password = _post(client, url, {"confirmation": "CONFIRMAR"}, csrf)
    assert no_password.status_code == 403
    assert _post(client, reverse("api-auth-reauthenticate"), {"password": "correct-password"}, csrf)
    for payload in ({}, {"confirmation": "confirmar"}, {"confirmation": "SI"}):
        assert _post(client, url, payload, csrf).status_code == 400, payload
    assert client.get(reverse("api-send-mode")).json()["data"]["effective_live"] is False

    enabled = _post(client, url, {"confirmation": "CONFIRMAR"}, csrf)
    assert enabled.status_code == 200, enabled.content
    assert enabled.json()["data"]["effective_live"] is True
    summary = client.get(reverse("api-dashboard-summary")).json()["data"]["safety"]
    assert (summary["send_server_allows_live"], summary["send_effective_live"]) == (True, True)

    disabled = _post(client, reverse("api-send-mode-action", args=("disable-live",)), {}, csrf)
    assert disabled.status_code == 200
    assert disabled.json()["data"]["effective_live"] is False


@pytest.mark.django_db
def test_the_api_explains_when_the_server_does_not_allow_real_sending(owner: User) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf = _csrf(client)
    assert _post(client, reverse("api-auth-reauthenticate"), {"password": "correct-password"}, csrf)

    refused = _post(
        client,
        reverse("api-send-mode-action", args=("enable-live",)),
        {"confirmation": "CONFIRMAR"},
        csrf,
    )

    assert refused.status_code == 400
    assert "servidor no permite" in refused.json()["detail"]
