from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import LoginThrottle, Membership, Workspace
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
