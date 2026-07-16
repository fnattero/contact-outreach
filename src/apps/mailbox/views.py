from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.integrations.contracts import ProviderError
from apps.mailbox.forms import ManualReplyForm
from apps.mailbox.manual import authorize_manual_reply
from apps.mailbox.models import GmailConnection, InboundMessage
from apps.mailbox.services import (
    authorization_url,
    connect_gmail,
    disconnect_gmail,
    oauth_material,
    oauth_redirect_uri,
    test_gmail_connection,
)
from apps.mailbox.tasks import deliver_manual_reply_task

OAUTH_STATE_SESSION_KEY = "gmail_oauth_state"
OAUTH_VERIFIER_SESSION_KEY = "gmail_oauth_verifier"


@dataclass(frozen=True, slots=True)
class ThreadItem:
    direction: str
    date: datetime
    sender: str
    body: str
    classification: str


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


@login_required
def response_list(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    responses = InboundMessage.objects.filter(connection__owner=owner).select_related(
        "related_outbound__campaign",
        "related_outbound__prospect",
    )
    return render(request, "mailbox/responses.html", {"responses": responses})


@login_required
def response_thread(request: HttpRequest, inbound_id: uuid.UUID) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    inbound = get_object_or_404(
        InboundMessage.objects.select_related(
            "related_outbound__campaign",
            "related_outbound__prospect",
            "related_outbound__prospect_email",
        ),
        pk=inbound_id,
        connection__owner=owner,
    )
    root = inbound.related_outbound
    inbound_items = InboundMessage.objects.filter(
        connection=inbound.connection,
    ).filter(Q(related_outbound=root) | Q(gmail_thread_id=inbound.gmail_thread_id))
    outbound_items = root.campaign.messages.filter(
        Q(pk=root.pk) | Q(parent_inbound__related_outbound=root)
    ).distinct()
    chronology = [
        ThreadItem(
            direction="inbound",
            date=item.external_at,
            sender=item.sender,
            body=item.body_text,
            classification=item.get_classification_display(),
        )
        for item in inbound_items
    ]
    chronology.extend(
        ThreadItem(
            direction="outbound",
            date=item.sent_at or item.simulated_at or item.created_at,
            sender=inbound.connection.email,
            body=item.body_text,
            classification=item.get_kind_display(),
        )
        for item in outbound_items
    )
    chronology.sort(key=lambda item: item.date)
    form = ManualReplyForm(initial={"idempotency_key": uuid.uuid4()})
    return render(
        request,
        "mailbox/thread.html",
        {
            "inbound": inbound,
            "root": root,
            "chronology": chronology,
            "form": form,
        },
    )


@login_required
@require_POST
def manual_reply(request: HttpRequest, inbound_id: uuid.UUID) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    form = ManualReplyForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Revisá el texto de la respuesta e intentá nuevamente.")
        return redirect("response-thread", inbound_id=inbound_id)
    try:
        outbound, created = authorize_manual_reply(
            actor=owner,
            inbound_id=inbound_id,
            body_text=form.cleaned_data["body_text"],
            request_key=form.cleaned_data["idempotency_key"],
        )
    except (InboundMessage.DoesNotExist, ValidationError, ProviderError) as exc:
        messages.error(request, str(exc))
    else:
        if created:
            deliver_manual_reply_task.delay(str(outbound.pk))
            messages.success(request, "Respuesta autorizada y encolada para Gmail.")
        elif outbound.state == outbound.State.SENT:
            messages.success(request, "La respuesta ya había sido enviada.")
        else:
            messages.warning(
                request,
                "Ya existe una respuesta para este mensaje; no se creó otro envío.",
            )
    return redirect("response-thread", inbound_id=inbound_id)
