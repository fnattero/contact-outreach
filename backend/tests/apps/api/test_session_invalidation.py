from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.db.models import F
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Membership
from apps.accounts.services import change_membership_role


def _two_admins() -> tuple[User, User]:
    first = User.objects.create_user(username="first-admin", password="password-for-tests-1")
    second = User.objects.create_user(username="second-admin", password="password-for-tests-2")
    for user in (first, second):
        user.membership.role = Membership.Role.ADMIN
        user.membership.save(update_fields=("role", "updated_at"))
    return first, second


@pytest.mark.django_db
def test_role_change_leaves_the_caller_unauthenticated_with_problem_json() -> None:
    first, second = _two_admins()
    client = Client()
    client.force_login(first)
    assert client.get(reverse("api-auth-session")).status_code == 200

    change_membership_role(
        membership=first.membership,
        role=Membership.Role.VENDEDOR,
        actor=second,
    )
    response = client.get(reverse("api-auth-session"))

    assert response.status_code == 401
    assert response["Content-Type"] == "application/problem+json"
    assert response.json()["code"] == "authentication_required"
    # change_membership_role also deletes the user's sessions, so DRF sees an anonymous caller.
    assert client.get(reverse("api-auth-session")).status_code == 401


@pytest.mark.django_db
def test_stale_session_version_is_rejected_by_middleware_with_problem_json() -> None:
    # Bump the version without deleting sessions: the middleware is the backstop for a session
    # that survived a security-relevant change, and must answer API callers with JSON.
    first, _ = _two_admins()
    client = Client()
    client.force_login(first)
    assert client.get(reverse("api-auth-session")).status_code == 200
    Membership.objects.filter(pk=first.membership.pk).update(
        session_version=F("session_version") + 1
    )

    response = client.get(reverse("api-auth-session"))

    assert response.status_code == 401
    assert response["Content-Type"] == "application/problem+json"
    assert response.json()["code"] == "authentication_required"
    assert response["Cache-Control"] == "private, no-store"
    assert "WWW-Authenticate" not in response


@pytest.mark.django_db
def test_user_without_membership_is_logged_out_with_problem_json() -> None:
    orphan = User.objects.create_user(username="orphan", password="password-for-tests-3")
    Membership.objects.filter(user=orphan).delete()
    client = Client()
    client.force_login(orphan)

    response = client.get(reverse("api-auth-session"))

    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"
