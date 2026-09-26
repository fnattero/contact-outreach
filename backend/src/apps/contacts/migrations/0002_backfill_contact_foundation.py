from __future__ import annotations

import hashlib
from email.utils import parseaddr

from django.db import migrations
from django.db.models import Max


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_email(value: object) -> str:
    _, parsed = parseaddr(str(value or ""))
    candidate = parsed.strip() or str(value or "").strip()
    if candidate.count("@") != 1:
        return ""
    local_part, domain = candidate.rsplit("@", 1)
    local_part = local_part.strip()
    domain = domain.strip().rstrip(".")
    if not local_part or not domain:
        return ""
    try:
        normalized_domain = domain.encode("idna").decode("ascii").casefold()
    except UnicodeError:
        return ""
    return f"{local_part.casefold()}@{normalized_domain}"


def _identity_value(prospect, identity, kind: str) -> str:
    if kind == "BUSINESS_DOMAIN":
        return prospect.business_domain or ""
    if kind == "NAME_ADDRESS":
        if prospect.normalized_name and prospect.normalized_address:
            return f"{prospect.normalized_name}\x1f{prospect.normalized_address}"
        return ""
    if kind == "EMAIL":
        for prospect_email in prospect.emails.all():
            if _sha256(prospect_email.normalized_email) == identity.value_hash:
                return prospect_email.normalized_email
    return ""


def _identity_keys(prospect) -> list[tuple[str, str, str, str]]:
    provider = prospect.source_run.provider if prospect.source_run_id else ""
    keys: dict[tuple[str, str], tuple[str, str, str, str]] = {}
    for identity in prospect.identities.all():
        kind = identity.kind
        if kind == "PROVIDER_ID" and provider == "overture":
            kind = "GERS_ID"
        value = _identity_value(prospect, identity, identity.kind)
        keys[(kind, identity.value_hash)] = (kind, identity.value_hash, value, provider)
    if prospect.business_domain:
        value = prospect.business_domain.casefold()
        keys.setdefault(
            ("BUSINESS_DOMAIN", _sha256(value)),
            ("BUSINESS_DOMAIN", _sha256(value), value, ""),
        )
    if prospect.normalized_name and prospect.normalized_address:
        value = f"{prospect.normalized_name}\x1f{prospect.normalized_address}"
        keys.setdefault(
            ("NAME_ADDRESS", _sha256(value)),
            ("NAME_ADDRESS", _sha256(value), value, ""),
        )
    return list(keys.values())


def _merge_organizations(
    *,
    canonical,
    duplicates,
    Organization,
    OrganizationIdentity,
    EmailAddress,
    Prospect,
) -> None:
    changed_fields: list[str] = []
    for duplicate in duplicates:
        for field in (
            "name",
            "normalized_name",
            "address",
            "normalized_address",
            "website",
            "business_domain",
            "phone",
        ):
            if not getattr(canonical, field) and getattr(duplicate, field):
                setattr(canonical, field, getattr(duplicate, field))
                changed_fields.append(field)

        for identity in OrganizationIdentity.objects.filter(organization_id=duplicate.pk):
            conflict = OrganizationIdentity.objects.filter(
                workspace_id=canonical.workspace_id,
                kind=identity.kind,
                value_hash=identity.value_hash,
            ).exclude(pk=identity.pk)
            if conflict.exists():
                identity.delete()
            else:
                OrganizationIdentity.objects.filter(pk=identity.pk).update(
                    organization_id=canonical.pk
                )

        canonical_has_preferred = EmailAddress.objects.filter(
            organization_id=canonical.pk,
            is_preferred=True,
        ).exists()
        for email_address in EmailAddress.objects.filter(organization_id=duplicate.pk):
            if email_address.is_preferred and canonical_has_preferred:
                EmailAddress.objects.filter(pk=email_address.pk).update(is_preferred=False)
            elif email_address.is_preferred:
                canonical_has_preferred = True
            EmailAddress.objects.filter(pk=email_address.pk).update(organization_id=canonical.pk)
        Prospect.objects.filter(organization_id=duplicate.pk).update(organization_id=canonical.pk)
        Organization.objects.filter(pk=duplicate.pk).delete()
    if changed_fields:
        canonical.save(update_fields=tuple(dict.fromkeys((*changed_fields, "updated_at"))))


def _get_or_create_email(
    *,
    EmailAddress,
    workspace,
    organization,
    original_email: str,
    normalized_email: str,
    provenance: str,
    validity: str = "UNKNOWN",
    validated_at=None,
    invalid_reason: str = "",
    invalidated_at=None,
    source_url: str = "",
    source_content_hash: str = "",
    provider_order: int = 0,
    legacy_prospect_email_id=None,
    prefer: bool = False,
):
    if not normalized_email:
        return None
    email_address = EmailAddress.objects.filter(
        workspace_id=workspace.pk,
        normalized_email=normalized_email,
    ).first()
    if email_address is None:
        can_prefer = (
            prefer
            and not EmailAddress.objects.filter(
                organization_id=organization.pk,
                is_preferred=True,
            ).exists()
        )
        email_address = EmailAddress.objects.create(
            workspace_id=workspace.pk,
            organization_id=organization.pk,
            original_email=original_email or normalized_email,
            normalized_email=normalized_email,
            domain=normalized_email.rsplit("@", 1)[-1],
            is_preferred=can_prefer,
            provenance=provenance,
            source_url=source_url,
            source_content_hash=source_content_hash,
            provider_order=provider_order,
            validity=validity,
            validated_at=validated_at,
            invalid_reason=invalid_reason,
            invalidated_at=invalidated_at,
            legacy_prospect_email_id=legacy_prospect_email_id,
        )
        return email_address

    updates: dict[str, object] = {}
    if legacy_prospect_email_id and not email_address.legacy_prospect_email_id:
        updates["legacy_prospect_email_id"] = legacy_prospect_email_id
    if validity == "INVALID" and email_address.validity != "INVALID":
        updates.update(
            validity="INVALID",
            invalid_reason=invalid_reason,
            invalidated_at=invalidated_at,
        )
    elif validity == "VALID" and email_address.validity in {"UNKNOWN", "TRANSIENT"}:
        updates.update(validity="VALID", validated_at=validated_at)
    if prefer and email_address.organization_id == organization.pk:
        preferred_exists = EmailAddress.objects.filter(
            organization_id=organization.pk,
            is_preferred=True,
        ).exclude(pk=email_address.pk)
        if not preferred_exists.exists():
            updates["is_preferred"] = True
    if updates:
        EmailAddress.objects.filter(pk=email_address.pk).update(**updates)
        for field, value in updates.items():
            setattr(email_address, field, value)
    return email_address


def _organization_for_unmatched_email(
    *, Organization, EmailAddress, workspace, normalized_email: str, source_id
):
    existing = EmailAddress.objects.filter(
        workspace_id=workspace.pk,
        normalized_email=normalized_email,
    ).first()
    if existing is not None:
        return Organization.objects.get(pk=existing.organization_id), existing
    organization = Organization.objects.create(
        workspace_id=workspace.pk,
        source="SUPPRESSION",
        provenance={"legacy_suppression_id": str(source_id)},
    )
    email_address = _get_or_create_email(
        EmailAddress=EmailAddress,
        workspace=workspace,
        organization=organization,
        original_email=normalized_email,
        normalized_email=normalized_email,
        provenance="LEGACY_SUPPRESSION",
        prefer=True,
    )
    return organization, email_address


def backfill_contact_foundation(apps, schema_editor) -> None:
    del schema_editor
    Workspace = apps.get_model("accounts", "Workspace")
    Organization = apps.get_model("contacts", "Organization")
    OrganizationIdentity = apps.get_model("contacts", "OrganizationIdentity")
    EmailAddress = apps.get_model("contacts", "EmailAddress")
    Contact = apps.get_model("contacts", "Contact")
    CommunicationRestriction = apps.get_model("contacts", "CommunicationRestriction")
    Conversation = apps.get_model("contacts", "Conversation")
    CampaignEnrollment = apps.get_model("contacts", "CampaignEnrollment")
    Prospect = apps.get_model("prospects", "Prospect")
    OutboundMessage = apps.get_model("campaigns", "OutboundMessage")
    InboundMessage = apps.get_model("mailbox", "InboundMessage")
    GmailConnection = apps.get_model("mailbox", "GmailConnection")
    SuppressionEntry = apps.get_model("compliance", "SuppressionEntry")

    workspace = Workspace.objects.order_by("created_at", "pk").first()
    if workspace is None:
        workspace = Workspace.objects.create(singleton_key=1, name="Mi empresa")

    # Expand business identity first. No legacy row is deleted or rewritten.
    prospects = (
        Prospect.objects.select_related("source_run")
        .prefetch_related("identities", "emails")
        .order_by("created_at", "pk")
    )
    for prospect in prospects.iterator(chunk_size=500):
        keys = _identity_keys(prospect)
        candidate_ids: set[object] = set()
        for kind, value_hash, _, _ in keys:
            candidate_ids.update(
                OrganizationIdentity.objects.filter(
                    workspace_id=workspace.pk,
                    kind=kind,
                    value_hash=value_hash,
                ).values_list("organization_id", flat=True)
            )
        normalized_emails = list(prospect.emails.values_list("normalized_email", flat=True))
        candidate_ids.update(
            EmailAddress.objects.filter(
                workspace_id=workspace.pk,
                normalized_email__in=normalized_emails,
            ).values_list("organization_id", flat=True)
        )
        candidates = list(
            Organization.objects.filter(pk__in=candidate_ids).order_by("created_at", "pk")
        )
        if candidates:
            organization = candidates[0]
            _merge_organizations(
                canonical=organization,
                duplicates=candidates[1:],
                Organization=Organization,
                OrganizationIdentity=OrganizationIdentity,
                EmailAddress=EmailAddress,
                Prospect=Prospect,
            )
        else:
            organization = Organization.objects.create(
                workspace_id=workspace.pk,
                name=prospect.name,
                normalized_name=prospect.normalized_name,
                address=prospect.address,
                normalized_address=prospect.normalized_address,
                website=prospect.website,
                business_domain=prospect.business_domain,
                phone=prospect.phone,
                source="MIGRATION",
                provenance={"legacy_prospect_id": str(prospect.pk)},
            )

        for kind, value_hash, value, provider in keys:
            OrganizationIdentity.objects.get_or_create(
                workspace_id=workspace.pk,
                kind=kind,
                value_hash=value_hash,
                defaults={
                    "organization_id": organization.pk,
                    "value": value,
                    "provider": provider,
                    "provenance": {"legacy_prospect_id": str(prospect.pk)},
                },
            )

        for prospect_email in prospect.emails.all():
            validity = (
                "INVALID"
                if prospect_email.is_invalid or prospect_email.mx_status == "INVALID"
                else prospect_email.mx_status
                if prospect_email.mx_status in {"VALID", "TRANSIENT"}
                else "UNKNOWN"
            )
            email_address = _get_or_create_email(
                EmailAddress=EmailAddress,
                workspace=workspace,
                organization=organization,
                original_email=prospect_email.original_email,
                normalized_email=prospect_email.normalized_email,
                provenance=prospect_email.source,
                validity=validity,
                validated_at=prospect_email.mx_checked_at,
                invalid_reason=prospect_email.invalid_reason,
                invalidated_at=prospect_email.invalidated_at,
                source_url=prospect_email.source_url,
                source_content_hash=prospect_email.source_content_hash,
                provider_order=prospect_email.provider_order,
                legacy_prospect_email_id=prospect_email.pk,
                prefer=prospect_email.is_primary,
            )
            if email_address is not None and email_address.organization_id != organization.pk:
                # This can only occur in an inconsistent legacy dataset. Prefer the organization
                # already protected by the globally unique email and link this prospect to it.
                organization = Organization.objects.get(pk=email_address.organization_id)
        Prospect.objects.filter(pk=prospect.pk).update(organization_id=organization.pk)

    # Every prospect receives a campaign participation row. Multiple pathological legacy
    # duplicates in one campaign share the one invariant-preserving enrollment.
    for prospect in Prospect.objects.select_related("organization").order_by("created_at", "pk"):
        if prospect.organization_id is None:
            continue
        selected_email = EmailAddress.objects.filter(
            legacy_prospect_email__prospect_id=prospect.pk,
            legacy_prospect_email__is_primary=True,
        ).first()
        if selected_email is None:
            selected_email = EmailAddress.objects.filter(
                organization_id=prospect.organization_id,
                is_preferred=True,
            ).first()
        ineligible_states = {
            "SKIPPED_NO_EMAIL",
            "SKIPPED_DUPLICATE",
            "SKIPPED_IRRELEVANT",
            "ERROR",
        }
        if prospect.pipeline_state in ineligible_states:
            state = "INELIGIBLE"
            exclusion_reason = prospect.pipeline_state
        elif prospect.pipeline_state == "QUEUED":
            state = "PREPARED"
            exclusion_reason = ""
        elif selected_email is not None:
            state = "ELIGIBLE"
            exclusion_reason = ""
        else:
            state = "DISCOVERED"
            exclusion_reason = ""
        enrollment, _ = CampaignEnrollment.objects.get_or_create(
            workspace_id=workspace.pk,
            campaign_id=prospect.campaign_id,
            organization_id=prospect.organization_id,
            defaults={
                "selected_email_id": selected_email.pk if selected_email is not None else None,
                "state": state,
                "source": "LEGACY_PROSPECT",
                "exclusion_reason": exclusion_reason,
            },
        )
        if enrollment.selected_email_id is None and selected_email is not None:
            enrollment.selected_email_id = selected_email.pk
            enrollment.save(update_fields=("selected_email_id", "updated_at"))
        Prospect.objects.filter(pk=prospect.pk).update(campaign_enrollment_id=enrollment.pk)

    # Link outbound rows before promotion so every inbound can resolve its organization.
    for outbound in OutboundMessage.objects.select_related("prospect").order_by("created_at", "pk"):
        organization_id = outbound.prospect.organization_id
        enrollment_id = outbound.prospect.campaign_enrollment_id
        OutboundMessage.objects.filter(pk=outbound.pk).update(
            organization_id=organization_id,
            campaign_enrollment_id=enrollment_id,
        )

    # A genuine human reply promotes the whole organization to Contact. Automatic replies and
    # bounces remain events and do not independently establish a relationship.
    human_inbound = (
        InboundMessage.objects.exclude(classification__in=("AUTO_REPLY", "BOUNCE"))
        .filter(is_human=True)
        .select_related("related_outbound")
        .order_by("external_at", "pk")
    )
    for inbound in human_inbound.iterator():
        outbound = OutboundMessage.objects.get(pk=inbound.related_outbound_id)
        if outbound.organization_id is None:
            continue
        organization = Organization.objects.get(pk=outbound.organization_id)
        sender_normalized = _normalize_email(inbound.sender)
        sender_email = EmailAddress.objects.filter(
            workspace_id=workspace.pk,
            normalized_email=sender_normalized,
        ).first()
        if sender_email is None and sender_normalized:
            sender_email = _get_or_create_email(
                EmailAddress=EmailAddress,
                workspace=workspace,
                organization=organization,
                original_email=parseaddr(inbound.sender)[1] or sender_normalized,
                normalized_email=sender_normalized,
                provenance="INBOUND_MESSAGE",
                validity="VALID",
                validated_at=inbound.external_at,
                prefer=not EmailAddress.objects.filter(
                    organization_id=organization.pk,
                    is_preferred=True,
                ).exists(),
            )
        if sender_email is not None and sender_email.organization_id != organization.pk:
            sender_email = None
        preferred = (
            sender_email
            or EmailAddress.objects.filter(
                organization_id=organization.pk,
                is_preferred=True,
            ).first()
        )
        status = "UNSUBSCRIBED" if inbound.classification == "UNSUBSCRIBE" else "ACTIVE"
        reason = "UNSUBSCRIBE" if inbound.classification == "UNSUBSCRIBE" else "HUMAN_REPLY"
        contact, _ = Contact.objects.get_or_create(
            organization_id=organization.pk,
            defaults={
                "workspace_id": workspace.pk,
                "preferred_email_id": preferred.pk if preferred is not None else None,
                "status": status,
                "created_reason": reason,
                "source_inbound_message_id": inbound.pk,
                "last_interaction_at": inbound.external_at,
            },
        )
        updates: dict[str, object] = {}
        if contact.preferred_email_id is None and preferred is not None:
            updates["preferred_email_id"] = preferred.pk
        if contact.last_interaction_at is None or inbound.external_at > contact.last_interaction_at:
            updates["last_interaction_at"] = inbound.external_at
        if inbound.classification == "UNSUBSCRIBE" and contact.status != "UNSUBSCRIBED":
            updates.update(status="UNSUBSCRIBED", created_reason="UNSUBSCRIBE")
        if updates:
            Contact.objects.filter(pk=contact.pk).update(**updates)
            for field, value in updates.items():
                setattr(contact, field, value)

    # Preserve the historical suppression evidence while moving the active safety policy into
    # Contactos. Bounce-only addresses are invalidated without creating a Contact.
    for suppression in SuppressionEntry.objects.order_by("created_at", "pk"):
        normalized = suppression.normalized_email or _normalize_email(suppression.original_email)
        if not normalized:
            continue
        organization, email_address = _organization_for_unmatched_email(
            Organization=Organization,
            EmailAddress=EmailAddress,
            workspace=workspace,
            normalized_email=normalized,
            source_id=suppression.pk,
        )
        if email_address is None:
            continue
        contact = Contact.objects.filter(organization_id=organization.pk).first()
        if suppression.reason != "BOUNCE":
            status = "UNSUBSCRIBED" if suppression.reason == "UNSUBSCRIBE" else "DO_NOT_CONTACT"
            reason = "UNSUBSCRIBE" if suppression.reason == "UNSUBSCRIBE" else "MANUAL_RESTRICTION"
            contact, _ = Contact.objects.get_or_create(
                organization_id=organization.pk,
                defaults={
                    "workspace_id": workspace.pk,
                    "preferred_email_id": email_address.pk,
                    "status": status,
                    "created_reason": reason,
                    "created_by_id": suppression.created_by_id,
                },
            )
            if suppression.reason == "UNSUBSCRIBE" and contact.status != "UNSUBSCRIBED":
                Contact.objects.filter(pk=contact.pk).update(
                    status="UNSUBSCRIBED",
                    created_reason="UNSUBSCRIBE",
                )
        CommunicationRestriction.objects.get_or_create(
            email_address_id=email_address.pk,
            kind=suppression.reason,
            revoked_at=None,
            defaults={
                "workspace_id": workspace.pk,
                "scope": "EMAIL",
                "source": suppression.source,
                "evidence": suppression.evidence,
                "created_by_id": suppression.created_by_id,
            },
        )
        if suppression.reason == "BOUNCE":
            EmailAddress.objects.filter(pk=email_address.pk).update(
                validity="INVALID",
                invalid_reason=email_address.invalid_reason or "Rebote registrado anteriormente",
                invalidated_at=email_address.invalidated_at or suppression.created_at,
            )

    # Prospect-level invalidity may predate a SuppressionEntry. Preserve it as an address-only
    # bounce restriction and never promote it to Contact by itself.
    for email_address in EmailAddress.objects.filter(validity="INVALID").order_by(
        "created_at", "pk"
    ):
        CommunicationRestriction.objects.get_or_create(
            email_address_id=email_address.pk,
            kind="BOUNCE",
            revoked_at=None,
            defaults={
                "workspace_id": workspace.pk,
                "scope": "EMAIL",
                "source": "legacy_email_invalidity",
                "evidence": email_address.invalid_reason,
            },
        )

    # Link every inbound row to its campaign participation and, when the organization has become a
    # Contact, place all messages from that Gmail thread on the same Conversation.
    for inbound in InboundMessage.objects.select_related("related_outbound", "connection").order_by(
        "external_at", "pk"
    ):
        outbound = OutboundMessage.objects.get(pk=inbound.related_outbound_id)
        organization_id = outbound.organization_id
        enrollment_id = outbound.campaign_enrollment_id
        contact = (
            Contact.objects.filter(organization_id=organization_id).first()
            if organization_id is not None
            else None
        )
        conversation = None
        if contact is not None and inbound.gmail_thread_id:
            conversation, _ = Conversation.objects.get_or_create(
                connection_id=inbound.connection_id,
                gmail_thread_id=inbound.gmail_thread_id,
                defaults={
                    "workspace_id": workspace.pk,
                    "contact_id": contact.pk,
                    "subject": inbound.subject,
                    "last_message_at": inbound.external_at,
                },
            )
            if (
                conversation.last_message_at is None
                or inbound.external_at > conversation.last_message_at
            ):
                Conversation.objects.filter(pk=conversation.pk).update(
                    last_message_at=inbound.external_at
                )
        InboundMessage.objects.filter(pk=inbound.pk).update(
            organization_id=organization_id,
            campaign_enrollment_id=enrollment_id,
            contact_id=contact.pk if contact is not None else None,
            conversation_id=conversation.pk if conversation is not None else None,
        )
        if conversation is not None:
            OutboundMessage.objects.filter(
                gmail_thread_id=inbound.gmail_thread_id,
                organization_id=organization_id,
            ).update(
                contact_id=contact.pk,
                conversation_id=conversation.pk,
            )
        if (
            enrollment_id is not None
            and inbound.is_human
            and inbound.classification not in {"AUTO_REPLY", "BOUNCE"}
        ):
            CampaignEnrollment.objects.filter(pk=enrollment_id).update(
                state="RESPONDED",
                replied_at=inbound.external_at,
            )

    # A contact created manually may still have an already-sent legacy Gmail thread without an
    # inbound row. Resolve the connection from the campaign creator and preserve that thread too.
    for outbound in (
        OutboundMessage.objects.exclude(gmail_thread_id="")
        .select_related("campaign")
        .order_by("created_at", "pk")
    ):
        if outbound.organization_id is None:
            continue
        contact = Contact.objects.filter(organization_id=outbound.organization_id).first()
        if contact is None:
            continue
        connection = GmailConnection.objects.filter(
            owner_id=outbound.campaign.created_by_id
        ).first()
        if connection is None:
            continue
        message_at = outbound.sent_at or outbound.created_at
        conversation, _ = Conversation.objects.get_or_create(
            connection_id=connection.pk,
            gmail_thread_id=outbound.gmail_thread_id,
            defaults={
                "workspace_id": workspace.pk,
                "contact_id": contact.pk,
                "subject": outbound.subject,
                "last_message_at": message_at,
            },
        )
        if conversation.last_message_at is None or message_at > conversation.last_message_at:
            Conversation.objects.filter(pk=conversation.pk).update(last_message_at=message_at)
        OutboundMessage.objects.filter(pk=outbound.pk).update(
            contact_id=contact.pk,
            conversation_id=conversation.pk,
        )
        InboundMessage.objects.filter(
            connection_id=connection.pk,
            gmail_thread_id=outbound.gmail_thread_id,
        ).update(
            organization_id=outbound.organization_id,
            campaign_enrollment_id=outbound.campaign_enrollment_id,
            contact_id=contact.pk,
            conversation_id=conversation.pk,
        )

    # Project confirmed legacy sends after reply promotion, without touching any message identity.
    for enrollment in CampaignEnrollment.objects.order_by("created_at", "pk"):
        sent = (
            OutboundMessage.objects.filter(
                campaign_enrollment_id=enrollment.pk,
                kind="FIRST_CONTACT",
                state="SENT",
            )
            .order_by("sent_at", "created_at")
            .first()
        )
        if sent is None:
            continue
        updates = {"initial_sent_at": sent.sent_at or sent.created_at}
        if enrollment.state != "RESPONDED":
            updates["state"] = "INITIAL_SENT"
        CampaignEnrollment.objects.filter(pk=enrollment.pk).update(**updates)

    # Recalculate timeline dates from persisted rows. This remains deterministic and idempotent.
    for conversation in Conversation.objects.all():
        latest_inbound = InboundMessage.objects.filter(conversation_id=conversation.pk).aggregate(
            value=Max("external_at")
        )["value"]
        latest_outbound = OutboundMessage.objects.filter(conversation_id=conversation.pk).aggregate(
            value=Max("sent_at")
        )["value"]
        latest = max(
            (value for value in (latest_inbound, latest_outbound) if value is not None),
            default=conversation.last_message_at,
        )
        if latest != conversation.last_message_at:
            Conversation.objects.filter(pk=conversation.pk).update(last_message_at=latest)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("campaigns", "0012_outboundmessage_campaign_enrollment_and_more"),
        ("compliance", "0002_contactledger_contactoverride"),
        ("contacts", "0001_initial"),
        ("mailbox", "0003_inboundmessage_campaign_enrollment_and_more"),
        ("prospects", "0005_prospect_campaign_enrollment_prospect_organization"),
    ]

    operations = [
        migrations.RunPython(backfill_contact_foundation, migrations.RunPython.noop),
    ]
