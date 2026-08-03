from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.permissions import (
    Capability,
    has_capability,
    require_capability,
    workspace_for_user,
)
from apps.automation.models import HumanTask, ReplyDecision
from apps.automation.presentation import review_reason_for_decision, review_reason_for_task
from apps.campaigns.models import OutboundMessage
from apps.configuration.integrations import runtime_integration_configuration
from apps.dashboard.csv_export import csv_download
from apps.dashboard.queries import (
    outbound_workspace_filter,
    response_queryset,
    workspace_campaigns,
)
from apps.integrations.contracts import ProviderError
from apps.integrations.fakes import FakeGmailProvider
from apps.mailbox.forms import FakeInboundForm, ManualReplyForm
from apps.mailbox.manual import authorize_manual_reply
from apps.mailbox.models import GmailConnection, InboundMessage
from apps.mailbox.services import (
    authorization_url,
    connect_gmail,
    disconnect_gmail,
    oauth_material,
    oauth_redirect_uri,
    provider_for_connection,
    test_gmail_connection,
)
from apps.mailbox.tasks import deliver_manual_reply_task, sync_gmail_connection_task

OAUTH_STATE_SESSION_KEY = "gmail_oauth_state"
OAUTH_VERIFIER_SESSION_KEY = "gmail_oauth_verifier"


@dataclass(frozen=True, slots=True)
class ThreadItem:
    direction: str
    date: datetime
    sender: str
    body: str
    classification: str


def _message_party_label(message: OutboundMessage) -> str:
    if message.prospect is not None:
        return message.prospect.name
    if message.contact is not None:
        return str(message.contact)
    if message.organization is not None:
        return message.organization.name or "Organización sin nombre"
    return message.recipient_normalized


def _inbound_party_label(message: InboundMessage) -> str:
    root = message.related_outbound
    if root is not None:
        return _message_party_label(root)
    if message.contact is not None:
        return str(message.contact)
    if message.organization is not None:
        return message.organization.name or "Organización sin nombre"
    return "Contacto directo"


@require_capability(Capability.MANAGE_INTEGRATIONS)
@never_cache
def gmail_settings(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    workspace = workspace_for_user(owner, Capability.MANAGE_INTEGRATIONS)
    connection = GmailConnection.objects.filter(workspace=workspace).first()
    runtime = runtime_integration_configuration(owner.pk)
    fake_outbound = OutboundMessage.objects.none()
    if runtime.gmail_provider == "fake":
        fake_outbound = (
            OutboundMessage.objects.filter(
                outbound_workspace_filter(workspace.pk),
                state=OutboundMessage.State.SENT,
            )
            .select_related("campaign", "prospect", "organization", "contact")
            .distinct()
        )
    return render(
        request,
        "mailbox/settings.html",
        {
            "connection": connection,
            "fake_mode": runtime.gmail_provider == "fake",
            "gmail_provider": runtime.gmail_provider,
            "fake_outbound": fake_outbound,
            "fake_form": FakeInboundForm(),
        },
    )


@require_capability(Capability.MANAGE_INTEGRATIONS)
@require_POST
def gmail_connect(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    state, verifier, challenge = oauth_material()
    request.session[OAUTH_STATE_SESSION_KEY] = state
    request.session[OAUTH_VERIFIER_SESSION_KEY] = verifier
    callback = request.build_absolute_uri(reverse("gmail-oauth-callback"))
    redirect_uri = oauth_redirect_uri(callback)
    try:
        url = authorization_url(
            owner=owner,
            state=state,
            verifier=verifier,
            challenge=challenge,
            redirect_uri=redirect_uri,
        )
    except (ValidationError, ProviderError, ValueError) as exc:
        request.session.pop(OAUTH_STATE_SESSION_KEY, None)
        request.session.pop(OAUTH_VERIFIER_SESSION_KEY, None)
        messages.error(request, str(exc))
        return redirect("gmail-settings")
    return redirect(url)


@require_capability(Capability.MANAGE_INTEGRATIONS)
@require_GET
def gmail_oauth_callback(request: HttpRequest) -> HttpResponse:
    state = request.GET.get("state", "")
    code = request.GET.get("code", "")
    expected_state = request.session.pop(OAUTH_STATE_SESSION_KEY, "")
    verifier = request.session.pop(OAUTH_VERIFIER_SESSION_KEY, "")
    if not state or not secrets_compare(state, expected_state) or not code or not verifier:
        messages.error(
            request,
            "La autorización de Google no superó la verificación segura de la sesión.",
        )
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
        messages.success(
            request,
            "Cuenta Gmail conectada. Enviá la prueba antes de habilitar el envío en vivo.",
        )
    return redirect("gmail-settings")


def secrets_compare(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left.encode(), right.encode())


@require_capability(Capability.MANAGE_INTEGRATIONS)
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


@require_capability(Capability.MANAGE_INTEGRATIONS)
@require_POST
def gmail_disconnect(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    try:
        disconnect_gmail(owner=owner)
    except (GmailConnection.DoesNotExist, ValidationError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Cuenta Gmail desconectada y credencial local eliminada.")
    return redirect("gmail-settings")


@require_capability(Capability.VIEW_CONTACTS)
@require_GET
@never_cache
def response_list(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    workspace = workspace_for_user(owner, Capability.VIEW_CONTACTS)
    responses = response_queryset(request.GET, workspace_id=workspace.pk)
    page = Paginator(responses, 25).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(
        request,
        "mailbox/responses.html",
        {
            "responses": page,
            "page_obj": page,
            "query_string": query.urlencode(),
            "campaigns": workspace_campaigns(workspace.pk, include_drafts=False),
            "classifications": InboundMessage.Classification.choices,
        },
    )


@require_capability(Capability.EXPORT_DATA)
@require_GET
@never_cache
def response_export(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    workspace = workspace_for_user(owner, Capability.EXPORT_DATA)
    rows = response_queryset(request.GET, workspace_id=workspace.pk)
    return csv_download(
        filename="respuestas.csv",
        headers=(
            "fecha",
            "campaña",
            "prospecto",
            "remitente",
            "asunto",
            "clasificación",
            "humana",
        ),
        rows=(
            (
                item.external_at,
                (
                    item.related_outbound.campaign.name
                    if item.related_outbound is not None
                    and item.related_outbound.campaign is not None
                    else "Sin campaña"
                ),
                _inbound_party_label(item),
                item.sender,
                item.subject,
                item.get_classification_display(),
                "sí" if item.is_human else "no",
            )
            for item in rows
        ),
    )


@require_capability(Capability.MANAGE_INTEGRATIONS)
@require_POST
def fake_inbound(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    workspace = workspace_for_user(owner, Capability.MANAGE_INTEGRATIONS)
    if runtime_integration_configuration(owner.pk).gmail_provider != "fake":
        raise Http404
    form = FakeInboundForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Elegí un envío y un escenario simulado válido.")
        return redirect("gmail-settings")
    outbound = get_object_or_404(
        OutboundMessage.objects.filter(outbound_workspace_filter(workspace.pk))
        .select_related("campaign", "prospect", "organization", "contact")
        .distinct(),
        pk=form.cleaned_data["outbound_id"],
        state=OutboundMessage.State.SENT,
    )
    connection = get_object_or_404(
        GmailConnection,
        workspace=workspace,
        status=GmailConnection.Status.CONNECTED,
    )
    provider = provider_for_connection(connection, persist_fake=True)
    if not isinstance(provider, FakeGmailProvider):
        raise Http404
    scenario = form.cleaned_data["scenario"]
    bodies = {
        "INTERESTED": "Sí, me interesa. Podemos coordinar una visita.",
        "NOT_INTERESTED": "No me interesa por el momento.",
        "UNSUBSCRIBE": "Solicito la BAJA y no recibir más mensajes.",
        "AUTO_REPLY": "Respuesta automática: estoy fuera de la oficina.",
        "BOUNCE": "Falla permanente: usuario desconocido.",
    }
    provider.inject_inbound(
        thread_id=outbound.gmail_thread_id,
        sender=outbound.recipient_normalized,
        recipient=connection.email,
        subject=f"Re: {outbound.subject}",
        body_text=bodies[scenario],
        in_reply_to=outbound.message_id,
        references=(outbound.message_id,),
    )
    sync_gmail_connection_task.delay(str(connection.pk))
    messages.success(request, "Respuesta simulada generada y sincronización solicitada.")
    return redirect("responses")


@require_capability(Capability.VIEW_CONTACTS)
@require_GET
@never_cache
def response_thread(request: HttpRequest, inbound_id: uuid.UUID) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    workspace = workspace_for_user(owner, Capability.VIEW_CONTACTS)
    inbound = get_object_or_404(
        InboundMessage.objects.select_related(
            "related_outbound__campaign",
            "related_outbound__prospect",
            "related_outbound__prospect_email",
            "related_outbound__organization",
            "related_outbound__contact",
            "related_outbound__email_address",
            "organization",
            "contact",
        ),
        pk=inbound_id,
        connection__workspace=workspace,
    )
    root = inbound.related_outbound
    inbound_filter = Q(gmail_thread_id=inbound.gmail_thread_id)
    if root is not None:
        inbound_filter |= Q(related_outbound=root)
    inbound_items = InboundMessage.objects.filter(connection=inbound.connection).filter(
        inbound_filter
    )
    outbound_filter = Q(gmail_thread_id=inbound.gmail_thread_id)
    if root is not None:
        outbound_filter |= Q(pk=root.pk)
    outbound_items = (
        OutboundMessage.objects.filter(outbound_workspace_filter(workspace.pk))
        .filter(outbound_filter)
        .distinct()
    )
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
    review_task = (
        HumanTask.objects.filter(workspace=workspace, inbound=inbound)
        .select_related("decision")
        .order_by("-opened_at")
        .first()
    )
    automation_review = review_reason_for_task(review_task) if review_task is not None else None
    if automation_review is None:
        decision = ReplyDecision.objects.filter(workspace=workspace, inbound=inbound).first()
        if decision is not None:
            automation_review = review_reason_for_decision(decision)
    can_reply = has_capability(owner, Capability.SEND_REPLIES)
    form = ManualReplyForm(initial={"idempotency_key": uuid.uuid4()})
    return render(
        request,
        "mailbox/thread.html",
        {
            "inbound": inbound,
            "root": root,
            "chronology": chronology,
            "automation_review": automation_review,
            "form": form,
            "can_reply": can_reply,
            "show_thread_sidebar": automation_review is not None
            or (inbound.is_human and can_reply),
        },
    )


@require_capability(Capability.SEND_REPLIES)
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
