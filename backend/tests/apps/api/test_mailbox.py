from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.campaigns.models import Campaign, OutboundMessage
from apps.catalogs.services import create_catalog
from apps.mailbox.models import GmailConnection, InboundMessage


@pytest.mark.django_db
def test_mailbox_and_outbound_endpoints_require_authentication(owner: User) -> None:
    anonymous = Client()
    for name in ("api-inbound-messages", "api-outbound-messages"):
        response = anonymous.get(reverse(name))
        assert response.status_code == 401
        assert response.json()["code"] == "authentication_required"

    client = Client()
    client.force_login(owner)
    assert client.get(reverse("api-inbound-messages")).status_code == 200
    assert client.get(reverse("api-outbound-messages")).status_code == 200


@pytest.mark.django_db
def test_inbound_list_and_thread_serialize_messages(owner: User) -> None:
    connection = GmailConnection.objects.create(
        workspace=owner.membership.workspace,
        owner=owner,
        email="owner@example.invalid",
        scopes=["gmail.readonly"],
        refresh_token_encrypted="encrypted-refresh-token",
        status=GmailConnection.Status.CONNECTED,
    )
    received_at = timezone.now()
    inbound = InboundMessage.objects.create(
        connection=connection,
        gmail_message_id="gmail-message-1",
        gmail_thread_id="gmail-thread-1",
        sender="prospect@example.invalid",
        subject="Consulta comercial",
        external_at=received_at,
        received_at=received_at,
        body_text="Hola, me interesa recibir información.",
        classification=InboundMessage.Classification.INTERESTED,
        is_human=True,
    )
    client = Client()
    client.force_login(owner)

    listing = client.get(reverse("api-inbound-messages"), {"classification": "INTERESTED"})
    assert listing.status_code == 200
    row = listing.json()["data"][0]
    assert row["id"] == str(inbound.pk)
    assert row["body_preview"] == inbound.body_text
    assert row["classification_label"] == "Interesado"
    assert listing.json()["meta"]["total"] == 1

    thread = client.get(reverse("api-inbound-message-thread", args=(inbound.pk,)))
    assert thread.status_code == 200
    thread_data = thread.json()["data"]
    assert thread_data["inbound"]["body_text"] == inbound.body_text
    assert thread_data["timeline"] == [
        {
            "direction": "inbound",
            "at": received_at.isoformat(),
            "sender": inbound.sender,
            "body_text": inbound.body_text,
            "classification": inbound.classification,
        }
    ]


@pytest.mark.django_db
def test_outbound_api_exposes_admin_details_but_vendor_only_sees_sent(
    owner: User, private_catalog_dir
) -> None:
    catalog = create_catalog(
        name="Outbound catalog",
        upload=SimpleUploadedFile(
            "outbound.pdf",
            b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
            content_type="application/pdf",
        ),
        actor=owner,
    )
    campaign = Campaign.objects.create(
        workspace=owner.membership.workspace,
        name="API campaign",
        catalog=catalog,
        created_by=owner,
    )
    prepared = OutboundMessage.objects.create(
        campaign=campaign,
        recipient="draft@example.invalid",
        recipient_normalized="draft@example.invalid",
        subject="Borrador",
        body_text="Contenido privado",
        idempotency_key="draft-api-message",
        catalog=catalog,
        catalog_version=catalog.version,
        delivery_mode=Campaign.DeliveryMode.DRY_RUN,
        state=OutboundMessage.State.PREPARED,
    )
    sent_at = timezone.now()
    sent = OutboundMessage.objects.create(
        campaign=campaign,
        recipient="sent@example.invalid",
        recipient_normalized="sent@example.invalid",
        subject="Enviado",
        body_text="Contenido enviado",
        idempotency_key="sent-api-message",
        catalog=catalog,
        catalog_version=catalog.version,
        delivery_mode=Campaign.DeliveryMode.DRY_RUN,
        state=OutboundMessage.State.SENT,
        sent_at=sent_at,
        message_id="<sent-api-message@example.invalid>",
        gmail_thread_id="gmail-thread-sent",
    )
    admin = Client()
    admin.force_login(owner)

    admin_listing = admin.get(reverse("api-outbound-messages"))
    assert admin_listing.status_code == 200
    assert {row["id"] for row in admin_listing.json()["data"]} == {
        str(prepared.pk),
        str(sent.pk),
    }
    detail = admin.get(reverse("api-outbound-message-detail", args=(sent.pk,)))
    assert detail.status_code == 200
    assert detail.json()["data"]["gmail_thread_id"] == sent.gmail_thread_id

    vendor = User.objects.create_user(username="mailbox-vendor", password="vendor-password-1234")
    vendor_client = Client()
    vendor_client.force_login(vendor)
    vendor_listing = vendor_client.get(reverse("api-outbound-messages"))
    assert vendor_listing.status_code == 200
    assert [row["id"] for row in vendor_listing.json()["data"]] == [str(sent.pk)]
    assert "gmail_thread_id" not in vendor_listing.json()["data"][0]

    assert (
        vendor_client.get(reverse("api-outbound-message-detail", args=(prepared.pk,))).status_code
        == 400
    )
