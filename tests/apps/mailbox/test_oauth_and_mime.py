from __future__ import annotations

from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.test import Client, override_settings
from django.urls import reverse

from apps.integrations.contracts import GmailConnectionData, ProviderError
from apps.integrations.gmail import GMAIL_SCOPES
from apps.mailbox import services as mailbox_services
from apps.mailbox.crypto import decrypt_token, encrypt_token
from apps.mailbox.mime import (
    MAX_SERIALIZED_MIME_BYTES,
    MAX_SOURCE_PDF_BYTES,
    PdfAttachment,
    build_message,
    deterministic_message_id,
)
from apps.mailbox.models import FakeGmailMessage, GmailConnection
from apps.mailbox.services import (
    connect_gmail,
    disconnect_gmail,
)
from apps.mailbox.services import (
    test_gmail_connection as run_gmail_connection_test,
)


def test_mime_is_plain_text_with_exact_pdf_and_stable_message_id() -> None:
    message_id = deterministic_message_id("message:stable")
    assert message_id == deterministic_message_id("message:stable")
    from django.utils import timezone

    pdf = b"%PDF-1.4\nfixture\n%%EOF"
    built = build_message(
        sender="owner@gmail.com",
        recipient="ventas@example.com",
        subject="PUBLICIDAD - Consulta",
        body_text="Mensaje en texto plano.",
        message_id=message_id,
        sent_at=timezone.now(),
        campaign_header="opaque-campaign",
        message_header="opaque-message",
        pdf_bytes=pdf,
        pdf_filename="catalogo.pdf",
    )
    parsed = BytesParser(policy=policy.default).parsebytes(built.raw)
    assert parsed["To"] == "ventas@example.com"
    assert parsed["Message-ID"] == message_id
    assert parsed["Cc"] is None
    assert parsed["Bcc"] is None
    assert parsed.is_multipart()
    assert parsed.get_body(preferencelist=("plain",)).get_content().strip() == (
        "Mensaje en texto plano."
    )
    attachments = list(parsed.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_content_type() == "application/pdf"
    assert attachments[0].get_payload(decode=True) == pdf

    renamed = build_message(
        sender="owner@gmail.com",
        recipient="ventas@example.com",
        subject="Asunto",
        body_text="Texto",
        message_id=message_id,
        sent_at=timezone.now(),
        campaign_header="campaign",
        message_header="message",
        pdf_bytes=pdf,
        pdf_filename="unsafe.txt",
    )
    renamed_parsed = BytesParser(policy=policy.default).parsebytes(renamed.raw)
    assert next(renamed_parsed.iter_attachments()).get_filename() == "catalogo.pdf"
    with pytest.raises(ValidationError, match="remitente"):
        build_message(
            sender="bad\nsender",
            recipient="ventas@example.com",
            subject="Asunto",
            body_text="Texto",
            message_id=message_id,
            sent_at=timezone.now(),
            campaign_header="campaign",
            message_header="message",
        )


def test_mime_preserves_ordered_pdfs_and_enforces_source_and_final_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from django.utils import timezone

    from apps.mailbox import mime as mime_module

    first = b"%PDF-" + b"a" * (8 * 1024 * 1024 - 5)
    second = b"%PDF-" + b"b" * (MAX_SOURCE_PDF_BYTES - len(first) - 5)
    built = build_message(
        sender="owner@gmail.com",
        recipient="ventas@example.com",
        subject="Propuesta",
        body_text="Adjuntamos ambos catálogos.",
        message_id=deterministic_message_id("ordered-pdfs"),
        sent_at=timezone.now(),
        campaign_header="campaign",
        message_header="message",
        pdf_attachments=(
            PdfAttachment(filename="primero.pdf", content=first),
            PdfAttachment(filename="segundo.pdf", content=second),
        ),
    )
    parsed = BytesParser(policy=policy.default).parsebytes(built.raw)
    attachments = list(parsed.iter_attachments())
    assert [item.get_filename() for item in attachments] == ["primero.pdf", "segundo.pdf"]
    assert [item.get_payload(decode=True)[:6] for item in attachments] == [b"%PDF-a", b"%PDF-b"]
    assert built.size == len(built.raw) <= MAX_SERIALIZED_MIME_BYTES

    with pytest.raises(ValidationError, match="17 MiB"):
        build_message(
            sender="owner@gmail.com",
            recipient="ventas@example.com",
            subject="Propuesta",
            body_text="Adjuntos demasiado grandes.",
            message_id=deterministic_message_id("too-many-source-bytes"),
            sent_at=timezone.now(),
            campaign_header="campaign",
            message_header="message",
            pdf_attachments=(
                PdfAttachment(filename="primero.pdf", content=first),
                PdfAttachment(filename="segundo.pdf", content=second + b"x"),
            ),
        )

    monkeypatch.setattr(mime_module, "MAX_SERIALIZED_MIME_BYTES", 100)
    with pytest.raises(ValidationError, match="24 MiB"):
        build_message(
            sender="owner@gmail.com",
            recipient="ventas@example.com",
            subject="Propuesta",
            body_text="Correo serializado demasiado grande.",
            message_id=deterministic_message_id("raw-too-large"),
            sent_at=timezone.now(),
            campaign_header="campaign",
            message_header="message",
        )


def test_build_message_rejects_conflicting_or_malformed_pdf_attachments() -> None:
    from django.utils import timezone

    from apps.mailbox.mime import MAX_PDF_BYTES

    common_kwargs = {
        "sender": "owner@gmail.com",
        "recipient": "ventas@example.com",
        "subject": "Propuesta",
        "body_text": "Texto",
        "message_id": deterministic_message_id("edge-case"),
        "sent_at": timezone.now(),
        "campaign_header": "campaign",
        "message_header": "message",
    }

    with pytest.raises(ValidationError, match="una sola colección"):
        build_message(
            **common_kwargs,
            pdf_bytes=b"%PDF-1.4\nfixture\n%%EOF",
            pdf_attachments=(PdfAttachment(filename="a.pdf", content=b"%PDF-1.4\n%%EOF"),),
        )

    with pytest.raises(ValidationError, match="está vacío"):
        build_message(
            **common_kwargs,
            pdf_attachments=(PdfAttachment(filename="empty.pdf", content=b""),),
        )

    with pytest.raises(ValidationError, match="15 MiB"):
        build_message(
            **common_kwargs,
            pdf_attachments=(
                PdfAttachment(filename="huge.pdf", content=b"%PDF-" + b"a" * MAX_PDF_BYTES),
            ),
        )

    with pytest.raises(ValidationError, match="no es un PDF válido"):
        build_message(
            **common_kwargs,
            pdf_attachments=(PdfAttachment(filename="fake.pdf", content=b"not a pdf"),),
        )


def test_build_reply_message_validates_headers() -> None:
    from django.utils import timezone

    from apps.mailbox.mime import build_reply_message

    now = timezone.now()
    with pytest.raises(ValidationError, match="remitente o destinatario"):
        build_reply_message(
            sender="bad\nsender",
            recipient="ventas@example.com",
            subject="Re: Propuesta",
            body_text="Texto",
            message_id=deterministic_message_id("reply-bad-sender"),
            sent_at=now,
            thread_references=(),
            in_reply_to="<root@example.com>",
            campaign_header="campaign",
            message_header="message",
        )

    with pytest.raises(ValidationError, match="identificador del mensaje anterior"):
        build_reply_message(
            sender="owner@gmail.com",
            recipient="ventas@example.com",
            subject="Re: Propuesta",
            body_text="Texto",
            message_id=deterministic_message_id("reply-missing-in-reply-to"),
            sent_at=now,
            thread_references=(),
            in_reply_to="",
            campaign_header="campaign",
            message_header="message",
        )


def test_token_crypto_rejects_missing_key_empty_and_invalid_ciphertext() -> None:
    with pytest.raises(ValidationError, match="credencial necesaria para renovar el acceso"):
        encrypt_token("")
    with pytest.raises(ValidationError, match="descifrar"):
        decrypt_token("not-a-token")
    with override_settings(FIELD_ENCRYPTION_KEY=""):
        with pytest.raises(ImproperlyConfigured, match="FIELD_ENCRYPTION_KEY"):
            encrypt_token("secret")


@pytest.mark.django_db
def test_fake_oauth_connect_test_only_self_and_disconnect(
    client: Client, owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    client.force_login(owner)
    begin = client.post(reverse("gmail-connect"))
    assert begin.status_code == 302
    callback = client.get(begin["Location"])
    assert callback.status_code == 302

    connection = GmailConnection.objects.get(owner=owner)
    assert set(connection.scopes) == set(GMAIL_SCOPES)
    assert "fake-refresh-token" not in connection.refresh_token_encrypted
    assert decrypt_token(connection.refresh_token_encrypted) == "fake-refresh-token"

    blocked = client.post(reverse("gmail-test"))
    assert blocked.status_code == 302
    assert not FakeGmailMessage.objects.exists()

    with override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False):
        tested = client.post(
            reverse("gmail-test"),
            {"recipient": "attacker@example.com"},
        )
    assert tested.status_code == 302
    connection.refresh_from_db()
    assert connection.last_tested_at is not None
    fake = FakeGmailMessage.objects.get()
    assert fake.recipient == connection.email
    assert fake.recipient != "attacker@example.com"

    disconnected = client.post(reverse("gmail-disconnect"))
    assert disconnected.status_code == 302
    connection.refresh_from_db()
    assert connection.status == GmailConnection.Status.DISCONNECTED
    assert connection.refresh_token_encrypted == ""


@pytest.mark.django_db
def test_gmail_mutations_require_login_and_post(owner: User) -> None:
    anonymous = Client()
    assert anonymous.get(reverse("gmail-settings")).status_code == 302
    logged_in = Client()
    logged_in.force_login(owner)
    assert logged_in.get(reverse("gmail-connect")).status_code == 405
    assert logged_in.get(reverse("gmail-test")).status_code == 405
    assert logged_in.get(reverse("gmail-disconnect")).status_code == 405


class _StubExchangeProvider:
    def __init__(self, data: GmailConnectionData) -> None:
        self.data = data
        self.revoked = False

    def exchange_code(self, code: str, redirect_uri: str) -> GmailConnectionData:
        del code, redirect_uri
        return self.data

    def revoke(self) -> None:
        self.revoked = True


class _StubApiRuntime:
    gmail_provider = "api"


@pytest.mark.django_db
def test_connect_gmail_rejects_non_personal_domain_when_provider_is_api(
    owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubExchangeProvider(
        GmailConnectionData(
            email="user@company-domain.example",
            refresh_token="fake-refresh-token",
            scopes=GMAIL_SCOPES,
            history_id="1",
        )
    )
    monkeypatch.setattr(
        mailbox_services, "runtime_integration_configuration", lambda owner_id: _StubApiRuntime()
    )
    monkeypatch.setattr(mailbox_services, "get_gmail_provider", lambda **kwargs: stub)

    with pytest.raises(ValidationError, match="cuenta personal de Gmail"):
        connect_gmail(owner=owner, code="c", verifier="v", redirect_uri="https://cb.example")

    assert stub.revoked is True
    assert not GmailConnection.objects.filter(owner=owner).exists()


@pytest.mark.django_db
def test_connect_gmail_rejects_scope_mismatch(owner: User, monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubExchangeProvider(
        GmailConnectionData(
            email="user@gmail.com",
            refresh_token="fake-refresh-token",
            scopes=("https://www.googleapis.com/auth/gmail.readonly",),
            history_id="1",
        )
    )
    monkeypatch.setattr(mailbox_services, "get_gmail_provider", lambda **kwargs: stub)

    with pytest.raises(ValidationError, match="no concedió exactamente los permisos"):
        connect_gmail(owner=owner, code="c", verifier="v", redirect_uri="https://cb.example")

    assert stub.revoked is True
    assert not GmailConnection.objects.filter(owner=owner).exists()


@pytest.mark.django_db
def test_connect_gmail_rejects_missing_history_id(
    owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubExchangeProvider(
        GmailConnectionData(
            email="user@gmail.com",
            refresh_token="fake-refresh-token",
            scopes=GMAIL_SCOPES,
            history_id="",
        )
    )
    monkeypatch.setattr(mailbox_services, "get_gmail_provider", lambda **kwargs: stub)

    with pytest.raises(ValidationError, match="no devolvió el identificador inicial"):
        connect_gmail(owner=owner, code="c", verifier="v", redirect_uri="https://cb.example")

    assert stub.revoked is True
    assert not GmailConnection.objects.filter(owner=owner).exists()


@pytest.mark.django_db
def test_test_gmail_connection_requires_connected_status(owner: User) -> None:
    GmailConnection.objects.create(
        owner=owner,
        email="owner@example.invalid",
        scopes=list(GMAIL_SCOPES),
        refresh_token_encrypted=encrypt_token("fake-refresh-token"),
        history_id="1",
        status=GmailConnection.Status.DISCONNECTED,
    )
    with override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False):
        with pytest.raises(ValidationError, match="Conectá Gmail antes de enviar"):
            run_gmail_connection_test(owner=owner)


@pytest.mark.django_db
def test_test_gmail_connection_rejects_account_email_mismatch(owner: User) -> None:
    GmailConnection.objects.create(
        owner=owner,
        email="someone-else@example.invalid",
        scopes=list(GMAIL_SCOPES),
        refresh_token_encrypted=encrypt_token("fake-refresh-token"),
        history_id="1",
        status=GmailConnection.Status.CONNECTED,
    )
    with override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False):
        with pytest.raises(ValidationError, match="no coincide con la conexión guardada"):
            run_gmail_connection_test(owner=owner)
    assert not FakeGmailMessage.objects.exists()


@pytest.mark.django_db
def test_disconnect_gmail_skips_revoke_when_no_refresh_token(owner: User) -> None:
    GmailConnection.objects.create(
        owner=owner,
        email="owner@example.invalid",
        status=GmailConnection.Status.CONNECTED,
        refresh_token_encrypted="",
    )
    connection = disconnect_gmail(owner=owner)
    assert connection.status == GmailConnection.Status.DISCONNECTED
    assert connection.error == ""


@pytest.mark.django_db
def test_disconnect_gmail_records_error_when_remote_revoke_fails(
    owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    GmailConnection.objects.create(
        owner=owner,
        email="owner@example.invalid",
        status=GmailConnection.Status.CONNECTED,
        refresh_token_encrypted=encrypt_token("fake-refresh-token"),
    )

    class _FailingProvider:
        def revoke(self) -> None:
            raise ProviderError("remote revoke failed")

    monkeypatch.setattr(
        mailbox_services, "provider_for_connection", lambda *a, **k: _FailingProvider()
    )

    connection = disconnect_gmail(owner=owner)

    assert connection.status == GmailConnection.Status.DISCONNECTED
    assert "no confirmó la revocación remota" in connection.error
