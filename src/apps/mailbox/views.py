from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.integrations.contracts import ProviderError
from apps.mailbox.models import GmailConnection
from apps.mailbox.services import (
    authorization_url,
    connect_gmail,
    disconnect_gmail,
    oauth_material,
    oauth_redirect_uri,
    test_gmail_connection,
)

OAUTH_STATE_SESSION_KEY = "gmail_oauth_state"
OAUTH_VERIFIER_SESSION_KEY = "gmail_oauth_verifier"


@login_required
def gmail_settings(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    connection = GmailConnection.objects.filter(owner=owner).first()
    return render(request, "mailbox/settings.html", {"connection": connection})


@login_required
@require_POST
def gmail_connect(request: HttpRequest) -> HttpResponse:
    state, verifier, challenge = oauth_material()
    request.session[OAUTH_STATE_SESSION_KEY] = state
    request.session[OAUTH_VERIFIER_SESSION_KEY] = verifier
    callback = request.build_absolute_uri(reverse("gmail-oauth-callback"))
    redirect_uri = oauth_redirect_uri(callback)
    return redirect(
        authorization_url(
            state=state,
            verifier=verifier,
            challenge=challenge,
            redirect_uri=redirect_uri,
        )
    )


@login_required
@require_GET
def gmail_oauth_callback(request: HttpRequest) -> HttpResponse:
    state = request.GET.get("state", "")
    code = request.GET.get("code", "")
    expected_state = request.session.pop(OAUTH_STATE_SESSION_KEY, "")
    verifier = request.session.pop(OAUTH_VERIFIER_SESSION_KEY, "")
    if not state or not secrets_compare(state, expected_state) or not code or not verifier:
        messages.error(request, "La respuesta OAuth no pasó la validación de estado/PKCE.")
        return redirect("gmail-settings")
    owner = request.user
    assert isinstance(owner, User)
    callback = request.build_absolute_uri(reverse("gmail-oauth-callback"))
    try:
        connect_gmail(
            owner=owner,
            code=code,
            verifier=verifier,
            redirect_uri=oauth_redirect_uri(callback),
        )
    except (ValidationError, ProviderError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Cuenta Gmail conectada. Enviá la prueba antes de usar live.")
    return redirect("gmail-settings")


def secrets_compare(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left.encode(), right.encode())


@login_required
@require_POST
def gmail_test(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    try:
        connection = test_gmail_connection(owner=owner)
    except (GmailConnection.DoesNotExist, ValidationError, ProviderError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Prueba enviada únicamente a {connection.email}.")
    return redirect("gmail-settings")


@login_required
@require_POST
def gmail_disconnect(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    try:
        disconnect_gmail(owner=owner)
    except (GmailConnection.DoesNotExist, ValidationError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Cuenta Gmail desconectada y token local eliminado.")
    return redirect("gmail-settings")
