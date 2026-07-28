from __future__ import annotations

import logging
from datetime import timedelta
from io import StringIO

import pytest
from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import ActivationToken, LoginThrottle, Membership, RecoveryCode, Workspace
from apps.accounts.permissions import Capability, has_capability
from apps.accounts.services import (
    ActivationError,
    LastActiveAdminError,
    activate_with_token,
    canonical_client_ip,
    change_membership_role,
    create_managed_user,
    login_throttle_status,
    record_login_failure,
    set_user_active,
)


@pytest.mark.django_db
def test_first_user_becomes_admin_and_later_user_is_vendedor() -> None:
    first = User.objects.create_user(username="first", password="password")
    second = User.objects.create_user(username="second", password="password")

    assert Workspace.objects.count() == 1
    assert first.membership.workspace_id == second.membership.workspace_id
    assert first.membership.role == Membership.Role.ADMIN
    assert second.membership.role == Membership.Role.VENDEDOR
    assert has_capability(first, Capability.MANAGE_USERS)
    assert not has_capability(second, Capability.MANAGE_USERS)
    assert has_capability(second, Capability.VIEW_CONTACTS)


@pytest.mark.django_db
def test_fifth_failure_sets_fixed_pair_lock_and_locked_request_does_not_extend(
    client: Client,
) -> None:
    for _ in range(5):
        response = client.post(
            reverse("login"),
            {"username": " Missing ", "password": "wrong"},
            REMOTE_ADDR="127.0.0.80",
        )
        assert response.status_code == 200

    throttle = LoginThrottle.objects.get(scope=LoginThrottle.Scope.PAIR)
    locked_until = throttle.locked_until
    assert locked_until is not None

    blocked = client.post(
        reverse("login"),
        {"username": "missing", "password": "wrong"},
        REMOTE_ADDR="127.0.0.80",
    )
    assert blocked.status_code == 429
    assert 1 <= int(blocked["Retry-After"]) <= 1800

    throttle.refresh_from_db()
    assert throttle.failure_count == 5
    assert throttle.locked_until == locked_until


@pytest.mark.django_db
def test_ip_spray_lock_applies_across_normalized_usernames() -> None:
    base = timezone.now()
    for number in range(20):
        result = record_login_failure(
            username=f"candidate-{number}",
            client_ip="203.0.113.20",
            now=base + timedelta(seconds=number),
        )
    assert result.locked
    status = login_throttle_status(
        username="entirely-different",
        client_ip="203.0.113.20",
        now=base + timedelta(seconds=21),
    )
    assert status.locked
    assert LoginThrottle.objects.get(scope=LoginThrottle.Scope.IP).failure_count == 20


@pytest.mark.django_db
def test_successful_login_clears_only_the_username_ip_pair(client: Client) -> None:
    User.objects.create_user(username="owner", password="correct-password")
    for _ in range(4):
        client.post(
            reverse("login"),
            {"username": "OWNER", "password": "wrong"},
            REMOTE_ADDR="127.0.0.81",
        )
    assert LoginThrottle.objects.filter(scope=LoginThrottle.Scope.PAIR).exists()

    response = client.post(
        reverse("login"),
        {"username": "owner", "password": "correct-password"},
        REMOTE_ADDR="127.0.0.81",
    )
    assert response.status_code == 302
    assert not LoginThrottle.objects.filter(scope=LoginThrottle.Scope.PAIR).exists()
    assert LoginThrottle.objects.filter(scope=LoginThrottle.Scope.IP).exists()


def test_forwarded_address_is_used_only_from_a_trusted_proxy() -> None:
    meta: dict[str, object] = {
        "REMOTE_ADDR": "192.0.2.10",
        "HTTP_X_FORWARDED_FOR": "198.51.100.90",
    }
    assert canonical_client_ip(meta) == "192.0.2.10"
    with override_settings(TRUSTED_PROXY_IPS=["192.0.2.10"]):
        assert canonical_client_ip(meta) == "198.51.100.90"
    with override_settings(TRUSTED_PROXY_IPS=["192.0.2.0/24"]):
        assert canonical_client_ip(meta) == "198.51.100.90"


@pytest.mark.django_db
def test_activation_is_stored_hashed_expires_in_24_hours_and_is_one_use() -> None:
    admin = User.objects.create_user(username="admin", password="password")
    user, issued = create_managed_user(
        username="seller",
        email="seller@example.invalid",
        role=Membership.Role.VENDEDOR,
        actor=admin,
    )
    assert not user.is_active
    assert issued.raw_token not in issued.token.token_hash
    assert timedelta(hours=23, minutes=59) < issued.token.expires_at - issued.token.created_at
    assert issued.token.expires_at - issued.token.created_at <= timedelta(hours=24, seconds=1)

    activated = activate_with_token(raw_token=issued.raw_token, password="new-strong-password")
    assert activated.is_active
    assert activated.check_password("new-strong-password")
    with pytest.raises(ActivationError):
        activate_with_token(raw_token=issued.raw_token, password="another-password")


@pytest.mark.django_db
def test_activation_secret_is_redacted_from_request_logs(
    client: Client,
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_token = "sensitive-single-use-token"
    with caplog.at_level(logging.INFO, logger="contact_outreach.request"):
        response = client.get(reverse("account-activate", args=(raw_token,)))
    assert response.status_code == 200
    assert raw_token not in caplog.text
    paths = [getattr(record, "path", "") for record in caplog.records]
    assert "/activar/[REDACTED]/" in paths


@pytest.mark.django_db
def test_admin_page_displays_activation_link_only_on_creation_response(client: Client) -> None:
    admin = User.objects.create_user(username="admin", password="password")
    client.force_login(admin)
    response = client.post(
        reverse("account-users"),
        {"username": "new-seller", "email": "new@example.invalid", "role": "VENDEDOR"},
    )
    assert response.status_code == 200
    assert b"/activar/" in response.content
    assert b"Copi" in response.content

    refreshed = client.get(reverse("account-users"))
    assert b"/activar/" not in refreshed.content
    created = User.objects.get(username="new-seller")
    assert not created.is_active
    assert ActivationToken.objects.filter(user=created, used_at__isnull=True).count() == 1


@pytest.mark.django_db
def test_last_active_admin_cannot_be_demoted_or_deactivated() -> None:
    admin = User.objects.create_user(username="admin", password="password")
    membership = admin.membership
    with pytest.raises(LastActiveAdminError):
        change_membership_role(
            membership=membership,
            role=Membership.Role.VENDEDOR,
            actor=admin,
        )
    with pytest.raises(LastActiveAdminError):
        set_user_active(membership=membership, active=False, actor=admin)
    admin.refresh_from_db()
    membership.refresh_from_db()
    assert admin.is_active
    assert membership.role == Membership.Role.ADMIN


@pytest.mark.django_db
def test_role_change_invalidates_an_existing_session(client: Client) -> None:
    admin = User.objects.create_user(username="admin", password="password")
    second = User.objects.create_user(username="second", password="password")
    second.membership.role = Membership.Role.ADMIN
    second.membership.save(update_fields=("role", "updated_at"))
    client.force_login(admin)
    assert client.get(reverse("dashboard")).status_code == 200

    change_membership_role(
        membership=admin.membership,
        role=Membership.Role.VENDEDOR,
        actor=second,
    )
    response = client.get(reverse("dashboard"))
    assert response.status_code == 302
    assert response.url == f"{reverse('login')}?next=/"


@pytest.mark.django_db
def test_vendedor_cannot_open_user_management(client: Client) -> None:
    User.objects.create_user(username="admin", password="password")
    seller = User.objects.create_user(username="seller", password="password")
    client.force_login(seller)
    assert client.get(reverse("account-users")).status_code == 403


@pytest.mark.django_db
@override_settings(MFA_ENFORCEMENT_ENABLED=True)
def test_admin_enrolls_totp_and_can_consume_one_of_ten_recovery_codes(
    client: Client,
) -> None:
    admin = User.objects.create_user(username="admin", password="correct-password")
    password_response = client.post(
        reverse("login"),
        {"username": "admin", "password": "correct-password"},
    )
    assert password_response.status_code == 302
    assert password_response.url == reverse("mfa-enroll")

    enrollment = client.get(reverse("mfa-enroll"))
    assert enrollment.status_code == 200
    assert b"data:image/svg+xml" in enrollment.content
    assert "private" in enrollment["Cache-Control"]
    assert "no-store" in enrollment["Cache-Control"]
    device = TOTPDevice.objects.get(user=admin, confirmed=False)
    token = str(
        totp(
            device.bin_key,
            step=device.step,
            t0=device.t0,
            digits=device.digits,
            drift=device.drift,
        )
    ).zfill(device.digits)

    completed = client.post(reverse("mfa-enroll"), {"token": token})
    assert completed.status_code == 200
    recovery_codes = completed.context["recovery_codes"]
    assert len(recovery_codes) == 10
    unused_codes = RecoveryCode.objects.filter(
        membership=admin.membership,
        used_at__isnull=True,
    )
    assert unused_codes.count() == 10

    client.post(reverse("logout"))
    relogin = client.post(
        reverse("login"),
        {"username": "admin", "password": "correct-password"},
    )
    assert relogin.url == reverse("mfa-verify")
    verified = client.post(
        reverse("mfa-verify"),
        {"token": "", "recovery_code": recovery_codes[0]},
    )
    assert verified.status_code == 302
    assert verified.url == reverse("dashboard")
    assert unused_codes.count() == 9


@pytest.mark.django_db
def test_emergency_unlock_command_clears_matching_records() -> None:
    for _ in range(5):
        record_login_failure(username="locked", client_ip="203.0.113.40")
    output = StringIO()
    call_command("unlock_login", username="locked", stdout=output)
    throttle = LoginThrottle.objects.get(scope=LoginThrottle.Scope.PAIR)
    assert throttle.failure_count == 0
    assert throttle.locked_until is None
    assert "cleared: 1" in output.getvalue()


@pytest.mark.django_db
def test_unlock_command_requires_a_target() -> None:
    with pytest.raises(CommandError, match="usuario o una direcci"):
        call_command("unlock_login")
