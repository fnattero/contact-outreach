"""Default-deny authorization over every API route, enumerated from the URL table.

A new endpoint is administrator-only until someone deliberately lists it below, so forgetting to
protect one fails here instead of shipping open. Dummy identifiers keep the requests harmless: the
permission check runs before any view body, serializer or service.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client
from django.urls import URLPattern, reverse

from apps.api.urls import urlpatterns

WRITE_VERBS = ("post", "put", "patch", "delete")
VERBS = ("get", *WRITE_VERBS)

# Reachable without a session (they hand out a CSRF token, start a session or report liveness).
PUBLIC = {
    ("api-auth-csrf", "get"),
    ("api-auth-login", "post"),
    ("api-auth-activate", "post"),
    ("api-health-live", "get"),
    ("api-health-ready", "get"),
}

# The only things a VENDEDOR may reach: PRODUCT_SPEC "sólo lee Resumen, campañas, emails enviados,
# Contactos y conversaciones", plus their own session.
SELLER_ALLOWED = {
    ("api-auth-session", "get"),
    ("api-auth-logout", "post"),
    ("api-auth-reauthenticate", "post"),
    ("api-attention", "get"),
    ("api-dashboard-summary", "get"),
    ("api-campaigns", "get"),
    ("api-campaign-detail", "get"),
    ("api-campaign-coverage", "get"),
    ("api-campaign-messages", "get"),
    ("api-inbound-messages", "get"),
    ("api-inbound-message-thread", "get"),
    ("api-outbound-messages", "get"),
    ("api-outbound-message-detail", "get"),
    ("api-contacts", "get"),
    ("api-contact-detail", "get"),
}


def _view_class(callback: Any) -> Any:
    for attribute in ("cls", "view_class"):
        found = getattr(callback, attribute, None)
        if found:
            return found
    wrapped = getattr(callback, "__wrapped__", None)
    return _view_class(wrapped) if wrapped else None


def _dummy(pattern: URLPattern) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name, converter in pattern.pattern.converters.items():
        kind = type(converter).__name__
        values[name] = {"IntConverter": 987654, "UUIDConverter": uuid.UUID(int=7)}.get(kind, "x")
    return values


def _routes() -> list[tuple[str, str, str]]:
    routes = []
    for pattern in urlpatterns:
        if not isinstance(pattern, URLPattern) or not pattern.name:
            continue
        view = _view_class(pattern.callback)
        verbs = ("get",) if view is None else tuple(v for v in VERBS if hasattr(view, v))
        url = reverse(pattern.name, kwargs=_dummy(pattern))
        routes.extend((pattern.name, verb, url) for verb in verbs)
    return routes


ROUTES = _routes()
IDS = [f"{verb}-{name}" for name, verb, _ in ROUTES]


@pytest.fixture
def seller(owner: User) -> User:
    return User.objects.create_user(username="matrix-seller", password="seller-password-1234")


def _call(user: User | None, verb: str, url: str, *, csrf: bool = True):
    """One fresh client and an empty rate-limit cache per call, so no case sees another's state."""
    cache.clear()
    client = Client(enforce_csrf_checks=True)
    if user is not None:
        client.force_login(user)
    headers: dict[str, str] = {}
    if csrf:
        token = str(client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])
        headers = {"HTTP_X_CSRFTOKEN": token, "HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}
    if verb == "get":
        return client.get(url, **headers)
    return getattr(client, verb)(url, data="{}", content_type="application/json", **headers)


def test_the_matrix_actually_covers_the_api() -> None:
    names = {name for name, _, _ in ROUTES}

    assert len(ROUTES) > 90
    assert {name for name, _ in PUBLIC} <= names
    assert {name for name, _ in SELLER_ALLOWED} <= names
    # Every allow-list entry names a verb that really exists on that route.
    assert (PUBLIC | SELLER_ALLOWED) <= {(name, verb) for name, verb, _ in ROUTES}


@pytest.mark.django_db
@pytest.mark.parametrize(("name", "verb", "url"), ROUTES, ids=IDS)
def test_anonymous_visitors_reach_only_the_public_routes(name: str, verb: str, url: str) -> None:
    response = _call(None, verb, url)

    if (name, verb) in PUBLIC:
        assert response.status_code not in {401, 403}, (name, verb, response.status_code)
    else:
        assert response.status_code in {401, 403}, (name, verb, response.status_code)


@pytest.mark.django_db
@pytest.mark.parametrize(("name", "verb", "url"), ROUTES, ids=IDS)
def test_a_seller_reaches_only_the_documented_read_routes(
    seller: User, name: str, verb: str, url: str
) -> None:
    response = _call(seller, verb, url)

    if (name, verb) in SELLER_ALLOWED or (name, verb) in PUBLIC:
        assert response.status_code not in {401, 403}, (name, verb, response.status_code)
    else:
        assert response.status_code == 403, (name, verb, response.status_code)
        assert response.json()["code"] == "permission_denied"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("name", "verb", "url"),
    [route for route in ROUTES if route[1] in WRITE_VERBS],
    ids=[f"{v}-{n}" for n, v, _ in ROUTES if v in WRITE_VERBS],
)
def test_no_mutation_runs_without_a_csrf_token(owner: User, name: str, verb: str, url: str) -> None:
    signed_in = _call(owner, verb, url, csrf=False)
    assert signed_in.status_code == 403, (name, verb, signed_in.status_code)
    # Login and activation answer with their own csrf_failed; DRF routes with "CSRF Failed".
    assert signed_in.json()["code"] == "csrf_failed" or "CSRF" in signed_in.content.decode(), (
        name,
        verb,
    )

    anonymous = _call(None, verb, url, csrf=False)
    assert anonymous.status_code in {401, 403}, (name, verb, anonymous.status_code)
