from __future__ import annotations

import uuid

from django.contrib import messages
from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.permissions import (
    Capability,
    has_capability,
    require_capability,
    workspace_for_user,
)
from apps.automation.models import (
    ContactCommunicationPlan,
    HumanTask,
    ReplyAutomationConfiguration,
    ScheduledContactAttempt,
)
from apps.automation.scheduled import (
    authorize_scheduled_contact_attempt,
    edit_scheduled_contact_draft,
    save_contact_communication_plan,
    set_contact_communication_plan_state,
    snooze_contact_communication_plan,
)
from apps.automation.services import close_human_task
from apps.campaigns.models import Campaign
from apps.contacts.forms import (
    ContactCommunicationPlanForm,
    ContactEmailForm,
    ContactFilterForm,
    ContactNotesForm,
    ContactPlanSnoozeForm,
    HumanTaskResolutionForm,
    ManualContactForm,
    ManualRestrictionForm,
    RestrictionRevocationForm,
    ScheduledContactDraftForm,
)
from apps.contacts.models import CommunicationRestriction, Contact, EmailAddress
from apps.contacts.queries import attention_queryset, contact_queryset, conversation_timelines
from apps.contacts.services import (
    OrganizationResolutionConflict,
    add_contact_email_address,
    create_manual_contact,
    create_manual_restriction,
    queue_contact_email_validation,
    revoke_manual_restriction,
    set_contact_preferred_email,
    update_contact_notes,
)

EMAIL_PROVENANCE_LABELS = {
    "MANUAL": "Agregado manualmente",
    "INBOUND_MESSAGE": "Dirección desde la que respondió",
    "WEBSITE_MAILTO": "Enlace de email del sitio web",
    "WEBSITE_VISIBLE": "Email visible en el sitio web",
    "website_mailto": "Enlace de email del sitio web",
    "website_visible_text": "Email visible en el sitio web",
    "DISCOVERY": "Encontrado durante una campaña",
    "MIGRATION": "Importado desde el historial anterior",
}
RESTRICTION_SOURCE_LABELS = {
    "contacts_dashboard": "Agregada por un administrador",
    "gmail_reply": "Pedido recibido por email",
    "gmail_bounce": "Rebote informado por Gmail",
    "legacy_suppression": "Importada del historial anterior",
    "legacy_email_invalidity": "Email marcado como inválido anteriormente",
}
TASK_REASON_LABELS = {
    "MEETING_OR_DATE": "Quiere coordinar una reunión o una fecha",
    "PRICING_OR_QUOTE": "Consulta por precio o presupuesto",
    "NEGOTIATION": "Requiere una decisión comercial",
    "COMPLAINT": "Hay un reclamo que debe revisar una persona",
    "LEGAL_OR_PRIVACY": "Consulta legal o de privacidad",
    "UNSUPPORTED_TECHNICAL_ADVICE": "Consulta técnica que no se puede responder con seguridad",
    "MULTIPLE_INTENTS": "El mensaje contiene varios pedidos",
    "AMBIGUOUS_CANDIDATE": "No está claro a qué email enviar la propuesta",
    "OWNERSHIP_CONFLICT": "El email indicado aparece asociado a otra empresa",
    "INSUFFICIENT_CONTEXT": "Falta información para responder con seguridad",
    "PROVIDER_OR_SCHEMA_FAILURE": "No se pudo analizar la respuesta",
    "MANDATORY_CONTEXT_OVERFLOW": "La conversación necesita una revisión completa",
    "AUTOMATIC_MODE_NOT_AVAILABLE": "El envío automático todavía no está habilitado",
    "SCHEDULED_CONTEXT_OR_PROVIDER_FAILURE": ("No se pudo preparar un contacto programado seguro"),
    "SCHEDULED_DELIVERY_FAILED": "No se pudo enviar el contacto programado",
}


def _validation_message(error: ValidationError) -> str:
    return " ".join(error.messages) if error.messages else str(error)


def _contact_or_404(contact_id: uuid.UUID, *, workspace_id: object) -> Contact:
    return get_object_or_404(
        Contact.objects.select_related("organization", "preferred_email"),
        pk=contact_id,
        workspace_id=workspace_id,
    )


def _plan_for(contact: Contact) -> ContactCommunicationPlan | None:
    try:
        return contact.communication_plan
    except ObjectDoesNotExist:
        return None


def _automation_summary(contact: Contact) -> str:
    if contact.automation_suspended:
        return "Pausada para este contacto"
    if contact.conversations.filter(automation_suspended=True).exists():
        return "Pausada en una conversación que necesita revisión"
    configuration = ReplyAutomationConfiguration.objects.filter(workspace=contact.workspace).first()
    if configuration is None or configuration.mode == ReplyAutomationConfiguration.Mode.SHADOW:
        return "Modo de observación: analiza y sugiere, pero no envía"
    if configuration.mode == ReplyAutomationConfiguration.Mode.OFF:
        return "Desactivada"
    return "Activada con controles de seguridad"


@require_capability(Capability.VIEW_CONTACTS)
@require_GET
@never_cache
def contact_list(request: HttpRequest) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.VIEW_CONTACTS)
    form = ContactFilterForm(request.GET)
    values = form.cleaned_data if form.is_valid() else {}
    queryset = contact_queryset(values, workspace_id=workspace.pk)
    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(
        request,
        "contacts/list.html",
        {
            "filter_form": form,
            "page_obj": page,
            "query_string": query.urlencode(),
            "can_manage_contacts": has_capability(actor, Capability.MANAGE_CONTACTS),
        },
    )


@require_capability(Capability.MANAGE_CONTACTS)
@require_http_methods(("GET", "POST"))
@never_cache
def contact_create(request: HttpRequest) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    form = ManualContactForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            contact = create_manual_contact(
                actor=actor,
                email=form.cleaned_data["email"],
                organization_name=form.cleaned_data["organization_name"],
                contact_name=form.cleaned_data["contact_name"],
                notes=form.cleaned_data["notes"],
            )
        except (OrganizationResolutionConflict, ValidationError) as exc:
            form.add_error(None, _validation_message(exc))
        else:
            messages.success(
                request,
                "Contacto guardado. La empresa quedó excluida de nuevas campañas.",
            )
            return redirect("contact-detail", contact_id=contact.pk)
    return render(request, "contacts/form.html", {"form": form})


@require_capability(Capability.VIEW_CONTACTS)
@require_GET
@never_cache
def contact_detail(request: HttpRequest, contact_id: uuid.UUID) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.VIEW_CONTACTS)
    contact = _contact_or_404(contact_id, workspace_id=workspace.pk)
    can_manage = has_capability(actor, Capability.MANAGE_CONTACTS)
    email_addresses = list(
        EmailAddress.objects.filter(organization=contact.organization)
        .prefetch_related("restrictions")
        .order_by("-is_preferred", "provider_order", "created_at")
    )
    email_rows = [
        {
            "address": address,
            "provenance_label": EMAIL_PROVENANCE_LABELS.get(
                address.provenance,
                "Origen registrado" if address.provenance else "Origen no informado",
            ),
            "active_restrictions": [item for item in address.restrictions.all() if item.is_active],
        }
        for address in email_addresses
    ]
    restrictions = list(
        CommunicationRestriction.objects.filter(workspace=workspace)
        .filter(Q(contact=contact) | Q(email_address__organization=contact.organization))
        .select_related("email_address", "created_by", "revoked_by")
        .order_by("-created_at")
    )
    restriction_rows = [
        {
            "restriction": item,
            "source_label": RESTRICTION_SOURCE_LABELS.get(item.source, "Origen registrado"),
            "reason": (
                item.evidence
                if item.source == "contacts_dashboard" and not item.evidence.startswith("Inbound")
                else ""
            ),
        }
        for item in restrictions
    ]
    enrollments = contact.organization.campaign_enrollments.select_related(
        "campaign", "selected_email"
    )
    if not can_manage:
        enrollments = enrollments.exclude(campaign__state=Campaign.State.DRAFT)
    tasks = list(
        HumanTask.objects.filter(contact=contact)
        .select_related("conversation", "inbound", "resolved_by")
        .order_by("-opened_at")
    )
    task_rows = [
        {
            "task": task,
            "reason_label": TASK_REASON_LABELS.get(
                task.reason,
                task.friendly_summary or "Esta conversación necesita revisión",
            ),
        }
        for task in tasks
    ]
    plan = _plan_for(contact)
    latest_attempt = (
        plan.attempts.select_related("outbound_message").order_by("-due_at").first()
        if plan is not None
        else None
    )
    plan_form = ContactCommunicationPlanForm(contact=contact, plan=plan) if can_manage else None
    draft_form = None
    if (
        can_manage
        and latest_attempt is not None
        and latest_attempt.state == ScheduledContactAttempt.State.DRAFT_REVIEW
        and latest_attempt.outbound_message is not None
    ):
        draft_form = ScheduledContactDraftForm(
            initial={
                "subject": latest_attempt.outbound_message.subject,
                "body_text": latest_attempt.outbound_message.body_text,
            }
        )
    return render(
        request,
        "contacts/detail.html",
        {
            "contact": contact,
            "email_rows": email_rows,
            "restriction_rows": restriction_rows,
            "enrollments": enrollments.order_by("-created_at"),
            "timelines": conversation_timelines(contact, include_simulations=can_manage),
            "task_rows": task_rows,
            "open_task_count": sum(1 for task in tasks if task.status == HumanTask.Status.OPEN),
            "plan": plan,
            "latest_attempt": latest_attempt,
            "plan_form": plan_form,
            "plan_snooze_form": ContactPlanSnoozeForm() if can_manage and plan else None,
            "scheduled_draft_form": draft_form,
            "has_validated_preferred_email": bool(
                contact.preferred_email
                and contact.preferred_email.validity == EmailAddress.Validity.VALID
                and not contact.preferred_email.invalid_reason
            ),
            "automation_summary": _automation_summary(contact),
            "can_manage_contacts": can_manage,
            "notes_form": ContactNotesForm(initial={"notes": contact.notes}),
            "email_form": ContactEmailForm(),
            "restriction_form": ManualRestrictionForm(),
            "revocation_form": RestrictionRevocationForm(),
        },
    )


@require_capability(Capability.MANAGE_CONTACTS)
@require_POST
@never_cache
def contact_plan_save(request: HttpRequest, contact_id: uuid.UUID) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_CONTACTS)
    contact = _contact_or_404(contact_id, workspace_id=workspace.pk)
    plan = _plan_for(contact)
    form = ContactCommunicationPlanForm(
        request.POST,
        contact=contact,
        plan=plan,
    )
    if not form.is_valid():
        detail = " ".join(str(message) for errors in form.errors.values() for message in errors)
        messages.error(request, detail or "Revisá la programación e intentá nuevamente.")
    else:
        try:
            preferred_email = form.cleaned_data["preferred_email"]
            assert isinstance(preferred_email, EmailAddress)
            save_contact_communication_plan(
                actor=actor,
                contact_id=contact.pk,
                preferred_email_id=preferred_email.pk,
                purpose=form.cleaned_data["purpose"],
                goal_text=form.cleaned_data["goal_text"],
                cadence_days=form.cleaned_data["cadence_days"],
                mode=form.cleaned_data["mode"],
                enabled=form.cleaned_data["enabled"],
                next_due_at=form.cleaned_data["next_due_at"],
            )
        except ValidationError as exc:
            messages.error(request, _validation_message(exc))
        else:
            messages.success(
                request,
                (
                    "Próximo contacto activado. El sistema respetará la fecha y los controles "
                    "de seguridad."
                    if form.cleaned_data["enabled"]
                    else (
                        "Configuración guardada. No se prepararán mensajes mientras esté "
                        "desactivada."
                    )
                ),
            )
    return redirect("contact-detail", contact_id=contact_id)


@require_capability(Capability.MANAGE_CONTACTS)
@require_POST
@never_cache
def contact_plan_state(
    request: HttpRequest,
    contact_id: uuid.UUID,
    state: str,
) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_CONTACTS)
    _contact_or_404(contact_id, workspace_id=workspace.pk)
    allowed = {
        "activar": ContactCommunicationPlan.State.ACTIVE,
        "pausar": ContactCommunicationPlan.State.PAUSED,
        "desactivar": ContactCommunicationPlan.State.DISABLED,
    }
    selected = allowed.get(state)
    if selected is None:
        messages.error(request, "La acción solicitada no es válida.")
    else:
        try:
            set_contact_communication_plan_state(
                actor=actor,
                contact_id=contact_id,
                state=selected,
            )
        except (ContactCommunicationPlan.DoesNotExist, ValidationError) as exc:
            messages.error(
                request,
                _validation_message(exc)
                if isinstance(exc, ValidationError)
                else "Primero configurá el próximo contacto.",
            )
        else:
            label = {
                ContactCommunicationPlan.State.ACTIVE: "Seguimiento activado.",
                ContactCommunicationPlan.State.PAUSED: (
                    "Seguimiento pausado. No se prepararán ni enviarán mensajes."
                ),
                ContactCommunicationPlan.State.DISABLED: "Seguimiento desactivado.",
            }[selected]
            messages.success(request, label)
    return redirect("contact-detail", contact_id=contact_id)


@require_capability(Capability.MANAGE_CONTACTS)
@require_POST
@never_cache
def contact_plan_snooze(request: HttpRequest, contact_id: uuid.UUID) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_CONTACTS)
    _contact_or_404(contact_id, workspace_id=workspace.pk)
    form = ContactPlanSnoozeForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Elegí una fecha futura para posponer el contacto.")
    else:
        try:
            snooze_contact_communication_plan(
                actor=actor,
                contact_id=contact_id,
                until=form.cleaned_data["until"],
            )
        except (ContactCommunicationPlan.DoesNotExist, ValidationError) as exc:
            messages.error(
                request,
                _validation_message(exc)
                if isinstance(exc, ValidationError)
                else "Primero configurá el próximo contacto.",
            )
        else:
            messages.success(request, "Próximo contacto pospuesto hasta la fecha elegida.")
    return redirect("contact-detail", contact_id=contact_id)


def _scheduled_attempt_or_404(
    attempt_id: uuid.UUID,
    *,
    workspace_id: object,
) -> ScheduledContactAttempt:
    return get_object_or_404(
        ScheduledContactAttempt.objects.select_related("plan__contact", "outbound_message"),
        pk=attempt_id,
        plan__contact__workspace_id=workspace_id,
    )


@require_capability(Capability.MANAGE_CONTACTS)
@require_POST
@never_cache
def scheduled_contact_draft_edit(
    request: HttpRequest,
    contact_id: uuid.UUID,
    attempt_id: uuid.UUID,
) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_CONTACTS)
    attempt = _scheduled_attempt_or_404(attempt_id, workspace_id=workspace.pk)
    if attempt.plan.contact_id != contact_id:
        raise Http404
    form = ScheduledContactDraftForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Completá el asunto y el mensaje antes de guardar.")
    else:
        try:
            edit_scheduled_contact_draft(
                actor=actor,
                attempt_id=attempt.pk,
                subject=form.cleaned_data["subject"],
                body_text=form.cleaned_data["body_text"],
            )
        except ValidationError as exc:
            messages.error(request, _validation_message(exc))
        else:
            messages.success(request, "Borrador guardado. Todavía no se envió.")
    return redirect("contact-detail", contact_id=contact_id)


@require_capability(Capability.MANAGE_CONTACTS)
@require_POST
@never_cache
def scheduled_contact_authorize(
    request: HttpRequest,
    contact_id: uuid.UUID,
    attempt_id: uuid.UUID,
) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_CONTACTS)
    attempt = _scheduled_attempt_or_404(attempt_id, workspace_id=workspace.pk)
    if attempt.plan.contact_id != contact_id:
        raise Http404
    try:
        authorize_scheduled_contact_attempt(actor=actor, attempt_id=attempt.pk)
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
    else:
        messages.success(request, "Mensaje autorizado. Se enviará mediante Gmail.")
    return redirect("contact-detail", contact_id=contact_id)


@require_capability(Capability.VIEW_CONTACTS)
@require_GET
@never_cache
def attention_list(request: HttpRequest) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.VIEW_CONTACTS)
    queryset = attention_queryset(workspace_id=workspace.pk)
    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    rows = [
        {
            "task": task,
            "reason_label": TASK_REASON_LABELS.get(
                task.reason,
                task.friendly_summary or "Esta conversación necesita revisión",
            ),
        }
        for task in page.object_list
    ]
    query = request.GET.copy()
    query.pop("page", None)
    return render(
        request,
        "contacts/attention.html",
        {
            "page_obj": page,
            "attention_rows": rows,
            "query_string": query.urlencode(),
            "can_manage_contacts": has_capability(actor, Capability.MANAGE_CONTACTS),
        },
    )


@require_capability(Capability.MANAGE_AUTOMATION)
@require_POST
@never_cache
def human_task_close(
    request: HttpRequest,
    contact_id: uuid.UUID,
    task_id: uuid.UUID,
) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_AUTOMATION)
    _contact_or_404(contact_id, workspace_id=workspace.pk)
    task = get_object_or_404(
        HumanTask.objects.select_related("contact", "conversation"),
        pk=task_id,
        contact_id=contact_id,
        workspace=workspace,
    )
    outcome = request.POST.get("outcome", "")
    if outcome not in {"resolved", "dismissed"}:
        messages.error(request, "Elegí si la revisión quedó resuelta o si corresponde descartarla.")
        return redirect(f"{reverse('contact-detail', args=(contact_id,))}#tarea-{task.pk}")
    form = HumanTaskResolutionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Contanos brevemente cómo terminó esta revisión.")
    else:
        try:
            close_human_task(
                task,
                actor=actor,
                dismiss=outcome == "dismissed",
                note=form.cleaned_data["note"],
            )
        except ValidationError as exc:
            messages.error(request, _validation_message(exc))
        else:
            messages.success(
                request,
                (
                    "Revisión descartada. La decisión quedó guardada en el historial."
                    if outcome == "dismissed"
                    else "Revisión marcada como resuelta. La decisión quedó guardada."
                ),
            )
    return redirect(f"{reverse('contact-detail', args=(contact_id,))}#tarea-{task.pk}")


@require_capability(Capability.MANAGE_CONTACTS)
@require_POST
@never_cache
def contact_notes(request: HttpRequest, contact_id: uuid.UUID) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_CONTACTS)
    _contact_or_404(contact_id, workspace_id=workspace.pk)
    form = ContactNotesForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Revisá las notas e intentá nuevamente.")
    else:
        update_contact_notes(actor=actor, contact_id=contact_id, notes=form.cleaned_data["notes"])
        messages.success(request, "Notas guardadas.")
    return redirect("contact-detail", contact_id=contact_id)


@require_capability(Capability.MANAGE_CONTACTS)
@require_POST
@never_cache
def contact_email_add(request: HttpRequest, contact_id: uuid.UUID) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_CONTACTS)
    _contact_or_404(contact_id, workspace_id=workspace.pk)
    form = ContactEmailForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Revisá el nuevo email e intentá nuevamente.")
    else:
        try:
            add_contact_email_address(
                actor=actor,
                contact_id=contact_id,
                email=form.cleaned_data["email"],
                label=form.cleaned_data["label"],
                make_preferred=form.cleaned_data["make_preferred"],
            )
        except (OrganizationResolutionConflict, ValidationError) as exc:
            messages.error(request, _validation_message(exc))
        else:
            messages.success(request, "Email agregado al contacto.")
    return redirect("contact-detail", contact_id=contact_id)


@require_capability(Capability.MANAGE_CONTACTS)
@require_POST
@never_cache
def contact_email_preferred(
    request: HttpRequest,
    contact_id: uuid.UUID,
    email_address_id: uuid.UUID,
) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_CONTACTS)
    _contact_or_404(contact_id, workspace_id=workspace.pk)
    get_object_or_404(
        EmailAddress,
        pk=email_address_id,
        workspace=workspace,
        organization__contact__pk=contact_id,
    )
    try:
        set_contact_preferred_email(
            actor=actor,
            contact_id=contact_id,
            email_address_id=email_address_id,
        )
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
    else:
        messages.success(request, "Email preferido actualizado.")
    return redirect("contact-detail", contact_id=contact_id)


@require_capability(Capability.MANAGE_CONTACTS)
@require_POST
@never_cache
def contact_email_validate(
    request: HttpRequest,
    contact_id: uuid.UUID,
    email_address_id: uuid.UUID,
) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_CONTACTS)
    contact = _contact_or_404(contact_id, workspace_id=workspace.pk)
    get_object_or_404(
        EmailAddress,
        pk=email_address_id,
        workspace=workspace,
        organization=contact.organization,
    )
    try:
        queue_contact_email_validation(actor=actor, email_address_id=email_address_id)
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
    else:
        messages.success(
            request,
            "Validación iniciada. El resultado aparecerá en Emails cuando termine.",
        )
    return redirect("contact-detail", contact_id=contact_id)


@require_capability(Capability.MANAGE_CONTACTS)
@require_POST
@never_cache
def contact_restrict(request: HttpRequest, contact_id: uuid.UUID) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_CONTACTS)
    _contact_or_404(contact_id, workspace_id=workspace.pk)
    form = ManualRestrictionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Escribí un motivo para no contactar.")
    else:
        try:
            create_manual_restriction(
                actor=actor,
                scope=CommunicationRestriction.Scope.CONTACT,
                contact_id=contact_id,
                reason=form.cleaned_data["reason"],
            )
        except ValidationError as exc:
            messages.error(request, _validation_message(exc))
        else:
            messages.success(request, "El contacto completo quedó marcado como No contactar.")
    return redirect("contact-detail", contact_id=contact_id)


@require_capability(Capability.MANAGE_CONTACTS)
@require_POST
@never_cache
def contact_email_restrict(
    request: HttpRequest,
    contact_id: uuid.UUID,
    email_address_id: uuid.UUID,
) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_CONTACTS)
    _contact_or_404(contact_id, workspace_id=workspace.pk)
    get_object_or_404(
        EmailAddress,
        pk=email_address_id,
        workspace=workspace,
        organization__contact__pk=contact_id,
    )
    form = ManualRestrictionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Escribí un motivo para dejar de usar este email.")
    else:
        try:
            create_manual_restriction(
                actor=actor,
                scope=CommunicationRestriction.Scope.EMAIL,
                email_address_id=email_address_id,
                reason=form.cleaned_data["reason"],
            )
        except ValidationError as exc:
            messages.error(request, _validation_message(exc))
        else:
            messages.success(request, "Ese email quedó bloqueado; los demás canales no cambian.")
    return redirect("contact-detail", contact_id=contact_id)


@require_capability(Capability.MANAGE_CONTACTS)
@require_POST
@never_cache
def contact_restriction_revoke(
    request: HttpRequest,
    contact_id: uuid.UUID,
    restriction_id: uuid.UUID,
) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_CONTACTS)
    contact = _contact_or_404(contact_id, workspace_id=workspace.pk)
    get_object_or_404(
        CommunicationRestriction.objects.filter(workspace=workspace).filter(
            Q(contact=contact) | Q(email_address__organization=contact.organization)
        ),
        pk=restriction_id,
    )
    form = RestrictionRevocationForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Explicá por qué se vuelve a habilitar.")
    else:
        try:
            revoke_manual_restriction(
                actor=actor,
                restriction_id=restriction_id,
                reason=form.cleaned_data["reason"],
            )
        except ValidationError as exc:
            messages.error(request, _validation_message(exc))
        else:
            messages.success(request, "Restricción manual levantada y motivo guardado.")
    return redirect("contact-detail", contact_id=contact_id)
