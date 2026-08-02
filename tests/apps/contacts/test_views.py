from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.automation.models import (
    ContactCommunicationPlan,
    FollowUpTopic,
    HumanTask,
    ReplyAutomationConfiguration,
    ReplyDecision,
)
from apps.campaigns.models import Campaign, OutboundMessage
from apps.catalogs.services import create_catalog
from apps.contacts.models import (
    CampaignEnrollment,
    CommunicationRestriction,
    Contact,
    Conversation,
    EmailAddress,
    Organization,
)
from apps.contacts.services import OrganizationResolutionConflict, create_manual_contact
from apps.integrations.gmail import GMAIL_SCOPES
from apps.mailbox.crypto import encrypt_token
from apps.mailbox.models import GmailConnection, InboundMessage


def _messages(response) -> list[str]:
    return [str(item) for item in get_messages(response.wsgi_request)]


def _history_fixture(
    owner: User,
    *,
    private_catalog_dir: Path,
) -> tuple[Contact, EmailAddress, EmailAddress, HumanTask]:
    del private_catalog_dir
    workspace = owner.membership.workspace
    contact = create_manual_contact(
        actor=owner,
        email="ventas@cliente.example",
        organization_name="Motores del Sur",
        contact_name="Ana Pérez",
        notes="Prefiere recibir novedades por email.",
    )
    primary = contact.preferred_email
    assert primary is not None
    primary.validity = EmailAddress.Validity.VALID
    primary.provenance = "MANUAL"
    primary.validated_at = timezone.now()
    primary.save(update_fields=("validity", "provenance", "validated_at", "updated_at"))
    secondary = EmailAddress.objects.create(
        workspace=workspace,
        organization=contact.organization,
        original_email="propuestas@cliente.example",
        normalized_email="propuestas@cliente.example",
        domain="cliente.example",
        label="Propuestas",
        provenance="INBOUND_MESSAGE",
        validity=EmailAddress.Validity.VALID,
        validated_at=timezone.now(),
    )
    catalog = create_catalog(
        name="Catálogo Contactos",
        upload=SimpleUploadedFile(
            "catalogo-contactos.pdf",
            b"%PDF-1.4\n% contacts\n%%EOF",
            content_type="application/pdf",
        ),
        actor=owner,
    )
    campaign = Campaign.objects.create(
        workspace=workspace,
        name="Campaña Talleres",
        state=Campaign.State.PAUSED,
        catalog=catalog,
        created_by=owner,
    )
    enrollment = CampaignEnrollment.objects.create(
        workspace=workspace,
        campaign=campaign,
        organization=contact.organization,
        selected_email=primary,
        state=CampaignEnrollment.State.RESPONDED,
        source="TEST",
        initial_sent_at=timezone.now() - timedelta(days=5),
        replied_at=timezone.now() - timedelta(days=4),
    )
    connection = GmailConnection.objects.create(
        workspace=workspace,
        owner=owner,
        email="equipo@example.com",
        scopes=list(GMAIL_SCOPES),
        refresh_token_encrypted=encrypt_token("refresh-token"),
        status=GmailConnection.Status.CONNECTED,
        last_tested_at=timezone.now(),
    )
    first_conversation = Conversation.objects.create(
        workspace=workspace,
        contact=contact,
        connection=connection,
        gmail_thread_id="gmail-thread-initial-sensitive",
        subject="Propuesta comercial",
        last_message_at=timezone.now() - timedelta(days=4),
    )
    first_outbound = OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.INITIAL,
        campaign=campaign,
        organization=contact.organization,
        campaign_enrollment=enrollment,
        contact=contact,
        conversation=first_conversation,
        email_address=primary,
        recipient=primary.original_email,
        recipient_normalized=primary.normalized_email,
        subject="Propuesta comercial",
        body_text="Primero enviamos la propuesta y los catálogos.",
        state=OutboundMessage.State.SENT,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        idempotency_key="contact-ui-initial",
        message_id="<technical-first@example.invalid>",
        gmail_message_id="gmail-technical-first",
        gmail_thread_id=first_conversation.gmail_thread_id,
        sent_at=timezone.now() - timedelta(days=5),
    )
    first_inbound = InboundMessage.objects.create(
        connection=connection,
        organization=contact.organization,
        campaign_enrollment=enrollment,
        contact=contact,
        conversation=first_conversation,
        related_outbound=first_outbound,
        gmail_message_id="gmail-inbound-technical-first",
        gmail_thread_id=first_conversation.gmail_thread_id,
        message_id="<technical-inbound@example.invalid>",
        sender="Ana <ventas@cliente.example>",
        recipients=[connection.email],
        subject="Re: Propuesta comercial",
        external_at=timezone.now() - timedelta(days=4),
        received_at=timezone.now() - timedelta(days=4),
        body_text="Me interesa. ¿Qué día podemos reunirnos?",
        classification=InboundMessage.Classification.INTERESTED,
        classification_confidence="0.990",
        is_human=True,
    )
    task = HumanTask.objects.create(
        workspace=workspace,
        contact=contact,
        conversation=first_conversation,
        inbound=first_inbound,
        kind="REPLY_REVIEW",
        reason="MEETING_OR_DATE",
        status=HumanTask.Status.OPEN,
        friendly_summary="Esta conversación necesita que la revise una persona.",
        opened_at=timezone.now() - timedelta(days=4),
    )
    ReplyDecision.objects.create(
        workspace=workspace,
        inbound=first_inbound,
        contact=contact,
        conversation=first_conversation,
        mode="LIVE",
        provider="proveedor-tecnico-secreto",
        model="modelo-tecnico-secreto",
        policy_version="politica-tecnica-secreta",
        classification="INTERESTED",
        intent="MEETING_OR_DATE",
        action="HUMAN",
        confidence="0.990",
        human_reason="MEETING_OR_DATE",
        context_manifest={"technical": "manifest-secret"},
        context_hash="a" * 64,
        state=ReplyDecision.State.HUMAN_REQUIRED,
    )

    second_conversation = Conversation.objects.create(
        workspace=workspace,
        contact=contact,
        connection=connection,
        gmail_thread_id="gmail-thread-referred-sensitive",
        subject="Propuesta enviada a la dirección indicada",
        last_message_at=timezone.now() - timedelta(days=2),
    )
    referred = OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.REFERRED_PROPOSAL,
        campaign=campaign,
        organization=contact.organization,
        campaign_enrollment=enrollment,
        contact=contact,
        conversation=second_conversation,
        email_address=secondary,
        recipient=secondary.original_email,
        recipient_normalized=secondary.normalized_email,
        subject="Propuesta comercial",
        body_text="Después enviamos la propuesta a la dirección indicada.",
        state=OutboundMessage.State.SENT,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        idempotency_key="contact-ui-referred",
        message_id="<technical-referred@example.invalid>",
        gmail_message_id="gmail-technical-referred",
        gmail_thread_id=second_conversation.gmail_thread_id,
        sent_at=timezone.now() - timedelta(days=3),
    )
    InboundMessage.objects.create(
        connection=connection,
        organization=contact.organization,
        campaign_enrollment=enrollment,
        contact=contact,
        conversation=second_conversation,
        related_outbound=referred,
        gmail_message_id="gmail-inbound-technical-second",
        gmail_thread_id=second_conversation.gmail_thread_id,
        message_id="<technical-inbound-second@example.invalid>",
        sender="Compras <propuestas@cliente.example>",
        recipients=[connection.email],
        subject="Re: Propuesta comercial",
        external_at=timezone.now() - timedelta(days=2),
        received_at=timezone.now() - timedelta(days=2),
        body_text="Recibimos el material, muchas gracias.",
        classification=InboundMessage.Classification.OTHER,
        classification_confidence="0.800",
        is_human=True,
    )
    OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.INITIAL,
        campaign=campaign,
        organization=contact.organization,
        campaign_enrollment=enrollment,
        contact=contact,
        email_address=primary,
        recipient=primary.original_email,
        recipient_normalized=primary.normalized_email,
        subject="Simulación interna",
        body_text="CUERPO-DE-SIMULACION-SOLO-ADMIN",
        state=OutboundMessage.State.DRY_RUN_COMPLETED,
        delivery_mode=Campaign.DeliveryMode.DRY_RUN,
        idempotency_key="contact-ui-simulation",
        simulated_at=timezone.now() - timedelta(days=1),
    )
    CommunicationRestriction.objects.create(
        workspace=workspace,
        scope=CommunicationRestriction.Scope.EMAIL,
        kind=CommunicationRestriction.Kind.BOUNCE,
        email_address=secondary,
        source="gmail_bounce",
        evidence="InboundMessage:technical-evidence-id",
    )
    topic = FollowUpTopic.objects.create(
        workspace=workspace,
        name="Preguntar cómo está",
        objective="Retomar el contacto de manera cordial.",
        cadence_days=30,
        mode=FollowUpTopic.Mode.REVIEW_BEFORE_SEND,
        next_due_at=timezone.now() + timedelta(days=20),
        active=True,
        created_by=owner,
        updated_by=owner,
    )
    ContactCommunicationPlan.objects.create(
        contact=contact,
        topic=topic,
        preferred_email=primary,
        state=ContactCommunicationPlan.State.ACTIVE,
        next_due_at=timezone.now() + timedelta(days=20),
        created_by=owner,
        updated_by=owner,
    )
    contact.last_interaction_at = timezone.now() - timedelta(days=2)
    contact.save(update_fields=("last_interaction_at", "updated_at"))
    return contact, primary, secondary, task


@pytest.mark.django_db
def test_admin_contactos_navigation_timeline_and_plain_language(
    client: Client,
    owner: User,
    private_catalog_dir: Path,
) -> None:
    contact, _, _, _ = _history_fixture(owner, private_catalog_dir=private_catalog_dir)
    client.force_login(owner)

    listing = client.get(reverse("contacts"))
    detail = client.get(reverse("contact-detail", args=(contact.pk,)))
    attention = client.get(reverse("attention"))

    for response in (listing, detail, attention):
        assert response.status_code == 200
        assert "private" in response.headers["Cache-Control"]
        assert "no-store" in response.headers["Cache-Control"]
    list_page = listing.content.decode()
    detail_page = detail.content.decode()
    attention_page = attention.content.decode()
    assert "Ana Pérez" in list_page
    assert "Motores del Sur" in list_page
    assert "No contactar" in list_page
    assert "1 tema aprobado" in list_page
    assert "Necesita que lo revises" in list_page
    assert "Contactos" in list_page
    assert "Necesita atención" in list_page
    assert "Respuesta automática" in list_page
    assert ">Prospectos<" not in list_page
    assert ">Respuestas<" not in list_page
    assert ">Supresiones<" not in list_page

    assert 'aria-label="Mapa de relación"' in detail_page
    assert 'class="contact-overview-grid"' in detail_page
    assert 'class="conversation-timeline"' in detail_page
    assert 'class="task-card task-card--open"' in detail_page
    assert 'class="follow-up-topic-grid section-gap"' in detail_page
    assert detail_page.index('id="plan-heading"') < detail_page.index(
        'aria-label="Información y controles del contacto"'
    )
    assert detail_page.index('id="restriction-heading"') < detail_page.index(
        'aria-label="Información y controles del contacto"'
    )
    assert "1 canal disponible" in detail_page
    assert "Preguntar cómo está" in detail_page
    assert "No contactar este contacto" not in detail_page
    assert "Propuesta comercial" in detail_page
    assert "Propuesta enviada a la dirección indicada" in detail_page
    assert detail_page.index("Primero enviamos la propuesta") < detail_page.index(
        "Me interesa. ¿Qué día podemos reunirnos?"
    )
    assert detail_page.index("Después enviamos la propuesta") < detail_page.index(
        "Recibimos el material"
    )
    assert "Dirección desde la que respondió" in detail_page
    assert "Rebote informado por Gmail" in detail_page
    assert "Campaña Talleres" in detail_page
    assert "Prefiere recibir novedades por email" in detail_page
    assert "Modo de observación" in detail_page
    assert "No hay un próximo contacto programado" not in detail_page
    assert "CUERPO-DE-SIMULACION-SOLO-ADMIN" in detail_page
    assert "Leer y responder" in detail_page
    assert "Marcar como resuelta" in detail_page
    assert "Quiere coordinar una reunión o una fecha" in attention_page
    assert "MEETING_OR_DATE" not in attention_page

    # Historical routes remain available, but they are no longer primary navigation.
    assert client.get(reverse("prospects")).status_code == 200
    assert client.get(reverse("responses")).status_code == 200
    assert client.get(reverse("suppressions")).status_code == 200


@pytest.mark.django_db
def test_vendedor_reads_contactos_without_controls_simulations_or_technical_details(
    client: Client,
    owner: User,
    private_catalog_dir: Path,
) -> None:
    contact, primary, _, restriction_task = _history_fixture(
        owner,
        private_catalog_dir=private_catalog_dir,
    )
    seller = User.objects.create_user(username="seller-contactos", password="password")
    client.force_login(seller)

    readable = (
        reverse("contacts"),
        reverse("contact-detail", args=(contact.pk,)),
        reverse("attention"),
    )
    for url in readable:
        response = client.get(url)
        assert response.status_code == 200
        assert "private" in response.headers["Cache-Control"]
        assert "no-store" in response.headers["Cache-Control"]
        assert client.post(url).status_code == 405

    detail = client.get(reverse("contact-detail", args=(contact.pk,))).content.decode()
    assert "Primero enviamos la propuesta" in detail
    assert "Recibimos el material" in detail
    assert "CUERPO-DE-SIMULACION-SOLO-ADMIN" not in detail
    assert "Agregar otro email" not in detail
    assert "Guardar notas" not in detail
    assert "No contactar este contacto" not in detail
    assert "Usar como preferido" not in detail
    assert "Marcar como resuelta" not in detail
    assert "proveedor-tecnico-secreto" not in detail
    assert "modelo-tecnico-secreto" not in detail
    assert "manifest-secret" not in detail
    assert "gmail-thread-initial-sensitive" not in detail
    assert "technical-inbound@example.invalid" not in detail

    forbidden = (
        (reverse("contact-create"), {}),
        (reverse("contact-notes", args=(contact.pk,)), {"notes": "No autorizado"}),
        (
            reverse("contact-email-add", args=(contact.pk,)),
            {"email": "otro@cliente.example"},
        ),
        (
            reverse("contact-email-preferred", args=(contact.pk, primary.pk)),
            {},
        ),
        (
            reverse("contact-email-validate", args=(contact.pk, primary.pk)),
            {},
        ),
        (reverse("contact-no-contact-toggle", args=(contact.pk,)), {"blocked": "1"}),
        (reverse("contact-restrict", args=(contact.pk,)), {"reason": "No autorizado"}),
        (
            reverse("human-task-close", args=(contact.pk, restriction_task.pk)),
            {"outcome": "resolved", "note": "No autorizado"},
        ),
    )
    for url, payload in forbidden:
        assert client.post(url, payload).status_code == 403
    assert client.get(reverse("contact-create")).status_code == 403
    assert restriction_task.status == HumanTask.Status.OPEN


@pytest.mark.django_db
def test_contact_list_no_contact_checkbox_controls_manual_contact_restriction(
    client: Client,
    owner: User,
) -> None:
    contact = create_manual_contact(
        actor=owner,
        email="bloqueo@cliente.example",
        organization_name="Cliente Bloqueo",
        contact_name="Persona Bloqueo",
    )
    client.force_login(owner)

    blocked = client.post(
        reverse("contact-no-contact-toggle", args=(contact.pk,)),
        {"blocked": "1"},
    )

    assert blocked.status_code == 302
    contact.refresh_from_db()
    assert contact.status == Contact.Status.DO_NOT_CONTACT
    restriction = CommunicationRestriction.objects.get(contact=contact)
    assert restriction.kind == CommunicationRestriction.Kind.MANUAL
    list_page = client.get(reverse("contacts")).content.decode()
    assert "Cliente Bloqueo" in list_page
    assert "checked" in list_page

    unblocked = client.post(
        reverse("contact-no-contact-toggle", args=(contact.pk,)),
        {"blocked": "0"},
    )

    assert unblocked.status_code == 302
    contact.refresh_from_db()
    restriction.refresh_from_db()
    assert contact.status == Contact.Status.ACTIVE
    assert restriction.revoked_at is not None


@pytest.mark.django_db
def test_admin_can_resolve_human_task_from_contactos_and_resume_conversation(
    client: Client,
    owner: User,
    private_catalog_dir: Path,
) -> None:
    contact, _, _, task = _history_fixture(owner, private_catalog_dir=private_catalog_dir)
    assert task.conversation is not None
    task.conversation.automation_suspended = True
    task.conversation.save(update_fields=("automation_suspended", "updated_at"))
    client.force_login(owner)
    url = reverse("human-task-close", args=(contact.pk, task.pk))

    missing_note = client.post(url, {"outcome": "resolved", "note": ""})
    assert missing_note.status_code == 302
    task.refresh_from_db()
    assert task.status == HumanTask.Status.OPEN

    resolved = client.post(
        url,
        {
            "outcome": "resolved",
            "note": "Coordinamos la reunión por teléfono.",
        },
    )
    assert resolved.status_code == 302
    assert resolved.url.endswith(f"#tarea-{task.pk}")
    task.refresh_from_db()
    task.conversation.refresh_from_db()
    assert task.status == HumanTask.Status.RESOLVED
    assert task.resolved_by == owner
    assert task.resolution_note == "Coordinamos la reunión por teléfono."
    assert task.conversation.automation_suspended is False
    detail = client.get(reverse("contact-detail", args=(contact.pk,))).content.decode()
    assert "Coordinamos la reunión por teléfono" in detail


@pytest.mark.django_db
def test_admin_manual_contact_channels_notes_and_restrictions(
    client: Client,
    owner: User,
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks,
) -> None:
    client.force_login(owner)
    create_response = client.post(
        reverse("contact-create"),
        {
            "email": "cliente-manual@example.com",
            "organization_name": "Cliente manual",
            "contact_name": "Laura",
            "notes": "Contacto cargado por el equipo.",
        },
    )
    contact = Contact.objects.get(name="Laura")
    assert create_response.status_code == 302
    assert create_response.url == reverse("contact-detail", args=(contact.pk,))
    assert Contact.objects.count() == 1

    add_email = client.post(
        reverse("contact-email-add", args=(contact.pk,)),
        {
            "email": "compras@example.com",
            "label": "Compras",
            "make_preferred": "on",
        },
    )
    assert add_email.status_code == 302
    secondary = EmailAddress.objects.get(normalized_email="compras@example.com")
    contact.refresh_from_db()
    assert contact.preferred_email == secondary
    assert secondary.is_preferred is True

    queued_validations: list[str] = []
    monkeypatch.setattr(
        "apps.contacts.tasks.validate_contact_email_task.delay",
        lambda email_id: queued_validations.append(email_id),
    )
    with django_capture_on_commit_callbacks(execute=True):
        validation = client.post(
            reverse("contact-email-validate", args=(contact.pk, secondary.pk)),
        )
    assert validation.status_code == 302
    assert queued_validations == [str(secondary.pk)]

    assert (
        client.post(
            reverse("contact-notes", args=(contact.pk,)),
            {"notes": "Llamar únicamente por la tarde."},
        ).status_code
        == 302
    )
    contact.refresh_from_db()
    assert contact.notes == "Llamar únicamente por la tarde."

    assert (
        client.post(
            reverse("contact-email-restrict", args=(contact.pk, secondary.pk)),
            {"reason": "Pidió usar solamente el email de ventas."},
        ).status_code
        == 302
    )
    restriction = CommunicationRestriction.objects.get(
        email_address=secondary,
        kind=CommunicationRestriction.Kind.MANUAL,
        revoked_at__isnull=True,
    )
    assert (
        client.post(
            reverse("contact-restriction-revoke", args=(contact.pk, restriction.pk)),
            {"reason": "La clienta confirmó que vuelve a usar Compras."},
        ).status_code
        == 302
    )
    restriction.refresh_from_db()
    assert restriction.revoked_at is not None
    assert restriction.revocation_reason == "La clienta confirmó que vuelve a usar Compras."

    assert (
        client.post(
            reverse("contact-restrict", args=(contact.pk,)),
            {"reason": "Pausa solicitada por la clienta."},
        ).status_code
        == 302
    )
    contact.refresh_from_db()
    assert contact.status == Contact.Status.DO_NOT_CONTACT
    page = client.get(reverse("contact-detail", args=(contact.pk,))).content.decode()
    assert "Llamar únicamente por la tarde" in page
    assert "La clienta confirmó que vuelve a usar Compras" in page
    assert "Pausa solicitada por la clienta" in page


@pytest.mark.django_db
def test_contactos_forms_are_accessible_and_invalid_manual_entry_is_actionable(
    client: Client,
    owner: User,
) -> None:
    client.force_login(owner)
    form_page = client.get(reverse("contact-create"))
    assert form_page.status_code == 200
    content = form_page.content.decode()
    assert "El email es el único dato obligatorio" not in content
    assert "Es el único dato obligatorio" in content
    assert "<fieldset>" in content
    assert "(obligatorio)" in content

    invalid = client.post(reverse("contact-create"), {"email": "no-es-un-email"})
    invalid_page = invalid.content.decode()
    assert invalid.status_code == 200
    assert "No pudimos guardar el contacto" not in invalid_page
    assert "Escribí un email válido" in invalid_page
    assert Contact.objects.count() == 0


@pytest.mark.django_db
def test_contact_create_surfaces_a_business_rule_conflict_from_the_service(
    client: Client,
    owner: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_manual_contact can refuse to merge conflicting organizations; the
    view must surface that as a form error instead of a server crash."""

    def _raise(**_kwargs: object) -> None:
        raise OrganizationResolutionConflict(
            "Los datos coinciden con organizaciones distintas. Hace falta una revisión manual."
        )

    monkeypatch.setattr("apps.contacts.views.create_manual_contact", _raise)
    client.force_login(owner)

    response = client.post(
        reverse("contact-create"),
        {"email": "conflicto@example.com"},
    )
    assert response.status_code == 200
    assert "coinciden con organizaciones distintas" in response.content.decode()
    assert Contact.objects.count() == 0


@pytest.mark.django_db
def test_contact_detail_shows_plain_language_automation_summary_for_each_state(
    client: Client,
    owner: User,
) -> None:
    workspace = owner.membership.workspace
    client.force_login(owner)

    suspended_contact = create_manual_contact(actor=owner, email="pausado@cliente.example")
    suspended_contact.automation_suspended = True
    suspended_contact.save(update_fields=("automation_suspended", "updated_at"))
    suspended_page = client.get(
        reverse("contact-detail", args=(suspended_contact.pk,))
    ).content.decode()
    assert "Pausada para este contacto" in suspended_page

    conversation_contact = create_manual_contact(actor=owner, email="conversacion@cliente.example")
    connection = GmailConnection.objects.create(
        workspace=workspace,
        owner=owner,
        email="equipo@example.com",
        scopes=list(GMAIL_SCOPES),
        refresh_token_encrypted=encrypt_token("token-conversacion"),
        status=GmailConnection.Status.CONNECTED,
        last_tested_at=timezone.now(),
    )
    Conversation.objects.create(
        workspace=workspace,
        contact=conversation_contact,
        connection=connection,
        gmail_thread_id="thread-pausado",
        automation_suspended=True,
    )
    conversation_page = client.get(
        reverse("contact-detail", args=(conversation_contact.pk,))
    ).content.decode()
    assert "Pausada en una conversación que necesita revisión" in conversation_page

    normal_contact = create_manual_contact(actor=owner, email="normal@cliente.example")
    configuration = ReplyAutomationConfiguration.objects.create(
        workspace=workspace,
        mode=ReplyAutomationConfiguration.Mode.OFF,
    )
    off_page = client.get(reverse("contact-detail", args=(normal_contact.pk,))).content.decode()
    assert "Desactivada" in off_page

    configuration.mode = ReplyAutomationConfiguration.Mode.LIVE
    configuration.live_enabled_at = timezone.now()
    configuration.live_enabled_by = owner
    configuration.save(update_fields=("mode", "live_enabled_at", "live_enabled_by", "updated_at"))
    live_page = client.get(reverse("contact-detail", args=(normal_contact.pk,))).content.decode()
    assert "Activada con controles de seguridad" in live_page


@pytest.mark.django_db
def test_human_task_close_rejects_unknown_outcomes_and_surfaces_service_errors(
    client: Client,
    owner: User,
    private_catalog_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contact, _, _, task = _history_fixture(owner, private_catalog_dir=private_catalog_dir)
    client.force_login(owner)
    url = reverse("human-task-close", args=(contact.pk, task.pk))

    bogus_outcome = client.post(url, {"outcome": "bogus", "note": "Da igual"})
    assert bogus_outcome.status_code == 302
    assert any("resuelta" in m or "descartarla" in m for m in _messages(bogus_outcome))
    task.refresh_from_db()
    assert task.status == HumanTask.Status.OPEN

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise ValidationError("Contanos brevemente cómo se resolvió.")

    monkeypatch.setattr("apps.contacts.views.close_human_task", _raise)
    service_error = client.post(url, {"outcome": "resolved", "note": "Nota válida"})
    assert service_error.status_code == 302
    assert any("Contanos brevemente" in m for m in _messages(service_error))
    task.refresh_from_db()
    assert task.status == HumanTask.Status.OPEN


@pytest.mark.django_db
def test_contact_notes_rejects_a_form_that_is_too_long(
    client: Client,
    owner: User,
) -> None:
    contact = create_manual_contact(actor=owner, email="notas@cliente.example")
    client.force_login(owner)

    response = client.post(
        reverse("contact-notes", args=(contact.pk,)),
        {"notes": "x" * 5001},
    )
    assert response.status_code == 302
    assert any("Revisá las notas" in m for m in _messages(response))
    contact.refresh_from_db()
    assert contact.notes == ""


@pytest.mark.django_db
def test_contact_email_add_rejects_invalid_forms_and_cross_organization_conflicts(
    client: Client,
    owner: User,
) -> None:
    # Built directly (bypassing resolve_organization) so this contact's organization
    # has no OrganizationIdentity rows of its own; that isolates the conflict this
    # test wants to exercise (the target email already belongs to *another*
    # organization) from the "identities collide across organizations" conflict
    # that resolve_organization would otherwise raise first.
    workspace = owner.membership.workspace
    organization = Organization.objects.create(workspace=workspace, name="Titular")
    preferred = EmailAddress.objects.create(
        workspace=workspace,
        organization=organization,
        original_email="titular@cliente.example",
        normalized_email="titular@cliente.example",
        domain="cliente.example",
        is_preferred=True,
        validity=EmailAddress.Validity.VALID,
    )
    contact = Contact.objects.create(
        workspace=workspace,
        organization=organization,
        preferred_email=preferred,
        created_reason=Contact.CreatedReason.MANUAL_ENTRY,
        created_by=owner,
    )
    other_contact = create_manual_contact(actor=owner, email="otra-empresa@cliente.example")
    assert other_contact.preferred_email is not None
    client.force_login(owner)
    url = reverse("contact-email-add", args=(contact.pk,))

    invalid = client.post(url, {"email": "no-es-un-email"})
    assert invalid.status_code == 302
    assert any("Revisá el nuevo email" in m for m in _messages(invalid))

    conflict = client.post(
        url,
        {"email": other_contact.preferred_email.original_email},
    )
    assert conflict.status_code == 302
    assert any("pertenece a otra organización" in m for m in _messages(conflict))
    contact.refresh_from_db()
    assert contact.organization.email_addresses.count() == 1


@pytest.mark.django_db
def test_contact_email_validate_refuses_to_requeue_an_already_validated_email(
    client: Client,
    owner: User,
) -> None:
    contact = create_manual_contact(actor=owner, email="validado@cliente.example")
    email = contact.preferred_email
    assert email is not None
    email.validity = EmailAddress.Validity.VALID
    email.validated_at = timezone.now()
    email.save(update_fields=("validity", "validated_at", "updated_at"))
    client.force_login(owner)

    response = client.post(
        reverse("contact-email-validate", args=(contact.pk, email.pk)),
    )
    assert response.status_code == 302
    assert any("ya tiene un resultado de validación" in m for m in _messages(response))


@pytest.mark.django_db
def test_contact_restrict_rejects_blank_reasons_and_surfaces_service_errors(
    client: Client,
    owner: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contact = create_manual_contact(actor=owner, email="sin-motivo@cliente.example")
    client.force_login(owner)
    url = reverse("contact-restrict", args=(contact.pk,))

    invalid = client.post(url, {"reason": ""})
    assert invalid.status_code == 302
    assert any("Escribí un motivo para no contactar" in m for m in _messages(invalid))
    contact.refresh_from_db()
    assert contact.status == Contact.Status.ACTIVE

    def _raise(**_kwargs: object) -> None:
        raise ValidationError("No se pudo aplicar la restricción.")

    monkeypatch.setattr("apps.contacts.views.create_manual_restriction", _raise)
    service_error = client.post(url, {"reason": "Motivo válido"})
    assert service_error.status_code == 302
    assert any("No se pudo aplicar" in m for m in _messages(service_error))
    contact.refresh_from_db()
    assert contact.status == Contact.Status.ACTIVE


@pytest.mark.django_db
def test_contact_email_restrict_rejects_blank_reasons_and_surfaces_service_errors(
    client: Client,
    owner: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contact = create_manual_contact(actor=owner, email="canal@cliente.example")
    email = contact.preferred_email
    assert email is not None
    client.force_login(owner)
    url = reverse("contact-email-restrict", args=(contact.pk, email.pk))

    invalid = client.post(url, {"reason": ""})
    assert invalid.status_code == 302
    assert any("Escribí un motivo para dejar de usar" in m for m in _messages(invalid))

    def _raise(**_kwargs: object) -> None:
        raise ValidationError("No se pudo bloquear el canal.")

    monkeypatch.setattr("apps.contacts.views.create_manual_restriction", _raise)
    service_error = client.post(url, {"reason": "Motivo válido"})
    assert service_error.status_code == 302
    assert any("No se pudo bloquear" in m for m in _messages(service_error))
    assert not CommunicationRestriction.objects.filter(email_address=email).exists()


@pytest.mark.django_db
def test_contact_restriction_revoke_rejects_blank_reasons_and_non_manual_restrictions(
    client: Client,
    owner: User,
) -> None:
    contact = create_manual_contact(actor=owner, email="rebote@cliente.example")
    email = contact.preferred_email
    assert email is not None
    bounce = CommunicationRestriction.objects.create(
        workspace=owner.membership.workspace,
        scope=CommunicationRestriction.Scope.EMAIL,
        kind=CommunicationRestriction.Kind.BOUNCE,
        email_address=email,
        source="gmail_bounce",
        evidence="InboundMessage:test-bounce",
    )
    client.force_login(owner)
    url = reverse("contact-restriction-revoke", args=(contact.pk, bounce.pk))

    invalid = client.post(url, {"reason": ""})
    assert invalid.status_code == 302
    assert any("Explicá por qué se vuelve a habilitar" in m for m in _messages(invalid))

    non_manual = client.post(url, {"reason": "Ya no rebota"})
    assert non_manual.status_code == 302
    assert any("restricciones manuales" in m for m in _messages(non_manual))
    bounce.refresh_from_db()
    assert bounce.is_active
