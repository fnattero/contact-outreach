from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from email.message import EmailMessage
from email.policy import SMTP

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record_event
from apps.integrations.contracts import GmailProvider, GmailSendRequest, ProviderError
from apps.integrations.factory import get_gmail_provider
from apps.integrations.gmail import GMAIL_SCOPES
from apps.mailbox.crypto import decrypt_token, encrypt_token
from apps.mailbox.models import GmailConnection


def oauth_material() -> tuple[str, str, str]:
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    return state, verifier, challenge


def oauth_redirect_uri(request_uri: str) -> str:
    return settings.GMAIL_OAUTH_REDIRECT_URI or request_uri


def authorization_url(*, state: str, verifier: str, challenge: str, redirect_uri: str) -> str:
    del verifier
    provider = get_gmail_provider(code_challenge=challenge)
    return provider.authorization_url(state, redirect_uri)


@transaction.atomic
def connect_gmail(
    *,
    owner: User,
    code: str,
    verifier: str,
    redirect_uri: str,
) -> GmailConnection:
    provider = get_gmail_provider(code_verifier=verifier)
    data = provider.exchange_code(code, redirect_uri)
    domain = data.email.rsplit("@", 1)[-1].casefold()
    if settings.GMAIL_PROVIDER == "api" and domain not in {"gmail.com", "googlemail.com"}:
        try:
            provider.revoke()
        finally:
            raise ValidationError("La cuenta conectada debe ser una cuenta personal de Gmail.")
    granted = set(data.scopes)
    expected = set(GMAIL_SCOPES)
    if granted != expected:
        try:
            provider.revoke()
        finally:
            raise ValidationError("Google no concedió exactamente los scopes Gmail requeridos.")
    if not data.history_id:
        try:
            provider.revoke()
        finally:
            raise ValidationError("Gmail no devolvió el historyId inicial requerido.")
    encrypted = encrypt_token(data.refresh_token)
    connection, _ = GmailConnection.objects.select_for_update().update_or_create(
        owner=owner,
        defaults={
            "email": data.email,
            "scopes": sorted(granted),
            "refresh_token_encrypted": encrypted,
            "history_id": data.history_id,
            "status": GmailConnection.Status.CONNECTED,
            "last_tested_at": None,
            "error": "",
            "token_version": 1,
        },
    )
    record_event(
        action="gmail.connected",
        entity=connection,
        actor=owner,
        after={"email": connection.email, "scopes": connection.scopes},
    )
    return connection


def provider_for_connection(
    connection: GmailConnection, *, persist_fake: bool = False
) -> GmailProvider:
    return get_gmail_provider(
        refresh_token=decrypt_token(connection.refresh_token_encrypted),
        persist_fake=persist_fake,
    )


@transaction.atomic
def test_gmail_connection(*, owner: User) -> GmailConnection:
    if settings.SEND_MODE != "live" or settings.SEND_KILL_SWITCH:
        raise ValidationError("La prueba Gmail está bloqueada por SEND_MODE o el kill switch.")
    connection = GmailConnection.objects.select_for_update().get(owner=owner)
    if connection.status != GmailConnection.Status.CONNECTED:
        raise ValidationError("Conectá Gmail antes de enviar la prueba.")
    provider = provider_for_connection(connection, persist_fake=True)
    account = provider.test_connection()
    if account.email.casefold() != connection.email.casefold():
        raise ValidationError("La cuenta autenticada no coincide con la conexión guardada.")

    # The recipient is deliberately not accepted from HTTP input.
    message_id = f"<{uuid.uuid4().hex}@contact-outreach.local>"
    message = EmailMessage(policy=SMTP)
    message["From"] = connection.email
    message["To"] = connection.email
    message["Subject"] = "Prueba de conexión · Contact Outreach"
    message["Date"] = timezone.now()
    message["Message-ID"] = message_id
    message.set_content(
        "Esta prueba confirma que Contact Outreach puede enviar desde esta misma cuenta Gmail."
    )
    provider.send(
        GmailSendRequest(
            recipient=connection.email,
            raw_message=message.as_bytes(policy=SMTP),
            message_id=message_id,
            correlation_id=str(connection.pk),
            idempotency_key=f"gmail-test:{message_id}",
        )
    )
    connection.last_tested_at = timezone.now()
    connection.error = ""
    connection.save(update_fields=("last_tested_at", "error", "updated_at"))
    record_event(
        action="gmail.test_sent",
        entity=connection,
        actor=owner,
        after={"recipient": connection.email},
    )
    return connection


@transaction.atomic
def disconnect_gmail(*, owner: User) -> GmailConnection:
    connection = GmailConnection.objects.select_for_update().get(owner=owner)
    if connection.refresh_token_encrypted:
        try:
            provider_for_connection(connection).revoke()
        except ProviderError:
            # Local revocation is still mandatory; the remote error remains auditable.
            connection.error = "Google no confirmó la revocación remota."
    connection.status = GmailConnection.Status.DISCONNECTED
    connection.refresh_token_encrypted = ""
    connection.scopes = []
    connection.history_id = ""
    connection.last_tested_at = None
    connection.save(
        update_fields=(
            "status",
            "refresh_token_encrypted",
            "scopes",
            "history_id",
            "last_tested_at",
            "error",
            "updated_at",
        )
    )
    record_event(
        action="gmail.disconnected",
        entity=connection,
        actor=owner,
        after={"status": connection.status},
    )
    return connection
