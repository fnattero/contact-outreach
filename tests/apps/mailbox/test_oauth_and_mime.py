from __future__ import annotations

from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.test import Client, override_settings
from django.urls import reverse

from apps.integrations.gmail import GMAIL_SCOPES
from apps.mailbox.crypto import decrypt_token, encrypt_token
from apps.mailbox.mime import build_message, deterministic_message_id
from apps.mailbox.models import FakeGmailMessage, GmailConnection


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


def test_token_crypto_rejects_missing_key_empty_and_invalid_ciphertext() -> None:
    with pytest.raises(ValidationError, match="refresh token"):
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
