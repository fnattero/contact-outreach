from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Membership
from apps.audit.services import record_event
from apps.automation.models import HumanTask, NotificationDelivery
from apps.compliance.services import normalize_email
from apps.configuration.integrations import redact_provider_error
from apps.integrations.contracts import (
    AmbiguousProviderError,
    AuthenticationError,
    GmailProvider,
    GmailSendRequest,
    PermanentProviderError,
    ProviderError,
    RetryableProviderError,
    ValidationProviderError,
)
from apps.integrations.gmail import GMAIL_SCOPES
from apps.mailbox.mime import build_message, deterministic_message_id
from apps.mailbox.models import GmailConnection
from apps.mailbox.services import provider_for_connection

NOTIFICATION_SUBJECT = "Hay una conversación que necesita revisión"
STALE_SENDING_AFTER = timedelta(minutes=2)


def _secure_base_url() -> str:
    value = str(settings.PUBLIC_BASE_URL).strip().rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return ""
    return value


def _task_url(task: HumanTask) -> str:
    base = _secure_base_url()
    if not base:
        return ""
    return f"{base}/contactos/{task.contact_id}/#tarea-{task.pk}"


@transaction.atomic
def ensure_notification_deliveries(task_id: uuid.UUID | str) -> tuple[uuid.UUID, ...]:
    task = HumanTask.objects.select_related("workspace").get(pk=task_id)
    url = _task_url(task)
    admins = User.objects.filter(
        is_active=True,
        email__gt="",
        membership__workspace_id=task.workspace_id,
        membership__role=Membership.Role.ADMIN,
    ).order_by("pk")
    pending: list[uuid.UUID] = []
    for admin in admins:
        key = f"human-task-alert:{task.pk}:admin:{admin.pk}"
        delivery, created = NotificationDelivery.objects.get_or_create(
            task=task,
            recipient=admin,
            channel="GMAIL",
            defaults={
                "subject": NOTIFICATION_SUBJECT,
                "secure_url": url,
                "idempotency_key": key,
                "message_id": deterministic_message_id(key),
                "state": (
                    NotificationDelivery.State.PENDING if url else NotificationDelivery.State.FAILED
                ),
                "error": "" if url else "PUBLIC_BASE_URL debe ser una URL HTTPS válida.",
            },
        )
        if created:
            record_event(
                action="human_task.notification_created",
                entity=delivery,
                actor=None,
                after={"channel": delivery.channel, "state": delivery.state},
            )
        if delivery.state == NotificationDelivery.State.PENDING:
            pending.append(delivery.pk)
    if pending:
        transaction.on_commit(lambda: _dispatch_notification_ids(tuple(pending)))
    return tuple(pending)


def _dispatch_notification_ids(delivery_ids: tuple[uuid.UUID, ...]) -> None:
    from apps.automation.tasks import deliver_notification_task

    for delivery_id in delivery_ids:
        deliver_notification_task.delay(str(delivery_id))


def _notification_preflight_error(
    delivery: NotificationDelivery,
    connection: GmailConnection | None,
) -> str:
    if delivery.task.status != HumanTask.Status.OPEN:
        return "La tarea ya no está abierta."
    recipient = delivery.recipient
    if not recipient.is_active or not recipient.email.strip():
        return "El administrador ya no tiene un email activo."
    membership = Membership.objects.filter(user=recipient).first()
    if (
        membership is None
        or membership.workspace_id != delivery.task.workspace_id
        or membership.role != Membership.Role.ADMIN
    ):
        return "El destinatario ya no es administrador de este espacio."
    try:
        normalize_email(recipient.email)
    except ValidationError:
        return "El email del administrador no es válido."
    expected_url = _task_url(delivery.task)
    if not expected_url or delivery.secure_url != expected_url:
        return "El enlace seguro del aviso no está disponible."
    if settings.SEND_MODE != "live" or settings.SEND_KILL_SWITCH:
        return "El envío en vivo está desactivado por la configuración general."
    if connection is None or not connection.is_ready:
        return "Gmail no está conectado y probado."
    if set(connection.scopes) != set(GMAIL_SCOPES):
        return "La conexión Gmail no tiene exactamente los permisos esperados."
    return ""


@transaction.atomic
def _prepare_notification(
    delivery_id: uuid.UUID | str,
) -> tuple[uuid.UUID, bytes, str, str, str] | str:
    delivery = (
        NotificationDelivery.objects.select_for_update()
        .select_related("task__workspace", "recipient")
        .get(pk=delivery_id)
    )
    if delivery.state in {
        NotificationDelivery.State.SENT,
        NotificationDelivery.State.FAILED,
        NotificationDelivery.State.SENDING,
        NotificationDelivery.State.RECONCILING,
    }:
        return delivery.state
    connection = (
        GmailConnection.objects.select_for_update()
        .filter(workspace_id=delivery.task.workspace_id)
        .first()
    )
    if error := _notification_preflight_error(delivery, connection):
        delivery.state = NotificationDelivery.State.FAILED
        delivery.error = error
        delivery.save(update_fields=("state", "error", "updated_at"))
        return delivery.state
    assert connection is not None
    body = f"Hay una conversación que necesita revisión.\n\n{delivery.secure_url}"
    built = build_message(
        sender=connection.email,
        recipient=delivery.recipient.email,
        subject=NOTIFICATION_SUBJECT,
        body_text=body,
        message_id=delivery.message_id,
        sent_at=delivery.created_at,
        campaign_header="human-attention",
        message_header=str(delivery.pk),
    )
    delivery.state = NotificationDelivery.State.SENDING
    delivery.attempts += 1
    delivery.error = ""
    delivery.save(update_fields=("state", "attempts", "error", "updated_at"))
    return (
        connection.pk,
        built.raw,
        delivery.recipient.email,
        delivery.message_id,
        delivery.idempotency_key,
    )


@transaction.atomic
def _finish_notification(
    delivery_id: uuid.UUID | str,
    *,
    state: str,
    error: str,
    result_message_id: str = "",
) -> str:
    delivery = NotificationDelivery.objects.select_for_update().get(pk=delivery_id)
    if delivery.state == NotificationDelivery.State.SENT:
        return delivery.state
    delivery.state = state
    delivery.error = error[:500]
    if state == NotificationDelivery.State.SENT:
        delivery.gmail_message_id = result_message_id
        delivery.sent_at = timezone.now()
    delivery.save(update_fields=("state", "error", "gmail_message_id", "sent_at", "updated_at"))
    record_event(
        action=(
            "human_task.notification_sent"
            if state == NotificationDelivery.State.SENT
            else "human_task.notification_reconciling"
            if state == NotificationDelivery.State.RECONCILING
            else "human_task.notification_retry_scheduled"
            if state == NotificationDelivery.State.PENDING
            else "human_task.notification_failed"
        ),
        entity=delivery,
        actor=None,
        after={"state": state},
    )
    return delivery.state


def deliver_notification(
    delivery_id: uuid.UUID | str,
    *,
    provider: GmailProvider | None = None,
) -> str:
    try:
        prepared = _prepare_notification(delivery_id)
    except ValidationError as exc:
        return _finish_notification(
            delivery_id,
            state=NotificationDelivery.State.FAILED,
            error="; ".join(exc.messages),
        )
    if isinstance(prepared, str):
        return prepared
    connection_id, raw, recipient, message_id, idempotency_key = prepared
    connection = GmailConnection.objects.get(pk=connection_id)
    try:
        active_provider = provider or provider_for_connection(connection, persist_fake=True)
        result = active_provider.send(
            GmailSendRequest(
                recipient=recipient,
                raw_message=raw,
                message_id=message_id,
                correlation_id=str(delivery_id),
                idempotency_key=idempotency_key,
            )
        )
    except (ImproperlyConfigured, ValidationError) as exc:
        return _finish_notification(
            delivery_id,
            state=NotificationDelivery.State.FAILED,
            error=redact_provider_error(exc, owner_id=connection.owner_id),
        )
    except (AmbiguousProviderError, RetryableProviderError) as exc:
        return _finish_notification(
            delivery_id,
            state=NotificationDelivery.State.RECONCILING,
            error=redact_provider_error(exc, owner_id=connection.owner_id),
        )
    except (AuthenticationError, PermanentProviderError, ValidationProviderError) as exc:
        return _finish_notification(
            delivery_id,
            state=NotificationDelivery.State.FAILED,
            error=redact_provider_error(exc, owner_id=connection.owner_id),
        )
    except ProviderError as exc:
        return _finish_notification(
            delivery_id,
            state=NotificationDelivery.State.RECONCILING,
            error=redact_provider_error(exc, owner_id=connection.owner_id),
        )
    return _finish_notification(
        delivery_id,
        state=NotificationDelivery.State.SENT,
        error="",
        result_message_id=result.message_id,
    )


def reconcile_notification(
    delivery_id: uuid.UUID | str,
    *,
    provider: GmailProvider | None = None,
) -> str:
    delivery = NotificationDelivery.objects.select_related("task", "recipient").get(pk=delivery_id)
    if delivery.state not in {
        NotificationDelivery.State.SENDING,
        NotificationDelivery.State.RECONCILING,
    }:
        return delivery.state
    connection = GmailConnection.objects.filter(workspace_id=delivery.task.workspace_id).first()
    if connection is None:
        return _finish_notification(
            delivery.pk,
            state=NotificationDelivery.State.FAILED,
            error="Gmail no está conectado y probado.",
        )
    try:
        active_provider = provider or provider_for_connection(connection, persist_fake=True)
        result = active_provider.find_by_message_id(delivery.message_id)
    except (ImproperlyConfigured, ValidationError) as exc:
        return _finish_notification(
            delivery.pk,
            state=NotificationDelivery.State.FAILED,
            error=redact_provider_error(exc, owner_id=connection.owner_id),
        )
    except RetryableProviderError as exc:
        return _finish_notification(
            delivery.pk,
            state=NotificationDelivery.State.RECONCILING,
            error=redact_provider_error(exc, owner_id=connection.owner_id),
        )
    except ProviderError as exc:
        return _finish_notification(
            delivery.pk,
            state=NotificationDelivery.State.FAILED,
            error=redact_provider_error(exc, owner_id=connection.owner_id),
        )
    if result is None:
        return _finish_notification(
            delivery.pk,
            state=NotificationDelivery.State.FAILED,
            error=(
                "Gmail no confirmó el aviso después de un resultado ambiguo; "
                "no se reintentó para evitar un duplicado."
            ),
        )
    return _finish_notification(
        delivery.pk,
        state=NotificationDelivery.State.SENT,
        error="",
        result_message_id=result.message_id,
    )


def pending_notification_ids() -> tuple[uuid.UUID, ...]:
    return tuple(
        NotificationDelivery.objects.filter(state=NotificationDelivery.State.PENDING).values_list(
            "pk", flat=True
        )
    )


def recoverable_notification_ids(now: datetime | None = None) -> tuple[uuid.UUID, ...]:
    moment = now or timezone.now()
    return tuple(
        NotificationDelivery.objects.filter(
            state=NotificationDelivery.State.RECONCILING
        ).values_list("pk", flat=True)
    ) + tuple(
        NotificationDelivery.objects.filter(
            state=NotificationDelivery.State.SENDING,
            updated_at__lte=moment - STALE_SENDING_AFTER,
        ).values_list("pk", flat=True)
    )
