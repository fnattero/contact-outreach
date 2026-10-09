"""Role capabilities are enforced in services and views, never inferred from who created a row."""

from __future__ import annotations

import uuid

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory

from apps.accounts.models import Membership, Workspace
from apps.accounts.permissions import (
    VENDEDOR_CAPABILITIES,
    Capability,
    has_capability,
    membership_for,
    require_capability,
    require_user_capability,
    workspace_for_user,
)


@pytest.fixture
def admin(db: None) -> User:
    return User.objects.create_user(username="admin", password="password-for-tests-1")


@pytest.fixture
def seller(admin: User) -> User:
    return User.objects.create_user(username="seller", password="password-for-tests-2")


def test_the_first_user_is_admin_and_later_users_are_sellers(admin: User, seller: User) -> None:
    assert admin.membership.role == Membership.Role.ADMIN
    assert seller.membership.role == Membership.Role.VENDEDOR


@pytest.mark.parametrize("capability", list(Capability))
def test_an_admin_holds_every_capability(admin: User, capability: Capability) -> None:
    assert has_capability(admin, capability) is True


@pytest.mark.parametrize("capability", list(Capability))
def test_a_seller_holds_only_the_four_read_capabilities(seller: User, capability: Capability) -> None:
    assert has_capability(seller, capability) is (capability in VENDEDOR_CAPABILITIES)


def test_the_seller_set_is_exactly_the_documented_read_only_set() -> None:
    assert VENDEDOR_CAPABILITIES == {
        Capability.VIEW_SUMMARY,
        Capability.VIEW_CAMPAIGNS,
        Capability.VIEW_SENT_MESSAGES,
        Capability.VIEW_CONTACTS,
    }
    assert not any(
        item.value.startswith(("manage_", "approve_", "send_", "download_", "export_", "retry_"))
        for item in VENDEDOR_CAPABILITIES
    )


@pytest.mark.parametrize("capability", list(Capability))
def test_a_deactivated_user_holds_nothing(admin: User, capability: Capability) -> None:
    admin.is_active = False
    admin.save()

    assert membership_for(admin) is None
    assert has_capability(admin, capability) is False


def test_a_user_without_a_membership_holds_nothing(admin: User) -> None:
    orphan = User.objects.create_user(username="orphan", password="password-for-tests-3")
    Membership.objects.filter(user=orphan).delete()
    orphan = User.objects.get(pk=orphan.pk)

    assert membership_for(orphan) is None
    assert has_capability(orphan, Capability.VIEW_SUMMARY) is False


def test_the_service_boundary_refuses_a_seller_for_admin_work(seller: User) -> None:
    with pytest.raises(PermissionDenied):
        require_user_capability(seller, Capability.SEND_REPLIES)
    with pytest.raises(PermissionDenied):
        workspace_for_user(seller, Capability.MANAGE_USERS)

    assert require_user_capability(seller, Capability.VIEW_CONTACTS) == seller.membership


def test_the_service_boundary_checks_the_workspace_when_one_is_given(admin: User) -> None:
    workspace = Workspace.objects.get()

    assert require_user_capability(admin, Capability.MANAGE_USERS, workspace_id=workspace.pk)
    assert require_user_capability(admin, Capability.MANAGE_USERS, workspace_id=str(workspace.pk))
    with pytest.raises(PermissionDenied):
        require_user_capability(admin, Capability.MANAGE_USERS, workspace_id=uuid.uuid4())
    assert workspace_for_user(admin, Capability.MANAGE_USERS) == workspace


def test_being_the_creator_of_something_grants_nothing(seller: User) -> None:
    # created_by is never a rule: even a seller who "owns" a record keeps seller capabilities.
    assert has_capability(seller, Capability.MANAGE_CAMPAIGNS) is False
    assert has_capability(seller, Capability.EXPORT_DATA) is False


def _guarded(capability: Capability):
    @require_capability(capability)
    def view(request):
        return HttpResponse("secret")

    return view


def test_the_view_decorator_sends_anonymous_visitors_to_login(db: None) -> None:
    request = RequestFactory().get("/x/")
    request.user = AnonymousUser()

    response = _guarded(Capability.VIEW_SUMMARY)(request)

    assert response.status_code == 302
    assert b"secret" not in response.content


def test_the_view_decorator_refuses_a_seller_and_serves_an_admin(admin: User, seller: User) -> None:
    view = _guarded(Capability.MANAGE_USERS)
    denied = RequestFactory().get("/x/")
    denied.user = seller
    allowed = RequestFactory().get("/x/")
    allowed.user = admin

    with pytest.raises(PermissionDenied):
        view(denied)
    assert view(allowed).content == b"secret"
