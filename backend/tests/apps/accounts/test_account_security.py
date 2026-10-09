"""Lockout, client address trust, administrator safeguards and activation links (SECURITY.md 2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, override_settings
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import ActivationToken, LoginThrottle, Membership
from apps.accounts.services import (
    ACTIVATION_LIFETIME,
    IP_FAILURE_LIMIT,
    LOGIN_FAILURE_WINDOW,
    LOGIN_LOCK_DURATION,
    PAIR_FAILURE_LIMIT,
    ActivationError,
    LastActiveAdminError,
    OwnerConflictError,
    activate_with_token,
    activation_for_token,
    canonical_client_ip,
    change_membership_role,
    clear_login_pair,
    create_managed_user,
    ensure_owner,
    issue_activation_token,
    login_throttle_status,
    normalize_login_username,
    record_login_failure,
    set_user_active,
    unlock_login,
)
from apps.audit.models import AuditEvent

PASSWORD = "a-long-enough-password-1"
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
IP = "203.0.113.7"


@pytest.fixture
def admin(db: None) -> User:
    return User.objects.create_user(username="admin", password=PASSWORD)


@pytest.fixture
def seller(admin: User) -> User:
    return User.objects.create_user(username="seller", password=PASSWORD)


def _fail(username: str, ip: str = IP, *, at: datetime = T0, times: int = 1):
    status = None
    for _ in range(times):
        status = record_login_failure(username=username, client_ip=ip, now=at)
    assert status is not None
    return status


# --- who is the client? -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        ({"CONTACT_OUTREACH_CLIENT_IP": "203.0.113.9", "REMOTE_ADDR": "10.0.0.2"}, "203.0.113.9"),
        (
            {"CONTACT_OUTREACH_CLIENT_IP": "2001:DB8:0:0::1", "REMOTE_ADDR": "10.0.0.2"},
            "2001:db8::1",
        ),
        ({"CONTACT_OUTREACH_CLIENT_IP": "garbage", "REMOTE_ADDR": "198.51.100.4"}, "198.51.100.4"),
        ({"REMOTE_ADDR": "198.51.100.4"}, "198.51.100.4"),
        ({"REMOTE_ADDR": "garbage"}, "unknown"),
        ({}, "unknown"),
    ],
)
@override_settings(TRUSTED_PROXY_IPS=())
def test_client_address_comes_from_the_connection_unless_the_frontend_proxy_vouched(
    meta: dict[str, str], expected: str
) -> None:
    assert canonical_client_ip(meta) == expected


@override_settings(TRUSTED_PROXY_IPS=())
def test_an_untrusted_peer_cannot_choose_its_address_through_forwarding_headers() -> None:
    meta = {"REMOTE_ADDR": "198.51.100.4", "HTTP_X_FORWARDED_FOR": "6.6.6.6"}

    assert canonical_client_ip(meta) == "198.51.100.4"


@pytest.mark.parametrize(
    ("forwarded", "expected"),
    [
        ("203.0.113.9", "203.0.113.9"),
        ("6.6.6.6, 203.0.113.9", "203.0.113.9"),  # the hop closest to our proxy wins, not the first
        ("203.0.113.9, 10.0.0.9", "203.0.113.9"),  # trusted hops on the right are skipped
        ("10.0.0.8, 10.0.0.9", "10.0.0.2"),  # nothing but proxies: fall back to the peer
        ("", "10.0.0.2"),
        ("203.0.113.9, not-an-ip", "10.0.0.2"),  # one bad entry discards the whole header
        (" , 203.0.113.9", "203.0.113.9"),
    ],
)
@override_settings(TRUSTED_PROXY_IPS=("10.0.0.0/8", "not-a-network"))
def test_forwarding_headers_are_read_from_the_right_through_trusted_proxies_only(
    forwarded: str, expected: str
) -> None:
    meta = {"REMOTE_ADDR": "10.0.0.2", "HTTP_X_FORWARDED_FOR": forwarded}

    assert canonical_client_ip(meta) == expected


@pytest.mark.parametrize("variant", ["Ana", " ana ", "ANA", "aNa"])
def test_usernames_are_compared_case_and_space_insensitively(variant: str) -> None:
    assert normalize_login_username(variant) == "ana"


# --- lockout -----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_fifth_failure_for_one_user_and_address_locks_them_for_thirty_minutes() -> None:
    for attempt in range(1, PAIR_FAILURE_LIMIT):
        assert _fail("ana", times=1).locked is False, attempt

    locked = _fail("ana")

    assert locked.locked is True
    assert locked.retry_after == int(LOGIN_LOCK_DURATION.total_seconds())
    assert login_throttle_status(username="ana", client_ip=IP, now=T0).locked is True
    just_before = T0 + LOGIN_LOCK_DURATION - timedelta(seconds=1)
    assert login_throttle_status(username="ana", client_ip=IP, now=just_before).retry_after == 1
    assert (
        login_throttle_status(username="ana", client_ip=IP, now=T0 + LOGIN_LOCK_DURATION).locked
        is False
    )


@pytest.mark.django_db
def test_a_lock_is_specific_to_the_user_and_address_pair() -> None:
    _fail("ana", times=PAIR_FAILURE_LIMIT)

    assert login_throttle_status(username="ana", client_ip=IP, now=T0).locked is True
    assert login_throttle_status(username="ana", client_ip="198.51.100.1", now=T0).locked is False
    assert login_throttle_status(username="beto", client_ip=IP, now=T0).locked is False
    assert login_throttle_status(username=" ANA ", client_ip=IP, now=T0).locked is True


@pytest.mark.django_db
def test_twenty_failures_from_one_address_lock_the_whole_address_even_across_usernames() -> None:
    for number in range(IP_FAILURE_LIMIT):
        status = _fail(f"user-{number}")

    assert status.locked is True
    assert login_throttle_status(username="never-tried", client_ip=IP, now=T0).locked is True
    assert (
        login_throttle_status(username="never-tried", client_ip="198.51.100.1", now=T0).locked
        is False
    )


@pytest.mark.django_db
def test_failing_while_locked_does_not_extend_the_lock() -> None:
    _fail("ana", times=PAIR_FAILURE_LIMIT)
    original = LoginThrottle.objects.get(scope=LoginThrottle.Scope.PAIR).locked_until

    _fail("ana", at=T0 + timedelta(minutes=10), times=3)

    assert LoginThrottle.objects.get(scope=LoginThrottle.Scope.PAIR).locked_until == original


@pytest.mark.django_db
def test_old_failures_expire_after_the_window() -> None:
    _fail("ana", times=PAIR_FAILURE_LIMIT - 1)

    later = T0 + LOGIN_FAILURE_WINDOW + timedelta(seconds=1)
    status = _fail("ana", at=later)

    assert status.locked is False
    assert LoginThrottle.objects.get(scope=LoginThrottle.Scope.PAIR).failure_count == 1


@pytest.mark.django_db
def test_a_successful_login_clears_only_that_pair() -> None:
    _fail("ana", times=PAIR_FAILURE_LIMIT)
    _fail("beto", times=PAIR_FAILURE_LIMIT)

    assert clear_login_pair(username="ana", client_ip=IP) == 1

    assert login_throttle_status(username="ana", client_ip=IP, now=T0).locked is False
    assert login_throttle_status(username="beto", client_ip=IP, now=T0).locked is True
    # The address-wide counter is not reset by one user's success, so spraying still adds up.
    ip_row = LoginThrottle.objects.get(scope=LoginThrottle.Scope.IP)
    assert ip_row.failure_count == 2 * PAIR_FAILURE_LIMIT
    assert clear_login_pair(username="ana", client_ip=IP) == 0


@pytest.mark.django_db
def test_throttle_rows_hold_only_digests_never_the_username_or_address() -> None:
    _fail("ana.garcia", ip="203.0.113.77")

    for row in LoginThrottle.objects.all():
        blob = " ".join(str(value) for value in (row.key_hash, row.username_hash, row.ip_hash))
        assert "ana" not in blob and "203.0.113" not in blob
        assert len(row.key_hash) == 64


# --- unlocking ---------------------------------------------------------------------------------


@pytest.mark.django_db
def test_unlock_needs_something_to_unlock_and_a_valid_address() -> None:
    with pytest.raises(ValidationError, match="usuario o una dirección"):
        unlock_login()
    with pytest.raises(ValidationError, match="no es válida"):
        unlock_login(client_ip="not-an-ip")


@pytest.mark.django_db
def test_unlock_by_username_or_address_resets_the_matching_locks() -> None:
    _fail("ana", times=PAIR_FAILURE_LIMIT)
    _fail("beto", ip="198.51.100.1", times=PAIR_FAILURE_LIMIT)

    assert unlock_login(username="ANA") >= 1
    assert login_throttle_status(username="ana", client_ip=IP, now=T0).locked is False
    assert login_throttle_status(username="beto", client_ip="198.51.100.1", now=T0).locked is True

    assert unlock_login(client_ip="198.51.100.1") >= 1
    assert login_throttle_status(username="beto", client_ip="198.51.100.1", now=T0).locked is False


@pytest.mark.django_db
def test_only_a_user_manager_can_unlock_and_the_audit_trail_hides_the_digests(
    admin: User, seller: User
) -> None:
    _fail("ana", times=PAIR_FAILURE_LIMIT)

    with pytest.raises(PermissionDenied):
        unlock_login(username="ana", actor=seller)
    assert login_throttle_status(username="ana", client_ip=IP, now=T0).locked is True

    count = unlock_login(username="ana", actor=admin)

    event = AuditEvent.objects.get(action="accounts.login_unlocked")
    assert event.after == {"records_unlocked": count}
    assert event.actor == admin
    stored = " ".join(row.key_hash for row in LoginThrottle.objects.all())
    assert stored not in str(event.after) and stored not in str(event.before)


@pytest.mark.django_db
def test_unlocking_nothing_leaves_no_audit_event(admin: User) -> None:
    assert unlock_login(username="nobody", actor=admin) == 0
    assert not AuditEvent.objects.filter(action="accounts.login_unlocked").exists()


# --- administrator safeguards ------------------------------------------------------------------


def _login(client: Client, user: User) -> None:
    client.force_login(user)


@pytest.mark.django_db
def test_the_last_active_admin_can_be_neither_demoted_nor_deactivated(admin: User) -> None:
    membership = admin.membership

    with pytest.raises(LastActiveAdminError):
        change_membership_role(membership=membership, role=Membership.Role.VENDEDOR, actor=admin)
    with pytest.raises(LastActiveAdminError):
        set_user_active(membership=membership, active=False, actor=admin)

    admin.refresh_from_db()
    assert admin.is_active and admin.membership.role == Membership.Role.ADMIN


@pytest.mark.django_db
def test_with_a_second_admin_the_first_can_step_down(admin: User, seller: User) -> None:
    change_membership_role(membership=seller.membership, role=Membership.Role.ADMIN, actor=admin)
    promoted = User.objects.get(pk=seller.pk)  # drop the cached membership of the old object

    changed = change_membership_role(
        membership=admin.membership, role=Membership.Role.VENDEDOR, actor=promoted
    )

    assert changed.role == Membership.Role.VENDEDOR


@pytest.mark.django_db
def test_changing_a_role_logs_the_user_out_and_bumps_the_security_version(
    admin: User, seller: User
) -> None:
    client = Client()
    _login(client, seller)
    assert Session.objects.count() == 1
    before = seller.membership.session_version

    change_membership_role(membership=seller.membership, role=Membership.Role.ADMIN, actor=admin)

    seller.membership.refresh_from_db()
    assert seller.membership.session_version == before + 1
    assert Session.objects.count() == 0
    event = AuditEvent.objects.get(action="accounts.role_changed")
    assert event.before == {"role": "VENDEDOR"} and event.after == {"role": "ADMIN"}


@pytest.mark.django_db
def test_setting_the_same_role_or_status_changes_nothing(admin: User, seller: User) -> None:
    version = seller.membership.session_version

    change_membership_role(membership=seller.membership, role=Membership.Role.VENDEDOR, actor=admin)
    set_user_active(membership=seller.membership, active=True, actor=admin)

    seller.membership.refresh_from_db()
    assert seller.membership.session_version == version
    assert not AuditEvent.objects.filter(action__startswith="accounts.").exists()


@pytest.mark.django_db
def test_an_unknown_role_is_refused(admin: User, seller: User) -> None:
    with pytest.raises(ValidationError, match="no existe"):
        change_membership_role(membership=seller.membership, role="SUPERUSER", actor=admin)


@pytest.mark.django_db
def test_a_seller_cannot_manage_users_at_all(admin: User, seller: User) -> None:
    with pytest.raises(PermissionDenied):
        change_membership_role(
            membership=seller.membership, role=Membership.Role.ADMIN, actor=seller
        )
    with pytest.raises(PermissionDenied):
        set_user_active(membership=admin.membership, active=False, actor=seller)
    with pytest.raises(PermissionDenied):
        create_managed_user(username="x", email="", role=Membership.Role.ADMIN, actor=seller)
    with pytest.raises(PermissionDenied):
        issue_activation_token(user=admin, actor=seller, kind=ActivationToken.Kind.RESET)


@pytest.mark.django_db
def test_deactivating_someone_ends_their_sessions_and_blocks_their_access(
    admin: User, seller: User
) -> None:
    client = Client()
    _login(client, seller)

    set_user_active(membership=seller.membership, active=False, actor=admin)

    seller.refresh_from_db()
    assert seller.is_active is False
    assert Session.objects.count() == 0
    assert AuditEvent.objects.filter(action="accounts.user_status_changed").exists()


# --- creating users and activation links -------------------------------------------------------


@pytest.mark.django_db
def test_a_new_user_starts_inactive_with_no_usable_password(admin: User) -> None:
    user, issued = create_managed_user(
        username=" Nuevo ", email="nuevo@example.com", role=Membership.Role.VENDEDOR, actor=admin
    )

    assert user.username == "Nuevo"
    assert user.is_active is False
    assert user.has_usable_password() is False
    assert user.membership.role == Membership.Role.VENDEDOR
    assert issued.token.expires_at - issued.token.created_at <= ACTIVATION_LIFETIME + timedelta(
        seconds=5
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("username", "role", "message"),
    [
        ("   ", "VENDEDOR", "nombre de usuario"),
        ("ADMIN", "VENDEDOR", "Ya existe"),
        ("otro", "ROOT", "rol"),
    ],
)
def test_user_creation_rejects_blank_duplicate_and_unknown_role(
    admin: User, username: str, role: str, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        create_managed_user(username=username, email="", role=role, actor=admin)


@pytest.mark.django_db
def test_only_a_digest_of_the_activation_link_is_stored(admin: User) -> None:
    _, issued = create_managed_user(username="nuevo", email="", role="VENDEDOR", actor=admin)

    assert issued.raw_token not in issued.token.token_hash
    assert len(issued.raw_token) >= 40
    assert not ActivationToken.objects.filter(token_hash=issued.raw_token).exists()


@pytest.mark.django_db
def test_issuing_a_new_link_cancels_the_previous_one(admin: User) -> None:
    user, first = create_managed_user(username="nuevo", email="", role="VENDEDOR", actor=admin)

    second = issue_activation_token(user=user, actor=admin, kind=ActivationToken.Kind.ACTIVATE)

    assert activation_for_token(first.raw_token) is None
    assert activation_for_token(second.raw_token) is not None
    with pytest.raises(ActivationError):
        activate_with_token(raw_token=first.raw_token, password=PASSWORD)


@pytest.mark.django_db
def test_a_reset_link_disables_the_old_password_and_sessions_immediately(
    admin: User, seller: User
) -> None:
    client = Client()
    _login(client, seller)

    issue_activation_token(user=seller, actor=admin, kind=ActivationToken.Kind.RESET)

    seller.refresh_from_db()
    assert seller.has_usable_password() is False
    assert Session.objects.count() == 0


@pytest.mark.django_db
def test_issuing_a_link_for_someone_without_a_membership_is_refused(admin: User) -> None:
    orphan = User.objects.create_user(username="orphan", password=PASSWORD)
    Membership.objects.filter(user=orphan).delete()

    with pytest.raises(ValidationError, match="no pertenece"):
        issue_activation_token(user=orphan, actor=admin, kind=ActivationToken.Kind.ACTIVATE)


@pytest.mark.django_db
def test_a_link_works_once_and_then_the_account_is_active_with_the_chosen_password(
    admin: User,
) -> None:
    user, issued = create_managed_user(username="nuevo", email="", role="VENDEDOR", actor=admin)

    activated = activate_with_token(raw_token=issued.raw_token, password=PASSWORD)

    assert activated.is_active and activated.check_password(PASSWORD)
    with pytest.raises(ActivationError):
        activate_with_token(raw_token=issued.raw_token, password="otra-contraseña-larga-2")
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


@pytest.mark.django_db
def test_an_expired_or_unknown_link_activates_nothing(admin: User) -> None:
    user, issued = create_managed_user(username="nuevo", email="", role="VENDEDOR", actor=admin)
    future = timezone.now() + ACTIVATION_LIFETIME + timedelta(seconds=1)

    assert activation_for_token(issued.raw_token, now=future) is None
    assert activation_for_token("") is None
    assert activation_for_token("no-such-token") is None
    ActivationToken.objects.filter(user=user).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    with pytest.raises(ActivationError):
        activate_with_token(raw_token=issued.raw_token, password=PASSWORD)
    with pytest.raises(ActivationError):
        activate_with_token(raw_token="no-such-token", password=PASSWORD)
    user.refresh_from_db()
    assert user.is_active is False


# --- bootstrap owner ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_bootstrap_creates_then_leaves_the_owner_unchanged_then_rotates_the_password() -> None:
    created = ensure_owner(username="owner", password=PASSWORD, email="o@example.com")
    unchanged = ensure_owner(username="owner", password=PASSWORD, email="o@example.com")
    rotated = ensure_owner(username="owner", password="another-long-password-2")

    assert (created.action, unchanged.action, rotated.action) == ("created", "unchanged", "updated")
    owner = User.objects.get(username="owner")
    assert owner.membership.role == Membership.Role.ADMIN
    assert owner.check_password("another-long-password-2")


@pytest.mark.django_db
def test_bootstrap_reactivates_a_disabled_owner_and_fixes_their_role() -> None:
    ensure_owner(username="owner", password=PASSWORD)
    owner = User.objects.get(username="owner")
    owner.is_active = False
    owner.save()
    Membership.objects.filter(user=owner).update(role=Membership.Role.VENDEDOR)

    result = ensure_owner(username="owner", password=PASSWORD)

    owner.refresh_from_db()
    assert result.action == "updated"
    assert owner.is_active and owner.membership.role == Membership.Role.ADMIN


@pytest.mark.django_db
def test_bootstrap_refuses_to_add_a_second_owner(admin: User) -> None:
    with pytest.raises(OwnerConflictError):
        ensure_owner(username="intruder", password=PASSWORD)

    assert not User.objects.filter(username="intruder").exists()


def test_login_lock_constants_match_the_security_document() -> None:
    assert (PAIR_FAILURE_LIMIT, IP_FAILURE_LIMIT) == (5, 20)
    assert LOGIN_LOCK_DURATION == LOGIN_FAILURE_WINDOW == timedelta(minutes=30)
    assert ACTIVATION_LIFETIME == timedelta(hours=24)
    assert services.PAIR_FAILURE_LIMIT == 5
